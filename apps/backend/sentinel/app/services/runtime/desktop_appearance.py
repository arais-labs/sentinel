"""First-run desktop defaults. User preferences take precedence after setup."""

from xml.etree import ElementTree as ET
from app.services.runtime.guest_commands import load_guest_command

WALLPAPER = ".local/share/backgrounds/sentinel/default.svg"


def _channel(name: str, properties: dict) -> str:
    root = ET.Element("channel", name=name, version="1.0")
    for path, value in properties.items():
        parent = root
        parts = path.split("/")
        for part in parts[:-1]:
            child = parent.find(f"property[@name='{part}']")
            if child is None:
                child = ET.SubElement(parent, "property", name=part, type="empty")
            parent = child
        kind = "bool" if isinstance(value, bool) else "int" if isinstance(value, int) else "string"
        node = ET.SubElement(
            parent,
            "property",
            name=parts[-1],
            type="array" if isinstance(value, list) else kind,
        )
        if isinstance(value, list):
            for item in value:
                item_type = (
                    "int"
                    if isinstance(item, int)
                    else "double" if isinstance(item, float) else "string"
                )
                ET.SubElement(node, "value", type=item_type, value=str(item))
        else:
            node.set("value", str(value).lower() if isinstance(value, bool) else str(value))
    return ET.tostring(root, encoding="unicode") + "\n"


def desktop_default_files() -> dict[str, str]:
    """Paths are relative to the guest HOME, never the project mount."""
    channels = {
        "xsettings": {
            "Net/ThemeName": "Greybird",
            "Net/IconThemeName": "Papirus-Dark",
            "Gtk/FontName": "Noto Sans 10",
            "Gtk/MonospaceFontName": "DejaVu Sans Mono 10",
            "Gtk/CursorThemeName": "Adwaita",
            "Gtk/CursorThemeSize": 24,
            "Gtk/DecorationLayout": "menu:minimize,maximize,close",
            "Xft/Antialias": 1,
            "Xft/Hinting": 1,
            "Xft/HintStyle": "hintslight",
        },
        "xfwm4": {
            "general/theme": "Numix",
            "general/title_font": "Noto Sans Bold 10",
            "general/title_alignment": "center",
            "general/button_layout": "O|HMC",
            "general/workspace_count": 1,
            "general/use_compositing": True,
            "general/click_to_focus": True,
            "general/box_move": False,
        },
        "xfce4-panel": {
            "configver": 2,
            "panels": [1],
            "panels/dark-mode": True,
            "panels/panel-1/position": "p=6;x=0;y=0",
            "panels/panel-1/position-locked": True,
            "panels/panel-1/size": 40,
            "panels/panel-1/length": 100,
            "panels/panel-1/icon-size": 22,
            "panels/panel-1/plugin-ids": [1, 2, 3, 4, 5, 6, 7, 8],
            "plugins/plugin-1": "whiskermenu",
            "plugins/plugin-2": "launcher",
            "plugins/plugin-2/items": ["sentinel-browser.desktop"],
            "plugins/plugin-3": "launcher",
            "plugins/plugin-3/items": ["thunar.desktop"],
            "plugins/plugin-4": "launcher",
            "plugins/plugin-4/items": ["xfce4-terminal.desktop"],
            "plugins/plugin-5": "tasklist",
            "plugins/plugin-5/show-labels": True,
            "plugins/plugin-5/flat-buttons": True,
            "plugins/plugin-5/grouping": 1,
            "plugins/plugin-6": "separator",
            "plugins/plugin-6/expand": True,
            "plugins/plugin-6/style": 0,
            "plugins/plugin-7": "systray",
            "plugins/plugin-8": "clock",
            "plugins/plugin-8/digital-format": "%a %d %b  %H:%M",
        },
        "xfce4-session": {"general/SaveOnExit": False},
        "xfce4-desktop": {
            "desktop-icons/style": 0,
            "backdrop/screen0/monitor__MONITOR__/workspace0/image-style": 5,
            "backdrop/screen0/monitor__MONITOR__/workspace0/last-image": f"__HOME__/{WALLPAPER}",
            "backdrop/screen0/monitor__MONITOR__/workspace0/color-style": 0,
            "backdrop/screen0/monitor__MONITOR__/workspace0/rgba1": [
                0.055,
                0.067,
                0.086,
                1.0,
            ],
        },
    }
    files = {
        f".config/xfce4/xfconf/xfce-perchannel-xml/{name}.xml": _channel(name, values)
        for name, values in channels.items()
    }
    files[WALLPAPER] = load_guest_command("linux/desktop/wallpaper.svg")
    # A desktop-file ID resolves through XDG applications for the menu, whereas
    # an XFCE panel launcher keeps its own desktop file in launcher-<plugin-id>.
    # Only the panel needs a private entry. The application menu uses the
    # authoritative system entry installed by workspace browser setup.
    browser_launcher = """[Desktop Entry]
Type=Application
Name=Web Browser
Comment=Open your preferred web browser
Exec=sentinel-browser %U
Icon=web-browser
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
"""
    files[".config/xfce4/panel/launcher-2/sentinel-browser.desktop"] = browser_launcher
    files[".config/gtk-3.0/settings.ini"] = """[Settings]
gtk-theme-name=Greybird
gtk-icon-theme-name=Papirus-Dark
gtk-font-name=Noto Sans 10
gtk-application-prefer-dark-theme=true
"""
    files[".config/xfce4/panel/whiskermenu-1.rc"] = """button-title=Apps
button-icon=view-app-grid-symbolic
show-button-title=true
show-button-icon=true
show-command-logout=false
show-command-lockscreen=false
show-command-switchuser=false
show-command-suspend=false
show-command-hibernate=false
show-command-shutdown=false
show-command-restart=false
favorites=sentinel-browser.desktop,thunar.desktop,xfce4-terminal.desktop,xfce-settings-manager.desktop
"""
    files[".config/xfce4/terminal/terminalrc"] = """[Configuration]
FontName=DejaVu Sans Mono 10
ColorForeground=#d8dee9
ColorBackground=#101318
ColorCursor=#88c0d0
ColorUseTheme=FALSE
MiscMenubarDefault=FALSE
MiscToolbarDefault=FALSE
MiscAlwaysShowTabs=FALSE
ScrollingBar=TERMINAL_SCROLLBAR_NONE
ScrollingLines=10000
"""
    return files
