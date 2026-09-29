"""Validate the finite monitor contract and print its deterministic C header."""

import argparse
import json
from pathlib import Path


def generate(path, language="c"):
    contract = json.loads(Path(path).read_text())
    if not isinstance(contract, dict) or set(contract) != {
        "schema",
        "refresh_hz",
        "default_geometry",
        "modes",
    }:
        raise ValueError("Invalid display contract fields")
    if type(contract["schema"]) is not int or contract["schema"] != 1:
        raise ValueError("Unsupported display contract schema")
    rate = contract["refresh_hz"]
    if type(rate) is not int or not 1 <= rate <= 120:
        raise ValueError("Invalid display refresh")
    modes = contract["modes"]
    if not isinstance(modes, list) or not modes:
        raise ValueError("Display modes must be a nonempty list")
    seen = set()
    for mode in modes:
        if not isinstance(mode, list) or len(mode) != 2 or any(type(n) is not int for n in mode):
            raise ValueError("Invalid display dimensions")
        width, height = mode
        # CVT rounds horizontal pixels to cells of eight. Reject contract modes
        # that would silently advertise a different geometry.
        if not (
            320 <= width <= 4096
            and 240 <= height <= 4096
            and width * height <= 16_000_000
            and width % 8 == 0
            and height % 2 == 0
        ):
            raise ValueError("Unsupported display dimensions")
        if tuple(mode) in seen:
            raise ValueError("Duplicate display dimensions")
        seen.add(tuple(mode))
    if not isinstance(contract["default_geometry"], str) or contract["default_geometry"] not in {
        f"{w}x{h}" for w, h in modes
    }:
        raise ValueError("Default geometry must be advertised")
    notice = "Generated from native/graphics/display/modes.json; do not edit."
    geometries = [f"{w}x{h}" for w, h in modes]
    default = contract["default_geometry"]
    if language == "python":
        return "\n".join(
            [
                "# " + notice,
                "DESKTOP_RESOLUTIONS = (",
                *(f"    {json.dumps(geometry)}," for geometry in geometries),
                ")",
                f"DEFAULT_DESKTOP_GEOMETRY = {json.dumps(default)}",
                f"DESKTOP_REFRESH_HZ = {rate}",
                "",
            ]
        )
    if language == "typescript":
        return "\n".join(
            [
                "// " + notice,
                "export const desktopResolutions = [",
                *(f"  {geometry!r}," for geometry in geometries),
                "] as const;",
                f"export const defaultDesktopGeometry = {default!r};",
                f"export const desktopRefreshHz = {rate};",
                "",
            ]
        )
    if language != "c":
        raise ValueError("Unsupported display contract output language")
    lines = [
        "/* " + notice + " */",
        "#ifndef SENTINEL_DISPLAY_MODES_H",
        "#define SENTINEL_DISPLAY_MODES_H",
        f"#define SENTINEL_DISPLAY_REFRESH_HZ {rate}",
        "static const struct { unsigned int width, height; } sentinel_display_modes[] = {",
        *(f"\t{{ {w}, {h} }}," for w, h in modes),
        "};",
        "#endif",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--language", choices=("c", "python", "typescript"), default="c")
    args = parser.parse_args()
    print(generate(args.contract, args.language), end="")
