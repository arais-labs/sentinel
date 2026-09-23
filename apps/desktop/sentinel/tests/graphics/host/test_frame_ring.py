"""Pixel-check the packaged desktop GL → Metal presentation path, without a VM.

Prepare the native graphics package first. SENTINEL_TEST_GRAPHICS_DIR may select
another freshly built package; no graphics capability overrides are used.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[3]


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS Metal")
class FrameRingTests(unittest.TestCase):
    def test_shared_context_pixels_resize_and_backpressure(self):
        package = Path(
            os.environ.get(
                "SENTINEL_TEST_GRAPHICS_DIR",
                DESKTOP / "build/macos-arm64/runtime/workspace-runtime/graphics",
            )
        ).resolve()
        work = DESKTOP / "build/graphics-sources"
        native = DESKTOP / "native/graphics"
        upstream = work / "remote-virtio-gpu"
        self.assertTrue(
            (package / "libvulkan_kosmickrisp.dylib").is_file(),
            "Prepare the desktop graphics package before ring tests",
        )
        includes = [
            "-I" + str(path)
            for path in (
                native / "renderer",
                native / "renderer/include",
                native / "transport",
                native / "video",
                upstream / "include",
                upstream / "src",
                work / "local/include",
                work / "virgl/src",
                work / "virgl-build/src",
                work / "egl-registry/api",
                work / "mesa-host/include",
                work / "mesa-host/src/gallium/drivers/zink",
                work / "mesa-host/src/kosmickrisp/vulkan",
            )
        ]
        with tempfile.TemporaryDirectory(prefix="sentinel-frame-ring-") as temporary:
            directory = Path(temporary)
            # Exercise production's adjacent-library lookup without modifying the
            # package, or giving the production renderer a test-only lookup path.
            for source in package.iterdir():
                if source.suffix == ".dylib" or source.name == "icd.json":
                    (directory / source.name).symlink_to(source)
            objects = []
            for source in (
                Path(__file__).with_name("renderer-idle-wake.c"),
                upstream / "src/rvgpu-sanity/rvgpu-sanity.c",
                upstream / "src/rvgpu-utils/rvgpu-utils.c",
                native / "renderer/GPUConnection.c",
                native / "transport/gpu_transport.c",
            ):
                target = directory / (source.stem + ".o")
                subprocess.run(
                    [
                        "clang",
                        "-O2",
                        "-std=gnu11",
                        *includes,
                        "-c",
                        str(source),
                        "-o",
                        str(target),
                    ],
                    check=True,
                    timeout=60,
                )
                objects.append(str(target))
            executable = directory / "frame-ring"
            frameworks = (
                "Foundation",
                "QuartzCore",
                "Metal",
                "VideoToolbox",
                "CoreMedia",
                "CoreVideo",
                "CoreGraphics",
                "IOSurface",
                "ImageIO",
            )
            subprocess.run(
                [
                    "clang",
                    "-O2",
                    "-fobjc-arc",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    *includes,
                    str(Path(__file__).with_name("frame-ring.m")),
                    *(
                        str(native / "video" / name)
                        for name in (
                            "DesktopVideoEncoder.m",
                            "DesktopDisplayServer.m",
                            "MetalFrameCopy.m",
                        )
                    ),
                    *objects,
                    str(directory / "libvirglrenderer.1.dylib"),
                    str(directory / "libepoxy.0.dylib"),
                    "-Wl,-rpath,@executable_path",
                    *(flag for name in frameworks for flag in ("-framework", name)),
                    "-o",
                    str(executable),
                ],
                check=True,
                timeout=60,
            )
            cursor_result = subprocess.run(
                [str(executable), "cursor-resources"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                cursor_result.returncode, 0, cursor_result.stdout + cursor_result.stderr
            )
            for validation in ("0", "1"):
                environment = dict(
                    os.environ,
                    MTL_DEBUG_LAYER=validation,
                    MTL_SHADER_VALIDATION=validation,
                )
                for variant in (
                    ("rgba8", "1", "0"),
                    ("rgb8", "1", "0"),
                    ("srgb8", "1", "0"),
                    ("rgba8", "2", "1"),
                ):
                    with self.subTest(
                        metal_validation=validation,
                        format=variant[0],
                        scale=variant[1],
                        mip=variant[2],
                    ):
                        result = subprocess.run(
                            [str(executable), *variant],
                            capture_output=True,
                            text=True,
                            timeout=65,
                            env=environment,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("PASS ring phase 3", result.stdout)
                        self.assertIn("resize and orderly shutdown", result.stdout)
                        self.assertIn("PASS idle latest-frame retry", result.stdout)
                        self.assertIn("PASS guest cursor:", result.stdout)
                        self.assertIn(
                            "PASS hardware H264 configuration and first keyframe",
                            result.stdout,
                        )


if __name__ == "__main__":
    unittest.main()
