"""Read-only compatibility gate for the native Linux desktop lifecycle."""

NATIVE_DESKTOPS = frozenset({"xfce", "lxqt", "gnome", "plasma"})
REINSTALL_REQUIRED = (
    "This workspace desktop predates native Linux sessions. Reinstall this "
    "workspace explicitly to use the current desktop runtime. Reinstall keeps "
    "the mounted host project but erases VM-only files, packages and settings."
)


def require_session_contract(config, selection):
    if not isinstance(config, dict):
        raise TypeError("desktop.json must contain a session configuration object")
    if selection in NATIVE_DESKTOPS:
        if config.get("session_manager") != "native":
            raise RuntimeError(REINSTALL_REQUIRED)
        if config.get("clipboard_backend") not in {"x11", "wayland"}:
            raise ValueError(
                "Native desktop.json requires an explicit x11/wayland clipboard_backend"
            )
    elif selection == "weston":
        # Weston is an explicit internal qualification profile, never an
        # implicit fallback for a normal desktop or unknown saved selection.
        if config.get("protocol") != "wayland":
            raise ValueError("The internal Weston profile requires Wayland")
    else:
        raise RuntimeError(REINSTALL_REQUIRED)
    return config
