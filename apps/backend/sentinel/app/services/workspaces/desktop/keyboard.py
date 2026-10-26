"""Resolve the local Mac's keyboard layouts to a standard Linux XKB configuration.

The viewer sends physical keys, so the desktop must use the layouts the user types with on the Mac:
every enabled keyboard layout, the current one first (the desktop's own switcher, or Super+Space,
changes between them). Layouts without an XKB equivalent are left out; with none, the guest keeps
its configuration. Host IME text composition is not transported by this physical-key path.
"""

import asyncio
import logging
import plistlib
import subprocess
import sys

logger = logging.getLogger(__name__)

PREFIX = "com.apple.keylayout."
# macOS keyboard layout ids -> xkeyboard-config layout and variant (its Mac variants where they
# exist). Do not infer a layout from language/locale: these are independent settings.
SOURCE_LAYOUTS = {
    "ABC": ("us", "mac"),
    "US": ("us", "mac"),
    "USExtended": ("us", "mac"),
    "USInternational-PC": ("us", "intl"),
    "Dvorak": ("us", "dvorak"),
    "Colemak": ("us", "colemak"),
    "British": ("gb", "mac"),
    "British-PC": ("gb", ""),
    "Irish": ("ie", ""),
    "Canadian": ("us", "mac"),
    "Canadian-CSA": ("ca", ""),
    "French": ("fr", "mac"),
    "French-PC": ("fr", ""),
    "French-numerical": ("fr", "mac"),
    "Belgian": ("be", ""),
    "SwissFrench": ("ch", "fr_mac"),
    "SwissGerman": ("ch", "de_mac"),
    "German": ("de", "mac"),
    "Austrian": ("at", "mac"),
    "Spanish": ("es", "mac"),
    "Spanish-ISO": ("es", "mac"),
    "Italian": ("it", "mac"),
    "Italian-Pro": ("it", "mac"),
    "Portuguese": ("pt", "mac"),
    "Brazilian": ("br", ""),
    "Brazilian-ABNT2": ("br", ""),
    "Brazilian-Pro": ("br", ""),
    "Dutch": ("nl", "mac"),
    "Danish": ("dk", "mac"),
    "Norwegian": ("no", "mac"),
    "Swedish": ("se", "mac"),
    "Swedish-Pro": ("se", "mac"),
    "Finnish": ("fi", "mac"),
    "Icelandic": ("is", "mac_legacy"),
    "Polish": ("pl", ""),
    "PolishPro": ("pl", ""),
    "Czech": ("cz", ""),
    "Czech-QWERTY": ("cz", "qwerty"),
    "Slovak": ("sk", ""),
    "Hungarian": ("hu", ""),
    "Romanian": ("ro", ""),
    "Croatian": ("hr", ""),
    "Slovenian": ("si", ""),
    "Turkish": ("tr", "f"),
    "Turkish-QWERTY": ("tr", ""),
    "Turkish-QWERTY-PC": ("tr", ""),
    "Greek": ("gr", ""),
    "Russian": ("ru", "mac"),
    "RussianWin": ("ru", ""),
    "Ukrainian": ("ua", ""),
    "Ukrainian-PC": ("ua", ""),
    "Hebrew": ("il", ""),
    "Arabic": ("ara", "mac"),
}
MAX_LAYOUTS = 4  # XKB groups


def source_id(entry: dict) -> str | None:
    # Enabled layouts are stored by name ("U.S.", "Swiss French", "French - PC"); their source ids
    # drop the dots and spaces ("US", "SwissFrench", "French-PC").
    name = entry.get("KeyboardLayout Name")
    if entry.get("InputSourceKind") != "Keyboard Layout" or not isinstance(name, str):
        return None
    return PREFIX + name.replace(" - ", "-").replace(" ", "").replace(".", "")


def keyboard_from_sources(current: str | None, enabled: list) -> dict[str, str] | None:
    sources = [current] if current else []
    sources += [s for s in (source_id(e) for e in enabled if isinstance(e, dict)) if s]
    layouts: list[tuple[str, str]] = []
    for source in sources:
        match = SOURCE_LAYOUTS.get(source[len(PREFIX) :]) if source.startswith(PREFIX) else None
        if match and match not in layouts:
            layouts.append(match)
    if not layouts:
        logger.info("Host keyboard layouts have no XKB mapping; keeping guest keyboard settings")
        return None
    layouts = layouts[:MAX_LAYOUTS]
    return {
        "layout": ",".join(layout for layout, _ in layouts),
        "variant": ",".join(variant for _, variant in layouts),
        "model": "pc105",
        "options": "grp:win_space_toggle" if len(layouts) > 1 else "",
    }


def _read_keyboard() -> dict[str, str] | None:
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "export", "com.apple.HIToolbox", "-"],
            capture_output=True,
            timeout=2,
            check=True,
        )
        settings = plistlib.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
        logger.warning("Could not read host keyboard layouts; keeping guest keyboard settings")
        return None
    return keyboard_from_sources(
        settings.get("AppleCurrentKeyboardLayoutInputSourceID"),
        settings.get("AppleEnabledInputSources") or [],
    )


async def host_keyboard() -> dict[str, str] | None:
    if sys.platform != "darwin":
        return None
    return await asyncio.to_thread(_read_keyboard)
