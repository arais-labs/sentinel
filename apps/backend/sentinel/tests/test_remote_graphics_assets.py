"""The remote release must carry the exact canonical host graphics contract."""

import ast
from pathlib import Path

from app.services.runtime.remote_mac import runtime_assets
from tests.workspace_image_assets import image_names


def assignment(path, name):
    tree = ast.parse(path.read_text())
    return next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )


def test_remote_graphics_libraries_match_canonical_packaging_contract():
    desktop = Path(__file__).resolve().parents[3] / "desktop/sentinel"
    packaging = desktop / "scripts/packaging/graphics"
    libraries = set(ast.literal_eval(assignment(packaging / "host.py", "LIBRARIES")))
    required = assignment(packaging / "build.py", "required")
    required_names = {
        node.value
        for node in required.elts
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    } | libraries
    files = runtime_assets(
        {
            "executable": "/release/sentinel-workspace-runtime",
            "kernel": "/release/kernel",
            "workspaceImageFiles": image_names(),
        },
        installed=True,
    )
    graphics = {name.removeprefix("graphics/") for _, name in files if name.startswith("graphics/")}
    assert {name for name in graphics if name.endswith(".dylib")} == libraries
    assert required_names <= graphics
    assert {"install-browser-graphics.py", "gpu-2404.snap", "gpu-2404.json"} <= graphics
    assert {"gpu-bridge.py", "libGLESv2.dylib"}.isdisjoint(graphics)
    assert {"licenses/vulkan-loader-LICENSE.txt", "licenses/spirv-tools-LICENSE"} <= graphics
