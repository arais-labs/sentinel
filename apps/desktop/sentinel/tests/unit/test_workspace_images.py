"""OCI image packaging contract; no daemon or VM is started by these tests."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

DESKTOP = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "workspace_image_build", DESKTOP / "scripts/packaging/workspace-images/build.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def put_blob(layout, value, media_type):
    data = value if isinstance(value, bytes) else builder.encoded(value)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    path = layout / "blobs/sha256" / digest.split(":")[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"mediaType": media_type, "digest": digest, "size": len(data)}


def image_fixture(layout, entrypoint=None, architecture="arm64"):
    layout.mkdir(parents=True, exist_ok=True)
    config = put_blob(
        layout,
        {
            "architecture": architecture,
            "os": "linux",
            "config": {"Entrypoint": entrypoint or ["/sbin/init"], "Cmd": []},
        },
        "application/vnd.oci.image.config.v1+json",
    )
    layer = put_blob(layout, b"fixture-layer", "application/vnd.oci.image.layer.v1.tar")
    manifest = put_blob(
        layout, {"schemaVersion": 2, "config": config, "layers": [layer]}, builder.MANIFEST_TYPE
    )
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (layout / "index.json").write_bytes(
        builder.encoded({"schemaVersion": 2, "manifests": [manifest]})
    )
    return layer


class WorkspaceImageTests(unittest.TestCase):
    def test_source_identity_tracks_recipes_not_documentation(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(builder, "RECIPES", Path(directory)),
        ):
            recipe = Path(directory) / "install-account.sh"
            recipe.write_text("first recipe")
            before = builder.source_key()
            (Path(directory) / "README.md").write_text("new instructions")
            self.assertEqual(builder.source_key(), before)
            recipe.write_text("changed recipe")
            self.assertNotEqual(builder.source_key(), before)

    def test_actual_index_digest_and_import_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Path(directory)
            image_fixture(layout)
            reference, digest = builder.qualify_layout(layout, "ubuntu", ["/sbin/init"])
            descriptor = json.loads((layout / "index.json").read_bytes())["manifests"][0]
            self.assertEqual(descriptor["digest"], digest)
            self.assertEqual(reference, f"sentinel.local/workspace/ubuntu@{digest}")
            self.assertEqual(
                descriptor["annotations"]["org.opencontainers.image.ref.name"], reference
            )
            self.assertEqual(
                builder.file_digest(builder.descriptor_blob(layout, descriptor)), digest
            )
            self.assertEqual(descriptor["mediaType"], builder.INDEX_TYPE)

    def test_corrupt_layer_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Path(directory)
            layer = image_fixture(layout)
            (layout / "blobs/sha256" / layer["digest"].split(":")[1]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "verification"):
                builder.qualify_layout(layout, "ubuntu", ["/sbin/init"])

    def test_wrong_architecture_and_non_init_rejected(self):
        for architecture, entrypoint in [("amd64", ["/sbin/init"]), ("arm64", ["dockerd"])]:
            with (
                self.subTest(architecture=architecture, entrypoint=entrypoint),
                tempfile.TemporaryDirectory() as directory,
            ):
                layout = Path(directory)
                image_fixture(layout, entrypoint, architecture)
                with self.assertRaises(ValueError):
                    builder.qualify_layout(layout, "ubuntu", ["/sbin/init"])

    def test_symlink_and_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Path(directory)
            layer = image_fixture(layout)
            blob = layout / "blobs/sha256" / layer["digest"].split(":")[1]
            target = layout / "outside"
            blob.rename(target)
            blob.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "regular file"):
                builder.descriptor_blob(layout, layer)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                builder.descriptor_blob(layout, {"digest": "sha256:../../outside", "size": 13})

    def test_failed_build_does_not_publish_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            with patch.object(
                builder.subprocess,
                "run",
                side_effect=[None, subprocess.CalledProcessError(1, "buildx")],
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.build(output, ["ubuntu"])
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_local_build_command_and_atomic_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            calls = []

            def run(command, check):
                calls.append(command)
                if "--output" in command:
                    option = command[command.index("--output") + 1]
                    layout = Path(option.split("dest=", 1)[1].rsplit(",tar=false", 1)[0])
                    image_fixture(layout)

            with patch.object(builder.subprocess, "run", side_effect=run):
                manifest = builder.build(output, ["ubuntu"], builder="owned-builder")
            self.assertEqual(manifest["boot_contract"], 1)
            self.assertEqual(manifest["images"]["ubuntu"]["entrypoint"], ["/sbin/init"])
            self.assertTrue((output / "ubuntu/index.json").is_file())
            self.assertIn("--provenance=false", calls[1])
            self.assertIn("owned-builder", calls[1])
            self.assertNotIn("--push", calls[1])
            self.assertEqual(calls[1][calls[1].index("--platform") + 1], "linux/arm64")
            with self.assertRaises(FileExistsError):
                builder.build(output, ["ubuntu"])

    def test_recipes_boot_native_init_and_use_pinned_bases(self):
        bases = json.loads((builder.RECIPES / "bases.json").read_bytes())
        self.assertEqual(set(bases["distributions"]), {"alpine", "ubuntu", "debian"})
        for distribution, base in bases["distributions"].items():
            self.assertRegex(base["image"], r"@sha256:[0-9a-f]{64}$")
            source = (builder.RECIPES / f"{distribution}.Dockerfile").read_text()
            self.assertIn("ENTRYPOINT " + json.dumps(base["entrypoint"]), source)
            self.assertNotIn("dockerd-entrypoint", source)
        for name in ["install-alpine.sh", "install-apt.sh"]:
            script = builder.RECIPES / name
            subprocess.run(["/bin/sh", "-n", str(script)], check=True)
            self.assertNotIn("sentinel-docker-ready", script.read_text())
            self.assertNotIn("docker", script.read_text())
            self.assertNotIn("containerd", script.read_text())
            self.assertNotIn("k3s", script.read_text())

    def test_native_pam_session_type_precedes_distribution_session(self):
        for protocol in ("wayland", "x11"):
            service = (builder.RECIPES / "pam" / f"sentinel-{protocol}").read_text()
            for facility in ("auth", "account", "password"):
                self.assertIn(f"{facility} include greetd\n", service)
            environment = (
                f"session required pam_env.so conffile=/etc/sentinel/pam-{protocol}.conf readenv=0"
            )
            self.assertIn(environment, service)
            self.assertLess(service.index(environment), service.index("session include greetd"))
            configuration = (builder.RECIPES / "pam" / f"pam-{protocol}.conf").read_text()
            self.assertEqual(
                configuration.strip(), f"XDG_SESSION_TYPE DEFAULT={protocol} OVERRIDE={protocol}"
            )
        for distribution in ("alpine", "ubuntu", "debian"):
            source = (builder.RECIPES / f"{distribution}.Dockerfile").read_text()
            self.assertIn("COPY pam/sentinel-* /etc/pam.d/", source)
            self.assertIn("COPY pam/pam-*.conf /etc/sentinel/", source)

    def test_native_apparmor_boot_policy_and_snap_dependencies(self):
        recipes = builder.RECIPES
        loader = recipes / "apparmor-load.sh"
        subprocess.run(["/bin/sh", "-n", str(loader)], check=True)
        # Delegate filtering and disabled/complain modes to the distro parser,
        # rather than maintaining our own interpretation of system profiles.
        self.assertIn("--replace --write-cache -- /etc/apparmor.d", loader.read_text())
        service = (recipes / "services/sentinel-apparmor.service").read_text()
        self.assertIn("Before=apparmor.service sysinit.target", service)
        self.assertIn("RequiredBy=sysinit.target", service)
        self.assertNotIn("ConditionSecurity", service)
        apt = (recipes / "install-apt.sh").read_text()
        self.assertIn("iproute2 apparmor", apt)
        self.assertIn("systemctl enable sentinel-apparmor.service", apt)
        for distribution in ("ubuntu", "debian"):
            source = (recipes / f"{distribution}.Dockerfile").read_text()
            self.assertIn("COPY apparmor-load.sh /usr/local/libexec/sentinel-apparmor-load", source)
            self.assertIn("COPY services/sentinel-apparmor.service", source)
        self.assertNotIn("apparmor", (recipes / "alpine.Dockerfile").read_text())
        ubuntu = (recipes / "ubuntu.Dockerfile").read_text()
        for unit in ("snapd.apparmor", "snapd"):
            self.assertIn(f"COPY services/{unit}.service.d/", ubuntu)
            dropin = (recipes / f"services/{unit}.service.d/sentinel-policy.conf").read_text()
            self.assertIn("Requires=sentinel-apparmor.service", dropin)
        snap = (recipes / "services/snapd.apparmor.service.d/sentinel-policy.conf").read_text()
        self.assertIn("ExecStartPre=/bin/sh /usr/local/libexec/sentinel-apparmor-load snap", snap)
        self.assertNotIn("ExecStart=", snap)


if __name__ == "__main__":
    unittest.main()
