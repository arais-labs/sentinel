"""Executed by Blender's GUI Python; paired with app-parity.py."""

import json
import math
import os
import traceback
import urllib.request
from pathlib import Path

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

root = Path(os.environ["SENTINEL_APP_OUTPUT"])
endpoint = os.environ["SENTINEL_APP_ENDPOINT"]
token = os.environ["SENTINEL_APP_TOKEN"]
phase = 0
frame = 0
result = {}
marker_shader = None
marker_batch = None


def report(value):
    data = json.dumps({"token": token, **value}).encode()
    request = urllib.request.Request(endpoint, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def initialize():
    global result, marker_shader, marker_batch
    result = {
        "app": "blender",
        "version": bpy.app.version_string,
        "backend": gpu.platform.backend_type_get(),
        "renderer": gpu.platform.renderer_get(),
        "gl_version": gpu.platform.version_get(),
    }
    assert result["backend"] == "OPENGL", result
    assert "virgl" in result["renderer"].lower(), result
    target = gpu.types.GPUOffScreen(64, 64)
    try:
        with target.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0, 0, 0, 1))
            gpu.state.viewport_set(0, 0, 64, 64)
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            triangle = batch_for_shader(shader, "TRIS", {"pos": [(-1, -1), (1, -1), (0, 1)]})
            shader.bind()
            shader.uniform_float("color", (1, 0, 0, 1))
            triangle.draw(shader)
            pixels = framebuffer.read_color(0, 0, 64, 64, 4, 0, "UBYTE")
            pixels.dimensions = 64 * 64 * 4
            checked = 0
            for y in range(64):
                for x in range(64):
                    distance = abs(x + 0.5 - 32)
                    edge = 32 - (y + 0.5) / 2
                    if abs(distance - edge) < 1:
                        continue
                    expected = [255 if distance < edge else 0, 0, 0, 255]
                    assert list(pixels[(y * 64 + x) * 4 : (y * 64 + x) * 4 + 4]) == expected
                    checked += 1
            result["exact_gpu_pixels"] = checked
    finally:
        target.free()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = 320, 240
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    cube = bpy.data.objects["Cube"]
    for index, rotation in enumerate((0, 40)):
        cube.rotation_euler.z = math.radians(rotation)
        scene.render.filepath = str(root / f"workbench-{index}.png")
        bpy.ops.render.render(write_still=True)
    cube.rotation_euler.z = 0
    bpy.context.preferences.view.show_splash = False
    marker_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    marker_batch = batch_for_shader(
        marker_shader, "TRIS", {"pos": [(16, 16), (80, 16), (80, 80), (16, 16), (80, 80), (16, 80)]}
    )


def marker():
    global frame
    marker_shader.bind()
    marker_shader.uniform_float("color", (1, 0, 0, 1) if phase == 0 else (0, 1, 0, 1))
    marker_batch.draw(marker_shader)
    frame += 1


def tick():
    global phase
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if not region or region.width < 100 or not frame:
                    area.tag_redraw()
                    continue
                with bpy.context.temp_override(window=window, area=area, region=region):
                    selected = bpy.ops.view3d.select(
                        location=(region.width // 2, region.height // 2)
                    )
                assert "FINISHED" in selected, selected
                command = report(
                    {
                        **result,
                        "phase": phase,
                        "frame": frame,
                        "window": [window.width, window.height],
                        "region": [region.x, region.y, region.width, region.height],
                    }
                )
                if command.get("done"):
                    bpy.ops.wm.quit_blender()
                    return None
                if command.get("advance") and phase == 0:
                    phase = 1
                    bpy.data.objects["Cube"].rotation_euler.z = math.radians(40)
                area.tag_redraw()
                return 0.1
        return 0.1
    except Exception:
        report({"error": traceback.format_exc()})
        bpy.ops.wm.quit_blender()
        return None


try:
    initialize()
    bpy.types.SpaceView3D.draw_handler_add(marker, (), "WINDOW", "POST_PIXEL")
    bpy.app.timers.register(tick)
except Exception:
    report({"error": traceback.format_exc()})
    bpy.ops.wm.quit_blender()
