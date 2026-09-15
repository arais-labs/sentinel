"""Exercise the Linux artifact handoff without Docker, a VM, or network access."""

import io
import json
import os
from pathlib import Path
import runpy
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class GuestGraphicsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.desktop = Path(self.temp.name)
        source = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics"
        self.script = self.desktop / "scripts/packaging/graphics/build-guest.py"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(source / "build-guest.py", self.script)
        (self.script.parent / "build-guest.sh").write_text("set -eu\necho build\n")
        (self.desktop / "runtime.lock.json").write_text(
            json.dumps(
                {
                    "platforms": {
                        "macos-arm64": {
                            "workspaceRuntime": {
                                "workspaceImage": "example/alpine@sha256:abc",
                            }
                        }
                    }
                }
            )
        )
        distribution = (
            self.desktop / "native/macos/Sources/WorkspaceRuntime/WorkspaceDistribution.swift"
        )
        distribution.parent.mkdir(parents=True)
        distribution.write_text('case .ubuntu: return "example/ubuntu@sha256:def"')
        self.artifacts = self.desktop / "artifacts"
        self.calls = []

    def docker(self, args, **kwargs):
        self.assertEqual(args[:3], ["docker", "run", "--rm"])
        self.assertEqual(args[args.index("--platform") + 1], "linux/arm64")
        output = Path(args[args.index("--volume") + 1].removesuffix(":/output"))
        with tarfile.open(output / "graphics.tar.xz", "w:xz") as archive:
            data = b"fixture-graphics"
            info = tarfile.TarInfo("./version")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        self.calls.append((args, kwargs["input"]))

    def execute(self, libc="musl", *, prebuilt=None, system="linux", runner=None):
        env = {"CI": "true"}
        if prebuilt is not None:
            env["SENTINEL_GUEST_GRAPHICS_DIR"] = str(prebuilt)
        destination = self.artifacts if prebuilt is None else self.desktop / "packaged"
        with (
            patch.dict(os.environ, env, clear=True),
            patch("sys.argv", [str(self.script), str(destination), libc]),
            patch("sys.platform", system),
            patch("platform.machine", return_value="aarch64"),
            patch("subprocess.run", side_effect=runner or self.docker),
        ):
            try:
                runpy.run_path(str(self.script), run_name="__main__")
            except SystemExit as error:
                self.assertEqual(error.code, 0)
        return destination

    def test_both_distributions_build_and_transfer_without_vm(self):
        for libc, image, suffix in [
            ("musl", "example/alpine@sha256:abc", ""),
            ("glibc", "example/ubuntu@sha256:def", "-glibc"),
        ]:
            with self.subTest(libc=libc):
                self.execute(libc)
                args, script = self.calls[-1]
                self.assertIn(image, args)
                self.assertEqual(b"SENTINEL_GRAPHICS_LIBC=glibc" in script, libc == "glibc")
                output = self.execute(
                    libc,
                    prebuilt=self.artifacts,
                    system="darwin",
                    runner=AssertionError("must not launch a builder"),
                )
                name = f"mesa-linux-arm64{suffix}.tar.xz"
                self.assertEqual((output / name).read_bytes(), (self.artifacts / name).read_bytes())

    def test_corrupted_or_stale_artifact_fails_without_fallback(self):
        self.execute()
        archive = self.artifacts / "mesa-linux-arm64.tar.xz"
        original = archive.read_bytes()
        archive.write_bytes(b"damaged")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute(prebuilt=self.artifacts, system="darwin")
        archive.write_bytes(original)
        (self.script.parent / "build-guest.sh").write_text("changed build inputs")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute(prebuilt=self.artifacts, system="darwin")

    def test_macos_ci_requires_supplied_artifacts(self):
        with self.assertRaisesRegex(RuntimeError, "requires SENTINEL_GUEST_GRAPHICS_DIR"):
            self.execute(system="darwin", runner=AssertionError("must not launch a VM"))

    def test_missing_supplied_artifact_fails(self):
        with self.assertRaises(FileNotFoundError):
            self.execute(prebuilt=self.desktop / "missing", system="darwin")
