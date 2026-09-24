"""Driver discovery for Sentinel's virtio GPU, not the host's physical GPU."""

from pathlib import Path

# Match ICD manifests, not library paths: confined apps have their own Mesa.
# Physical-GPU native-context drivers must not probe this virgl/virtio device.
GPU_ENVIRONMENT = {"VK_LOADER_DRIVERS_SELECT": "*virtio*"}


def install(root=Path("/")):
    assignments = "\n".join(f"{key}={value}" for key, value in GPU_ENVIRONMENT.items()) + "\n"
    files = {
        "etc/environment.d/60-sentinel-gpu.conf": assignments,
        "etc/profile.d/sentinel-gpu.sh": "".join(
            f"export {key}='{value}'\n" for key, value in GPU_ENVIRONMENT.items()
        ),
        "etc/systemd/system.conf.d/60-sentinel-gpu.conf": (
            "[Manager]\nDefaultEnvironment="
            + " ".join(f'"{key}={value}"' for key, value in GPU_ENVIRONMENT.items())
            + "\n"
        ),
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".next")
        temporary.write_text(contents)
        temporary.chmod(0o644)
        temporary.replace(path)


if __name__ == "__main__":
    import sys

    install(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/"))
