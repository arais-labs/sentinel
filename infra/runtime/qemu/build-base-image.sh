#!/usr/bin/env bash
set -euo pipefail

QEMU_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${QEMU_DIR}/../../.." && pwd)"
CACHE_DIR="${SENTINEL_QEMU_CACHE_DIR:-${QEMU_DIR}/cache}"
BUILD_ROOT="${SENTINEL_QEMU_BUILD_ROOT:-${QEMU_DIR}/build}"
OUTPUT_DIR="${SENTINEL_QEMU_OUTPUT_DIR:-${QEMU_DIR}/output}"
RUN_DIR="${SENTINEL_QEMU_RUN_DIR:-${QEMU_DIR}/run}"
CLOUD_INIT_DIR="${QEMU_DIR}/cloud-init"
PROVISION_SCRIPT="${QEMU_DIR}/provision/runtime-base.sh"

BASE_IMAGE_URL="${SENTINEL_QEMU_BASE_IMAGE_URL:-https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-arm64.qcow2}"
BASE_SHA_URL="${SENTINEL_QEMU_BASE_SHA_URL:-https://cloud.debian.org/images/cloud/bookworm/latest/SHA512SUMS}"
BASE_IMAGE_NAME="${SENTINEL_QEMU_BASE_IMAGE_NAME:-debian-12-generic-arm64.qcow2}"
OUTPUT_IMAGE_NAME="${SENTINEL_QEMU_OUTPUT_IMAGE_NAME:-sentinel-runtime-base-arm64.qcow2}"
CPUS="${SENTINEL_QEMU_CPUS:-4}"
MEMORY_MB="${SENTINEL_QEMU_MEMORY_MB:-4096}"
DISK_SIZE="${SENTINEL_QEMU_DISK_SIZE:-16G}"
SSH_PORT="${SENTINEL_QEMU_SSH_PORT:-2222}"
BUILDER_USER="${SENTINEL_QEMU_BUILDER_USER:-builder}"

mkdir -p "${CACHE_DIR}" "${BUILD_ROOT}" "${OUTPUT_DIR}" "${RUN_DIR}"

BASE_IMAGE="${CACHE_DIR}/${BASE_IMAGE_NAME}"
SHA_FILE="${CACHE_DIR}/SHA512SUMS"
BUILD_ID="$(date +%Y%m%d-%H%M%S)"
BUILD_DIR="${BUILD_ROOT}/${BUILD_ID}"
SEED_DIR="${BUILD_DIR}/seed"
SEED_ISO="${BUILD_DIR}/cidata.iso"
WORKING_IMAGE="${BUILD_DIR}/runtime.qcow2"
SSH_KEY="${BUILD_DIR}/builder_ed25519"
PID_FILE="${RUN_DIR}/build.pid"
SERIAL_LOG="${BUILD_DIR}/serial.log"
VARS_FILE="${BUILD_DIR}/edk2-arm-vars.fd"
OUTPUT_IMAGE="${OUTPUT_DIR}/${OUTPUT_IMAGE_NAME}"
OUTPUT_KEY="${OUTPUT_DIR}/${OUTPUT_IMAGE_NAME%.qcow2}.id_ed25519"
OUTPUT_PUB="${OUTPUT_KEY}.pub"

mkdir -p "${BUILD_DIR}" "${SEED_DIR}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd hdiutil
need_cmd qemu-img
need_cmd qemu-system-aarch64
need_cmd ssh
need_cmd ssh-keygen
need_cmd python3
need_cmd sha512sum

QEMU_CELLAR="$(brew --prefix qemu)"
EDK2_CODE="$(find "${QEMU_CELLAR%/opt/qemu}" /opt/homebrew/Cellar/qemu -path '*/share/qemu/edk2-aarch64-code.fd' 2>/dev/null | head -n 1)"
EDK2_VARS="$(find "${QEMU_CELLAR%/opt/qemu}" /opt/homebrew/Cellar/qemu -path '*/share/qemu/edk2-arm-vars.fd' 2>/dev/null | head -n 1)"
if [[ -z "${EDK2_CODE}" || -z "${EDK2_VARS}" ]]; then
  echo "Could not locate QEMU aarch64 firmware files" >&2
  exit 1
fi

echo "Downloading base image metadata..."
curl -fsSL "${BASE_SHA_URL}" -o "${SHA_FILE}"
if [[ ! -f "${BASE_IMAGE}" ]]; then
  echo "Downloading base cloud image..."
  curl -fL "${BASE_IMAGE_URL}" -o "${BASE_IMAGE}"
fi

EXPECTED_SHA="$(awk -v name="${BASE_IMAGE_NAME}" '{ file=$2; sub(/^\*/, "", file); if (file == name) print $1 }' "${SHA_FILE}")"
if [[ -z "${EXPECTED_SHA}" ]]; then
  echo "Could not find checksum for ${BASE_IMAGE_NAME}" >&2
  exit 1
fi
ACTUAL_SHA="$(sha512sum "${BASE_IMAGE}" | awk '{print $1}')"
if [[ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]]; then
  echo "Checksum mismatch for ${BASE_IMAGE}" >&2
  echo "expected: ${EXPECTED_SHA}" >&2
  echo "actual:   ${ACTUAL_SHA}" >&2
  exit 1
fi

ssh-keygen -q -t ed25519 -N "" -f "${SSH_KEY}" >/dev/null
cp "${EDK2_VARS}" "${VARS_FILE}"
BASE_FORMAT="$(qemu-img info "${BASE_IMAGE}" | awk -F': ' '/file format/ {print $2}')"
if [[ -z "${BASE_FORMAT}" ]]; then
  echo "Could not determine base image format" >&2
  exit 1
fi
qemu-img create -f qcow2 -F "${BASE_FORMAT}" -b "${BASE_IMAGE}" "${WORKING_IMAGE}" "${DISK_SIZE}" >/dev/null

PROVISION_B64="$(base64 < "${PROVISION_SCRIPT}" | tr -d '\n')"
SSH_PUB="$(tr -d '\n' < "${SSH_KEY}.pub")"
TIMEZONE="${SENTINEL_QEMU_TIMEZONE:-America/Los_Angeles}"
LOCALE="${SENTINEL_QEMU_LOCALE:-en_US.UTF-8}"

cp "${CLOUD_INIT_DIR}/meta-data" "${SEED_DIR}/meta-data"
python3 - "${CLOUD_INIT_DIR}/user-data.tpl" "${SEED_DIR}/user-data" "${PROVISION_B64}" "${SSH_PUB}" "${TIMEZONE}" "${LOCALE}" <<'PY'
from pathlib import Path
import sys

tpl_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
content = tpl_path.read_text()
content = content.replace("__PROVISION_SCRIPT_B64__", sys.argv[3])
content = content.replace("__SSH_AUTHORIZED_KEY__", sys.argv[4])
content = content.replace("__TIMEZONE__", sys.argv[5])
content = content.replace("__LOCALE__", sys.argv[6])
out_path.write_text(content)
PY

hdiutil makehybrid -quiet -iso -joliet -default-volume-name cidata -o "${SEED_ISO}" "${SEED_DIR}"

cleanup() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
    rm -f "${PID_FILE}"
  fi
}
trap cleanup EXIT

echo "Booting QEMU builder..."
qemu-system-aarch64 \
  -name sentinel-qemu-builder \
  -machine virt,accel=hvf \
  -cpu host \
  -smp "${CPUS}" \
  -m "${MEMORY_MB}" \
  -device virtio-gpu-pci \
  -device virtio-keyboard-pci \
  -device virtio-mouse-pci \
  -device qemu-xhci \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 \
  -device virtio-net-pci,netdev=net0 \
  -drive if=pflash,format=raw,readonly=on,file="${EDK2_CODE}" \
  -drive if=pflash,format=raw,file="${VARS_FILE}" \
  -drive if=virtio,format=qcow2,file="${WORKING_IMAGE}" \
  -drive if=virtio,media=cdrom,format=raw,file="${SEED_ISO}" \
  -display none \
  -serial "file:${SERIAL_LOG}" \
  >/dev/null 2>&1 &
QEMU_PID=$!
echo "${QEMU_PID}" > "${PID_FILE}"

SSH_OPTS=(
  -i "${SSH_KEY}"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o BatchMode=yes
  -o ConnectTimeout=5
  -p "${SSH_PORT}"
)

echo "Waiting for SSH..."
for _ in $(seq 1 180); do
  if ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 true >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 true >/dev/null

print_guest_progress() {
  ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 'bash -s' <<'REMOTE' 2>/dev/null || true
set -euo pipefail

status="$(cloud-init status 2>/dev/null | sed 's/^/cloud-init: /' || true)"
if [[ -n "${status}" ]]; then
  echo "${status}"
fi

if [[ -f /var/log/apt/term.log ]]; then
  apt_line="$(sudo tail -1 /var/log/apt/term.log 2>/dev/null || true)"
  if [[ -n "${apt_line}" ]]; then
    echo "apt: ${apt_line}"
  fi
fi

if [[ -f /var/log/sentinel-runtime-base.log ]]; then
  sentinel_line="$(sudo tail -1 /var/log/sentinel-runtime-base.log 2>/dev/null || true)"
  if [[ -n "${sentinel_line}" ]]; then
    echo "sentinel: ${sentinel_line}"
  fi
fi

markers=()
for marker in runtime-provisioned-v1 browser-provisioned-v1; do
  if [[ -f "/var/lib/sentinel/${marker}" ]]; then
    markers+=("${marker}")
  fi
done
if [[ ${#markers[@]} -gt 0 ]]; then
  echo "markers: ${markers[*]}"
fi
REMOTE
}

dump_guest_failure_logs() {
  ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 'bash -s' <<'REMOTE' 2>/dev/null || true
set -euo pipefail

echo "--- cloud-init status ---"
cloud-init status --long 2>&1 || true

echo "--- sentinel image provision log ---"
sudo tail -240 /var/log/sentinel-image-provision.log 2>&1 || true

echo "--- cloud-init output ---"
sudo tail -160 /var/log/cloud-init-output.log 2>&1 || true

echo "--- failed systemd units ---"
systemctl --no-pager --failed 2>&1 || true

echo "--- sentinel desktop service ---"
systemctl --no-pager status sentinel-runtime-desktop.service 2>&1 || true

echo "--- sentinel browser service ---"
systemctl --no-pager status sentinel-runtime-browser.service 2>&1 || true

echo "--- chromium logs ---"
sudo tail -120 /tmp/chromium-reset.log 2>&1 || true
sudo tail -120 /tmp/chromium-socat.log 2>&1 || true
REMOTE
}

echo "Waiting for baked runtime markers and services..."
READY_CMD=$'test -f /var/lib/sentinel/runtime-provisioned-v1 && test -f /var/lib/sentinel/browser-provisioned-v1 && curl -fsS http://127.0.0.1:6080/vnc.html >/dev/null && curl -fsS http://127.0.0.1:9223/json/version >/dev/null'
FAILED_CMD=$'cloud-init status 2>/dev/null | grep -Eq "status: (error|done)"'
READY=0
for attempt in $(seq 1 1800); do
  if ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 "${READY_CMD}" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 "${FAILED_CMD}" >/dev/null 2>&1; then
    echo "QEMU image provisioning finished before runtime readiness checks passed." >&2
    dump_guest_failure_logs >&2
    exit 1
  fi
  if (( attempt == 1 || attempt % 15 == 0 )); then
    print_guest_progress
  fi
  sleep 2
done
if [[ "${READY}" -ne 1 ]]; then
  echo "QEMU image provisioning did not become ready before timeout." >&2
  dump_guest_failure_logs >&2
  exit 1
fi

echo "Shutting down builder VM..."
ssh "${SSH_OPTS[@]}" "${BUILDER_USER}"@127.0.0.1 "sudo shutdown -h now" >/dev/null 2>&1 || true
for _ in $(seq 1 120); do
  if ! kill -0 "${QEMU_PID}" 2>/dev/null; then
    break
  fi
  sleep 2
done
if kill -0 "${QEMU_PID}" 2>/dev/null; then
  echo "QEMU builder did not exit cleanly after guest shutdown" >&2
  exit 1
fi

echo "Compacting output image..."
qemu-img convert -c -O qcow2 "${WORKING_IMAGE}" "${OUTPUT_IMAGE}"
cp "${SSH_KEY}" "${OUTPUT_KEY}"
cp "${SSH_KEY}.pub" "${OUTPUT_PUB}"
chmod 0600 "${OUTPUT_KEY}"
echo "Built ${OUTPUT_IMAGE}"
