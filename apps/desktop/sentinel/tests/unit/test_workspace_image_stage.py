"""Verify native-image staging entirely with tiny local OCI fixture blobs."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workspace_images import builder, image_fixture

spec = importlib.util.spec_from_file_location(
    "workspace_image_stage",
    builder.DESKTOP / "scripts/packaging/workspace-images/stage.py",
)
stager = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"build": builder}):
    spec.loader.exec_module(stager)


def fixture(source, split=False):
    bases = json.loads((builder.RECIPES / "bases.json").read_bytes())
    header = {
        "schema": 1,
        "boot_contract": bases["boot_contract"],
        "platform": bases["platform"],
        "source_key": builder.source_key(),
    }
    images = {}
    for distribution, base in bases["distributions"].items():
        bundle = source / distribution if split else source
        layout = bundle / distribution
        image_fixture(layout, base["entrypoint"])
        reference, digest = builder.qualify_layout(layout, distribution, base["entrypoint"])
        images[distribution] = {
            "layout": distribution,
            "reference": reference,
            "digest": digest,
            "base_image": base["image"],
            "release": base["release"],
            "entrypoint": base["entrypoint"],
        }
        if split:
            (bundle / "manifest.json").write_bytes(
                builder.encoded({**header, "images": {distribution: images[distribution]}})
            )
    if not split:
        (source / "manifest.json").write_bytes(builder.encoded({**header, "images": images}))


class WorkspaceImageStageTests(unittest.TestCase):
    def test_verify_only_cli_does_not_publish_or_modify(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input"
            fixture(source)
            before = {
                str(file.relative_to(source)): file.read_bytes()
                for file in source.rglob("*")
                if file.is_file()
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(stager.__file__)),
                    str(source),
                    "--verify-only",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                set(json.loads(result.stdout)["images"]), {"alpine", "ubuntu", "debian"}
            )
            self.assertEqual(
                before,
                {
                    str(file.relative_to(source)): file.read_bytes()
                    for file in source.rglob("*")
                    if file.is_file()
                },
            )
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_merged_and_per_distribution_exports_stage_same_relocatable_catalog(self):
        for split in (False, True):
            with self.subTest(split=split), tempfile.TemporaryDirectory() as directory:
                source, output = (
                    Path(directory) / "input",
                    Path(directory) / "runtime/workspace-images",
                )
                fixture(source, split)
                catalog = stager.stage(source, output)
                self.assertEqual(set(catalog["images"]), {"alpine", "ubuntu", "debian"})
                self.assertEqual(json.loads((output / "manifest.json").read_bytes()), catalog)
                stager.verify_catalog(output)
                for name, image in catalog["images"].items():
                    self.assertEqual(image["layout"], name)
                    self.assertTrue((output / name / "index.json").is_file())
                self.assertTrue(
                    (source / ("alpine/manifest.json" if split else "manifest.json")).is_file()
                )

    def test_invalid_artifacts_do_not_replace_current_bundle(self):
        for field, value in [
            ("source_key", "stale"),
            ("platform", "linux/amd64"),
            ("boot_contract", 999),
        ]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / "input", Path(directory) / "output"
                fixture(source)
                output.mkdir()
                marker = output / "keep"
                marker.write_text("previous valid bundle")
                metadata = json.loads((source / "manifest.json").read_bytes())
                metadata[field] = value
                (source / "manifest.json").write_bytes(builder.encoded(metadata))
                with self.assertRaisesRegex(ValueError, field):
                    stager.stage(source, output)
                self.assertEqual(marker.read_text(), "previous valid bundle")

    def test_missing_distribution_and_traversal_fail(self):
        for change in (
            "missing",
            "traversal",
            "wrong_reference",
            "wrong_entrypoint",
            "wrong_base",
        ):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as directory,
            ):
                source = Path(directory) / "input"
                fixture(source)
                metadata = json.loads((source / "manifest.json").read_bytes())
                image = metadata["images"]["ubuntu"]
                if change == "missing":
                    del metadata["images"]["ubuntu"]
                elif change == "traversal":
                    image["layout"] = "../outside"
                elif change == "wrong_reference":
                    image["reference"] = "other@" + image["digest"]
                elif change == "wrong_entrypoint":
                    image["entrypoint"] = ["/bin/sh"]
                else:
                    image["base_image"] = "oldbase"
                (source / "manifest.json").write_bytes(builder.encoded(metadata))
                with self.assertRaises(ValueError):
                    stager.stage(source, Path(directory) / "output")

    def test_blob_corruption_and_parent_symlinks_rejected(self):
        for corrupt in (False, True):
            with (
                self.subTest(corrupt=corrupt),
                tempfile.TemporaryDirectory() as directory,
            ):
                source = Path(directory) / "input"
                fixture(source)
                blobs = source / "alpine/blobs"
                if corrupt:
                    next((blobs / "sha256").iterdir()).write_bytes(b"bad")
                else:
                    blobs.rename(source / "external-blobs")
                    blobs.symlink_to(source / "external-blobs", target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "verification|non-regular"):
                    stager.stage(source, Path(directory) / "output")

    def test_success_replaces_only_owned_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = (
                Path(directory) / "input",
                Path(directory) / "runtime/workspace-images",
            )
            fixture(source)
            output.mkdir(parents=True)
            (output / "old").write_text("outdated catalog")
            sibling = output.parent / "kernel"
            sibling.write_text("keep")
            stager.stage(source, output)
            self.assertFalse((output / "old").exists())
            self.assertEqual(sibling.read_text(), "keep")
            self.assertFalse(list(output.parent.glob(".workspace-images-*")))


if __name__ == "__main__":
    unittest.main()
