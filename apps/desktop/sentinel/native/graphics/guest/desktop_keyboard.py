"""Standard XKB session configuration, shared by Xorg and Wayland desktops."""

import ctypes
import re

FIELDS = ("rules", "model", "layout", "variant", "options")


def resolve_keyboard(config, request):
    # Explicit null opts out of host matching. An explicit guest layout wins.
    value = config.get("keyboard", request.get("keyboard"))
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError("keyboard must contain XKB layout, variant, model, rules or options")
    if not value.get("layout"):
        raise ValueError("keyboard.layout is required")
    for item in value.values():
        if (
            not isinstance(item, str)
            or len(item) > 256
            or not re.fullmatch(r"[A-Za-z0-9_:+,.-]*", item)
        ):
            raise ValueError("Invalid XKB keyboard configuration")
    return {"rules": "evdev", "model": "pc105", "variant": "", "options": "", **value}


def validate_keyboard(keyboard):
    """Compile and return logical-key chords for computer use, once per session."""
    if keyboard is None:
        return

    class Names(ctypes.Structure):
        _fields_ = [(field, ctypes.c_char_p) for field in FIELDS]

    xkb = ctypes.CDLL("libxkbcommon.so.0")
    xkb.xkb_context_new.argtypes = [ctypes.c_int]
    xkb.xkb_context_new.restype = ctypes.c_void_p
    xkb.xkb_context_unref.argtypes = [ctypes.c_void_p]
    xkb.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(Names), ctypes.c_int]
    xkb.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    xkb.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
    context = xkb.xkb_context_new(0)
    if not context:
        raise RuntimeError("Could not initialize the Linux keyboard configuration")
    try:
        names = Names(*(keyboard[field].encode() for field in FIELDS))
        keymap = xkb.xkb_keymap_new_from_names(context, ctypes.byref(names), 0)
        if not keymap:
            raise ValueError("The selected keyboard layout is not installed in this workspace")
        try:
            return key_chords(xkb, keymap)
        finally:
            xkb.xkb_keymap_unref(keymap)
    finally:
        xkb.xkb_context_unref(context)


def key_chords(xkb, keymap):
    xkb.xkb_state_new.argtypes = [ctypes.c_void_p]
    xkb.xkb_state_new.restype = ctypes.c_void_p
    xkb.xkb_state_unref.argtypes = [ctypes.c_void_p]
    xkb.xkb_state_update_mask.argtypes = [ctypes.c_void_p, *([ctypes.c_uint32] * 6)]
    xkb.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    xkb.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
    xkb.xkb_keymap_mod_get_index.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    xkb.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
    xkb.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    xkb.xkb_keysym_to_utf32.argtypes = [ctypes.c_uint32]
    xkb.xkb_keysym_to_utf32.restype = ctypes.c_uint32
    state = xkb.xkb_state_new(keymap)
    if not state:
        raise RuntimeError("Could not initialize keyboard state")
    chords = {}
    try:
        shift = xkb.xkb_keymap_mod_get_index(keymap, b"Shift")
        level3 = xkb.xkb_keymap_mod_get_index(keymap, b"Mod5")
        # Prefer unmodified keys; modifiers are ordinary Linux evdev keys.
        for modifiers in ([], [(shift, 42)], [(level3, 100)], [(shift, 42), (level3, 100)]):
            if any(index >= 32 for index, _ in modifiers):
                continue
            mask = sum(1 << index for index, _ in modifiers)
            xkb.xkb_state_update_mask(state, mask, 0, 0, 0, 0, 0)
            for code in range(1, 256):
                symbol = xkb.xkb_state_key_get_one_sym(state, code + 8)
                if not symbol:
                    continue
                chord = [modifier for _, modifier in modifiers] + [code]
                name = ctypes.create_string_buffer(128)
                if xkb.xkb_keysym_get_name(symbol, name, len(name)) > 0:
                    chords.setdefault(name.value.decode(), chord)
                character = xkb.xkb_keysym_to_utf32(symbol)
                if character >= 32 and character != 127:
                    chords.setdefault(chr(character), chord)
    finally:
        xkb.xkb_state_unref(state)
    return chords


def xorg_keyboard(keyboard):
    if keyboard is None:
        return ""
    options = "".join(
        f'    Option "Xkb{field.title()}" "{value}"\n' for field, value in keyboard.items()
    )
    return (
        'Section "InputClass"\n    Identifier "Sentinel keyboard"\n'
        '    MatchIsKeyboard "on"\n' + options + "EndSection\n"
    )


def weston_keyboard(saved, keyboard):
    if keyboard is None:
        return saved
    section = re.search(r"^\[keyboard\][ \t]*\r?$", saved, re.MULTILINE)
    if section:
        following = re.search(r"^\[", saved[section.end() :], re.MULTILINE)
        end = section.end() + following.start() if following else len(saved)
        # Native compositor configuration is an explicit workspace override.
        if re.search(r"^\s*keymap_\w+\s*=", saved[section.end() : end], re.MULTILINE):
            return saved
    else:
        saved = saved.rstrip() + "\n\n[keyboard]\n"
        end = len(saved)
    settings = "".join(f"keymap_{field}={value}\n" for field, value in keyboard.items())
    return saved[:end].rstrip() + "\n" + settings + saved[end:]


def weston_effective_keyboard(saved):
    section = re.search(r"^\[keyboard\][ \t]*\r?$", saved, re.MULTILINE)
    if not section:
        return None
    body = saved[section.end() :]
    following = re.search(r"^\[", body, re.MULTILINE)
    if following:
        body = body[: following.start()]
    values = dict(
        re.findall(
            r"^\s*keymap_(rules|model|layout|variant|options)\s*=([^\r\n]*)", body, re.MULTILINE
        )
    )
    if not values:
        return None
    # Weston defaults to this layout when a native override omits layout.
    return resolve_keyboard(
        {"keyboard": {"layout": "us", **{key: value.strip() for key, value in values.items()}}}, {}
    )
