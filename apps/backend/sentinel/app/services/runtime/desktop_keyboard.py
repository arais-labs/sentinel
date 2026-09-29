"""Resolve the local Mac's input source to a standard Linux XKB layout.

This runs only when explicitly starting a desktop, never on a timer or on
reconnect. Unknown keyboard layouts retain guest configuration. Host IME text
composition is not transported by this physical-key path.
The guest's desktop.json keyboard setting always overrides this default.
"""

import asyncio
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# macOS input-source identifiers and xkeyboard-config's maintained Mac variants.
# Do not infer a layout from language/locale: these are independent settings.
SOURCE_LAYOUTS = {
    "com.apple.keylayout.ABC": ("us", "mac"),
    "com.apple.keylayout.US": ("us", "mac"),
    "com.apple.keylayout.French": ("fr", "mac"),
    "com.apple.keylayout.British": ("gb", "mac"),
    "com.apple.keylayout.German": ("de", "mac"),
    "com.apple.keylayout.Italian": ("it", "mac"),
}


def _read_keyboard() -> dict[str, str] | None:
    try:
        result = subprocess.run(
            [
                "/usr/bin/defaults",
                "read",
                "com.apple.HIToolbox",
                "AppleCurrentKeyboardLayoutInputSourceID",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not read host keyboard layout; keeping guest keyboard settings")
        return None
    source = result.stdout.strip()
    match = SOURCE_LAYOUTS.get(source)
    if match is None:
        logger.info("Host input source has no XKB mapping; keeping guest keyboard settings")
        return None
    layout, variant = match
    return {"layout": layout, "variant": variant, "model": "apple", "options": ""}


async def host_keyboard() -> dict[str, str] | None:
    if sys.platform != "darwin":
        return None
    return await asyncio.to_thread(_read_keyboard)
