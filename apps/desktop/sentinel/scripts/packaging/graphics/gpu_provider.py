"""Separate content-snap target sharing the persistent glibc graphics builder."""

import importlib.util
import json
import shlex
import shutil


class ProviderTarget:
    def __init__(self, desktop, destination, cache, mesa_build_key):
        self.script = desktop / "scripts/packaging/graphics/package-gpu-provider.py"
        spec = importlib.util.spec_from_file_location("gpu_provider_packager", self.script)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        self.sha = packager.sha
        self.assets = desktop / "native/graphics/packaging/gpu-2404"
        self.mesa = destination / "mesa-linux-arm64-glibc.tar.xz"
        mesa_metadata = json.loads((destination / "mesa-linux-arm64-glibc.json").read_text())
        self.mesa_sha = self.sha(self.mesa)
        if (
            mesa_metadata.get("buildKey") != mesa_build_key
            or mesa_metadata.get("sha256") != self.mesa_sha
        ):
            raise RuntimeError("Package gpu-2404 only after building verified glibc Mesa")
        self.pin = json.loads((desktop / "native/graphics/sources.lock.json").read_text())[
            "mesa-guest"
        ]
        self.key = packager.provider_identity(self.mesa_sha, self.pin["sha256"], self.assets)
        self.version = "1-" + self.key[:16]
        self.filename = f"sentinel-gpu-2404_{self.version}_arm64.snap"
        self.artifact = cache / ("gpu-2404-" + self.key + ".snap")
        self.manifest = self.artifact.with_suffix(".json")

    def valid(self, artifact, manifest):
        try:
            metadata = json.loads(manifest.read_text())
            return (
                metadata.get("schema") == 1
                and metadata.get("identity") == self.key
                and metadata.get("version") == self.version
                and metadata.get("mesa_sha256") == self.mesa_sha
                and metadata.get("mesa_source_sha256") == self.pin["sha256"]
                and metadata.get("size") == artifact.stat().st_size
                and metadata.get("sha256") == self.sha(artifact)
            )
        except (OSError, ValueError, KeyError):
            return False

    def stage(self, output, cached_source):
        shutil.copy2(self.script, output / self.script.name)
        for relative in (
            "dependencies.lock.json",
            "meta/snap.yaml.in",
            "bin/gpu-2404-provider-wrapper",
        ):
            target = output / "gpu-2404" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.assets / relative, target)
        shutil.copy2(self.mesa, output / "mesa-linux-arm64-glibc.tar.xz")
        shutil.copy2(cached_source(self.pin), output / "mesa.tar.xz")

    def commands(self, output):
        # Install packaging tools only when absent, in the existing builder OS.
        # This is unrelated to Mesa compilation and does not change its keys.
        return (
            """set -eu
missing=false
for tool in snap mksquashfs python3 curl readelf dpkg-deb; do
  command -v "$tool" >/dev/null || missing=true
done
if "$missing"; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update -qq
  apt-get install -y --no-install-recommends snapd squashfs-tools python3 curl binutils ca-certificates
fi
"""
            + shlex.join(
                [
                    "python3",
                    str(output / self.script.name),
                    "--mesa",
                    str(output / "mesa-linux-arm64-glibc.tar.xz"),
                    "--mesa-sha256",
                    self.mesa_sha,
                    "--mesa-source",
                    str(output / "mesa.tar.xz"),
                    "--mesa-source-sha256",
                    self.pin["sha256"],
                    "--assets",
                    str(output / "gpu-2404"),
                    "--cache",
                    "/var/cache/sentinel-build/gpu-2404-provider",
                    "--output",
                    str(output / "provider-output"),
                ]
            )
            + "\n"
        )

    def record(self, output, publish_file):
        artifact = output / "provider-output" / self.filename
        manifest = artifact.with_suffix(".json")
        if not self.valid(artifact, manifest):
            raise RuntimeError("Built gpu-2404 provider failed provenance/checksum verification")
        publish_file(artifact, self.artifact)
        publish_file(manifest, self.manifest)
