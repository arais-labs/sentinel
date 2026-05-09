#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QEMU_DIR="${ROOT_DIR}/qemu"
OUTPUT_IMAGE="${SENTINEL_QEMU_VALIDATE_IMAGE:-${QEMU_DIR}/output/sentinel-runtime-base-arm64.qcow2}"
RUN_DIR="${QEMU_DIR}/run/validate"
SSH_PORT="${SENTINEL_QEMU_VALIDATE_SSH_PORT:-2224}"
VNC_PORT="${SENTINEL_QEMU_VALIDATE_VNC_PORT:-16080}"
CDP_PORT="${SENTINEL_QEMU_VALIDATE_CDP_PORT:-19223}"
MEMORY_MB="${SENTINEL_QEMU_VALIDATE_MEMORY_MB:-4096}"
CPUS="${SENTINEL_QEMU_VALIDATE_CPUS:-4}"
VALIDATE_USER="${SENTINEL_QEMU_VALIDATE_USER:-builder}"
SEED_DIR="${RUN_DIR}/seed"
SEED_ISO="${RUN_DIR}/cidata.iso"

mkdir -p "${RUN_DIR}"
rm -rf "${RUN_DIR:?}/"*

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd qemu-system-aarch64
need_cmd ssh
need_cmd ssh-keygen
need_cmd qemu-img

if [[ ! -f "${OUTPUT_IMAGE}" ]]; then
  echo "Image not found: ${OUTPUT_IMAGE}" >&2
  exit 1
fi

EDK2_CODE="$(find /opt/homebrew/Cellar/qemu -path '*/share/qemu/edk2-aarch64-code.fd' 2>/dev/null | head -n 1)"
EDK2_VARS="$(find /opt/homebrew/Cellar/qemu -path '*/share/qemu/edk2-arm-vars.fd' 2>/dev/null | head -n 1)"
if [[ -z "${EDK2_CODE}" || -z "${EDK2_VARS}" ]]; then
  echo "Could not locate QEMU firmware files" >&2
  exit 1
fi

WORK_IMAGE="${RUN_DIR}/validate.qcow2"
PID_FILE="${RUN_DIR}/vm.pid"
SERIAL_LOG="${RUN_DIR}/serial.log"
QEMU_LOG="${RUN_DIR}/qemu.log"
VARS_FILE="${RUN_DIR}/edk2-arm-vars.fd"
SSH_KEY="${SENTINEL_QEMU_VALIDATE_KEY:-${OUTPUT_IMAGE%.qcow2}.id_ed25519}"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "Validation key not found: ${SSH_KEY}" >&2
  exit 1
fi
mkdir -p "${SEED_DIR}"
cp "${EDK2_VARS}" "${VARS_FILE}"
qemu-img create -f qcow2 -F qcow2 -b "${OUTPUT_IMAGE}" "${WORK_IMAGE}" >/dev/null

cat > "${SEED_DIR}/meta-data" <<EOF
instance-id: sentinel-qemu-validate
local-hostname: sentinel-qemu-validate
EOF

cat > "${SEED_DIR}/user-data" <<EOF
#cloud-config
users:
  - name: builder
    ssh_authorized_keys:
      - $(tr -d '\n' < "${SSH_KEY}.pub")
ssh_pwauth: false
EOF

hdiutil makehybrid -quiet -iso -joliet -default-volume-name cidata -o "${SEED_ISO}" "${SEED_DIR}"

cleanup() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT

qemu-system-aarch64 \
  -name sentinel-qemu-validate \
  -machine virt,accel=hvf \
  -cpu host \
  -smp "${CPUS}" \
  -m "${MEMORY_MB}" \
  -device virtio-gpu-pci \
  -device virtio-keyboard-pci \
  -device virtio-mouse-pci \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:${VNC_PORT}-:6080,hostfwd=tcp:127.0.0.1:${CDP_PORT}-:9223 \
  -device virtio-net-pci,netdev=net0 \
  -drive if=pflash,format=raw,readonly=on,file="${EDK2_CODE}" \
  -drive if=pflash,format=raw,file="${VARS_FILE}" \
  -drive if=virtio,format=qcow2,file="${WORK_IMAGE}" \
  -drive if=virtio,media=cdrom,format=raw,file="${SEED_ISO}" \
  -display none \
  -serial "file:${SERIAL_LOG}" \
  >"${QEMU_LOG}" 2>&1 &
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

for _ in $(seq 1 180); do
  if ! kill -0 "${QEMU_PID}" 2>/dev/null; then
    echo "QEMU validate VM exited before SSH became ready" >&2
    test -f "${QEMU_LOG}" && tail -n 120 "${QEMU_LOG}" >&2 || true
    test -f "${SERIAL_LOG}" && tail -n 120 "${SERIAL_LOG}" >&2 || true
    exit 1
  fi
  if ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 true >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 true >/dev/null

ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 \
  'test -f /var/lib/sentinel/runtime-provisioned-v1 && test -f /var/lib/sentinel/browser-provisioned-v1'

if ! ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 \
  'sudo /usr/local/bin/sentinel-session-prepare.sh --session-id validate123 >/tmp/sentinel-session.env &&
   grep -q "^SESSION_USER=ssn-validate123$" /tmp/sentinel-session.env &&
   grep -q "^SESSION_WORKSPACE=/srv/sentinel/sessions/validate123/workspace$" /tmp/sentinel-session.env &&
   sudo test -d /srv/sentinel/sessions/validate123/home &&
   sudo test -d /srv/sentinel/sessions/validate123/browser-profile &&
   sudo test -d /srv/sentinel/sessions/validate123/workspace &&
   sudo -u ssn-validate123 test -d /srv/sentinel/sessions/validate123/home &&
   sudo -u ssn-validate123 test -d /srv/sentinel/sessions/validate123/browser-profile &&
   sudo -u ssn-validate123 test -d /srv/sentinel/sessions/validate123/workspace &&
   sudo /usr/local/bin/sentinel-session-cleanup.sh --session-id validate123 &&
   sudo test ! -d /srv/sentinel/sessions/validate123'; then
  echo "Guest multi-user session helper validation failed" >&2
  ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 \
    'echo "--- session env"; cat /tmp/sentinel-session.env 2>/dev/null || true;
     echo "--- session root listing"; sudo ls -la /srv/sentinel/sessions || true;
     echo "--- validate session listing"; sudo ls -la /srv/sentinel/sessions/validate123 2>/dev/null || true;
     echo "--- prepare script"; sudo sed -n "1,260p" /usr/local/bin/sentinel-session-prepare.sh || true;
     echo "--- cleanup script"; sudo sed -n "1,200p" /usr/local/bin/sentinel-session-cleanup.sh || true' >&2 || true
  exit 1
fi

guest_wait_for_url() {
  local url="$1"
  local attempts="$2"
  for _ in $(seq 1 "${attempts}"); do
    if ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 \
      "curl -fsS '${url}' >/dev/null" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

dump_guest_diagnostics() {
  ssh "${SSH_OPTS[@]}" "${VALIDATE_USER}"@127.0.0.1 \
    'echo "--- systemctl status sentinel-runtime-desktop.service"; sudo systemctl status sentinel-runtime-desktop.service --no-pager || true;
     echo "--- journalctl sentinel-runtime-desktop.service"; sudo journalctl -u sentinel-runtime-desktop.service -n 120 --no-pager || true;
     echo "--- systemctl status sentinel-runtime-browser.service"; sudo systemctl status sentinel-runtime-browser.service --no-pager || true;
     echo "--- journalctl sentinel-runtime-browser.service"; sudo journalctl -u sentinel-runtime-browser.service -n 120 --no-pager || true;
     echo "--- listeners"; ss -ltnp | grep -E ":(5900|6080|9222|9223)\\b" || true' >&2 || true
}

if ! guest_wait_for_url "http://127.0.0.1:6080/vnc.html" 90; then
  echo "Guest VNC endpoint did not become ready" >&2
  dump_guest_diagnostics
  exit 1
fi

if ! guest_wait_for_url "http://127.0.0.1:9223/json/version" 90; then
  echo "Guest CDP endpoint did not become ready" >&2
  dump_guest_diagnostics
  exit 1
fi

curl -fsS "http://127.0.0.1:${VNC_PORT}/vnc.html" >/dev/null
curl -fsS "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null

echo "Validation OK"
echo "VNC: http://127.0.0.1:${VNC_PORT}/vnc.html"
echo "CDP: http://127.0.0.1:${CDP_PORT}/json/version"
