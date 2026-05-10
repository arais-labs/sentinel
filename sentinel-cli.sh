#!/usr/bin/env bash
set -uo pipefail

# Sentinel Stack CLI - Smooth Transition Edition
# Fixes the buffer-skip glitch and ensures onboarding info is readable.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

INSTANCES_DIR="$ROOT_DIR/.instances"
mkdir -p "$INSTANCES_DIR"
TMP_PICK="/tmp/sentinel_pick_$(id -u)"
STATUS_CACHE_TS=0
STATUS_CACHE_TEXT=""
MENU_NOTE=""

# Colors and Styling
if [[ -t 1 ]]; then
  BOLD="$(printf '\033[1m')"
  DIM="$(printf '\033[2m')"
  RED="$(printf '\033[31m')"
  GREEN="$(printf '\033[32m')"
  YELLOW="$(printf '\033[33m')"
  BLUE="$(printf '\033[34m')"
  MAGENTA="$(printf '\033[35m')"
  CYAN="$(printf '\033[36m')"
  BG_BLUE="$(printf '\033[44m')"
  RESET="$(printf '\033[0m')"
  CURSOR_OFF="$(tput civis)"
  CURSOR_ON="$(tput cnorm)"
  CLEAR_SCREEN="$(tput clear)"
  GOTO_TOP="$(tput cup 0 0)"
  CLEAR_LINE="$(printf '\033[K')"
else
  BOLD="" DIM="" RED="" GREEN="" YELLOW="" BLUE="" MAGENTA="" CYAN="" BG_BLUE="" RESET="" CURSOR_OFF="" CURSOR_ON="" CLEAR_SCREEN="" GOTO_TOP="" CLEAR_LINE=""
fi

# Icons
ICON_INFO="ℹ️"
ICON_SUCCESS="✅"
ICON_WARN="⚠️"
ICON_ERROR="❌"
ICON_START="🚀"
ICON_STOP="🛑"
ICON_RESTART="🔄"
ICON_DELETE="🗑️"
ICON_LIST="📋"
ICON_CONFIG="⚙️"

print_header_content() {
  printf "${CYAN}${BOLD}${CLEAR_LINE}\n"
  printf "   _____            _   _             _ ${CLEAR_LINE}\n"
  printf "  / ____|          | | (_)           | |${CLEAR_LINE}\n"
  printf " | (___   ___ _ __ | |_ _ _ __   ___| |${CLEAR_LINE}\n"
  printf "  \___ \ / _ \ '_ \| __| | '_ \ / _ \ |${CLEAR_LINE}\n"
  printf "  ____) |  __/ | | | |_| | | | |  __/ |${CLEAR_LINE}\n"
  printf " |_____/ \___|_| |_|\__|_|_| |_|\___|_|${CLEAR_LINE}\n"
  printf "                                        ${CLEAR_LINE}\n"
  printf "${BLUE}    S T A C K   C O N T R O L   C E N T E R${RESET}${CLEAR_LINE}\n"
  printf "${DIM}    Arrows ⬆️ ⬇️  to navigate • Enter ↵  to select${RESET}${CLEAR_LINE}\n"
  printf "${CLEAR_LINE}\n"
}

invalidate_status_cache() {
  STATUS_CACHE_TS=0
  STATUS_CACHE_TEXT=""
}

set_menu_note() {
  MENU_NOTE="${1:-}"
}

print_menu_note() {
  [[ -n "$MENU_NOTE" ]] || return 0
  printf "%b${CLEAR_LINE}\n" "$MENU_NOTE"
  printf "${CLEAR_LINE}\n"
}

build_status_header() {
  local now
  now="$(date +%s)"
  if [[ -n "$STATUS_CACHE_TEXT" ]] && (( now - STATUS_CACHE_TS < 2 )); then
    printf "%b" "$STATUS_CACHE_TEXT"
    return 0
  fi

  local docker_summary docker_ok="false"
  if ! require_cmd docker; then
    docker_summary="${RED}Docker missing${RESET}"
  elif docker info >/dev/null 2>&1; then
    docker_ok="true"
    docker_summary="${GREEN}Docker running${RESET}"
  else
    docker_summary="${YELLOW}Docker unavailable${RESET}"
  fi

  local instances=()
  while IFS= read -r line; do [[ -n "$line" ]] && instances+=("$line"); done < <(get_instances)

  local summary
  summary="${DIM}Status${RESET}  ${docker_summary}"
  if [[ ${#instances[@]} -eq 0 ]]; then
    summary+=$'\n'"${DIM}Instances${RESET}  none yet"
  else
    local inst
    for inst in "${instances[@]}"; do
      local backend port state running
      backend="$(backend_label "$(current_instance_backend "$inst")")"
      port="$(read_env_value "$(instance_env_file "$inst")" "STACK_PORT" || echo "4747")"
      state="${DIM}stopped${RESET}"
      if [[ "$docker_ok" == "true" ]]; then
        running="$(count_running_instance_services "$inst")"
        if [[ "$running" =~ ^[0-9]+$ ]] && (( running > 0 )); then
          state="${GREEN}running:${running}${RESET}"
        fi
      else
        state="${YELLOW}docker-off${RESET}"
      fi
      summary+=$'\n'"${DIM}Instance${RESET}  ${BOLD}${inst}${RESET} • ${backend} • :${port} • ${state}"
    done
  fi
  summary+=$'\n'"${CLEAR_LINE}"

  STATUS_CACHE_TS="$now"
  STATUS_CACHE_TEXT="$summary"
  printf "%b" "$STATUS_CACHE_TEXT"
}

count_running_instance_services() {
  local inst="$1"
  docker ps \
    --filter "label=com.docker.compose.project=$(instance_project_name "$inst")" \
    --format '{{.ID}}' 2>/dev/null | awk 'NF { count++ } END { print count + 0 }'
}

info() { echo -e "${BLUE}${ICON_INFO} [INFO]${RESET} $*"; }
success() { echo -e "${GREEN}${ICON_SUCCESS} [OK]${RESET} $*"; }
warn() { echo -e "${YELLOW}${ICON_WARN} [WARN]${RESET} $*"; }
error() { echo -e "${RED}${ICON_ERROR} [ERROR]${RESET} $*"; }

require_cmd() { command -v "$1" >/dev/null 2>&1; }

get_instances() {
  local dirs
  shopt -s nullglob
  dirs=("$INSTANCES_DIR"/*/.)
  shopt -u nullglob
  [[ ${#dirs[@]} -eq 0 ]] && return 0
  local dir
  for dir in "${dirs[@]}"; do
    local name
    name="$(basename "$(dirname "$dir")")"
    [[ -f "$INSTANCES_DIR/$name/.env" ]] && echo "$name"
  done
}

check_port_occupied() {
  local port="$1"
  if require_cmd lsof; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
  elif require_cmd nc; then
    nc -z localhost "$port" >/dev/null 2>&1 && return 0
  fi
  return 1
}

ensure_docker_ready() {
  if ! require_cmd docker; then error "Docker not found."; return 1; fi
  if ! docker info >/dev/null 2>&1; then error "Docker not running."; return 1; fi
  return 0
}

generate_secret() {
  local bytes="${1:-32}"
  if require_cmd openssl; then openssl rand -hex "$bytes"
  elif require_cmd python3; then python3 -c "import secrets; print(secrets.token_hex($bytes))"
  else echo "sec-$(date +%s)-${RANDOM}"; fi
}

read_env_value() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  awk -F= -v k="$key" '$1 == k {print substr($0, index($0, "=") + 1)}' "$file" | tail -n 1
}

prompt_default() {
  local label="$1" default="$2" value
  printf "%s" "$CURSOR_ON" >&2
  # Use /dev/tty for input to avoid capture
  read -r -p "${BOLD}${label}${RESET} [${DIM}${default}${RESET}]: " value < /dev/tty
  printf "%s" "$CURSOR_OFF" >&2
  echo "${value:-$default}"
}

sanitize_instance_name() {
  echo "${1:-main}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

instance_dir() { echo "$INSTANCES_DIR/${1}"; }
instance_env_file() { echo "$INSTANCES_DIR/${1}/.env"; }
instance_project_name() { echo "sentinel-${1}"; }
instance_backup_dir() { echo "$INSTANCES_DIR/${1}/backups"; }
instance_workspaces_dir() { echo "$INSTANCES_DIR/${1}/workspaces"; }
instance_qemu_bridge_pid_file() { echo "$(instance_dir "$1")/qemu-bridge.pid"; }
instance_qemu_bridge_log_file() { echo "$(instance_dir "$1")/qemu-bridge.log"; }
instance_qemu_run_dir() { echo "$(instance_dir "$1")/qemu-run"; }

host_os() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux) echo "linux" ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
    *) echo "unknown" ;;
  esac
}

upsert_env_value() {
  local file="$1" key="$2" value="$3"
  mkdir -p "$(dirname "$file")"
  touch "$file"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    local tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$value" '
      BEGIN { replaced = 0 }
      $0 ~ ("^" key "=") {
        print key "=" value
        replaced = 1
        next
      }
      { print }
      END {
        if (!replaced) {
          print key "=" value
        }
      }
    ' "$file" > "$tmp" && mv "$tmp" "$file"
  else
    printf "%s=%s\n" "$key" "$value" >> "$file"
  fi
}

ensure_instance_structure() {
  local inst="$1"
  mkdir -p "$(instance_dir "$inst")"
  mkdir -p "$(instance_backup_dir "$inst")"
  mkdir -p "$(instance_workspaces_dir "$inst")"
}

current_instance_backend() {
  local inst="$1"
  local value
  value="$(read_env_value "$(instance_env_file "$inst")" "RUNTIME_EXEC_BACKEND" || true)"
  echo "${value:-docker}"
}

is_supported_runtime_backend() {
  case "${1:-docker}" in
    docker|qemu|remote) return 0 ;;
    *) return 1 ;;
  esac
}

backend_label() {
  case "${1:-docker}" in
    docker) echo "Docker" ;;
    qemu) echo "QEMU" ;;
    remote) echo "Custom SSH" ;;
    *) echo "Unsupported ($1)" ;;
  esac
}

ensure_supported_runtime_backend() {
  local inst="$1"
  local backend ef
  backend="$(current_instance_backend "$inst")"
  if is_supported_runtime_backend "$backend"; then
    return 0
  fi
  ef="$(instance_env_file "$inst")"
  error "Unsupported RUNTIME_EXEC_BACKEND='$backend' in $ef."
  warn "Edit the instance runtime backend to one of: docker, qemu, remote."
  return 1
}

default_qemu_image_path() {
  local arch image_dir candidate
  arch="$(uname -m)"
  image_dir="$ROOT_DIR/qemu/output"
  candidate="$image_dir/sentinel-runtime-base-${arch}.qcow2"
  [[ -f "$candidate" ]] && { echo "$candidate"; return 0; }
  candidate="$image_dir/sentinel-runtime-base.qcow2"
  [[ -f "$candidate" ]] && { echo "$candidate"; return 0; }
  candidate="$image_dir/sentinel-runtime-base-arm64.qcow2"
  [[ -f "$candidate" ]] && { echo "$candidate"; return 0; }
  echo ""
}

default_qemu_key_path() {
  local image_path
  image_path="$(default_qemu_image_path)"
  [[ -n "$image_path" ]] || { echo ""; return 0; }
  local candidate="${image_path%.qcow2}.id_ed25519"
  [[ -f "$candidate" ]] && { echo "$candidate"; return 0; }
  echo ""
}

default_qemu_bridge_port() {
  local inst="$1"
  local ef stack_port
  ef="$(instance_env_file "$inst")"
  stack_port="$(read_env_value "$ef" "STACK_PORT" || echo 4747)"
  if [[ "$stack_port" =~ ^[0-9]+$ ]]; then
    local port=$((stack_port + 40001))
    if (( port > 65535 )); then
      echo 47481
      return 0
    fi
    echo "$port"
    return 0
  fi
  echo 47481
}

ensure_qemu_bridge_settings() {
  local inst="$1"
  local ef token port
  ef="$(instance_env_file "$inst")"
  token="$(read_env_value "$ef" "RUNTIME_QEMU_BRIDGE_TOKEN" || true)"
  port="$(read_env_value "$ef" "RUNTIME_QEMU_BRIDGE_PORT" || true)"
  [[ -z "$token" ]] && token="$(generate_secret 24)"
  [[ -z "$port" ]] && port="$(default_qemu_bridge_port "$inst")"
  upsert_env_value "$ef" "RUNTIME_QEMU_BRIDGE_TOKEN" "$token"
  upsert_env_value "$ef" "RUNTIME_QEMU_BRIDGE_PORT" "$port"
  upsert_env_value "$ef" "RUNTIME_QEMU_BRIDGE_URL" "http://host.docker.internal:${port}"
}

is_qemu_bridge_healthy() {
  local inst="$1"
  local ef token port
  ef="$(instance_env_file "$inst")"
  token="$(read_env_value "$ef" "RUNTIME_QEMU_BRIDGE_TOKEN" || true)"
  port="$(read_env_value "$ef" "RUNTIME_QEMU_BRIDGE_PORT" || true)"
  [[ -z "$token" || -z "$port" ]] && return 1
  curl -fsS -H "X-Sentinel-Bridge-Token: $token" "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1
}

ensure_qemu_bridge_running() {
  local inst="$1"
  ensure_qemu_bridge_settings "$inst"
  if is_qemu_bridge_healthy "$inst"; then
    return 0
  fi

  local ef token port pid_file log_file
  ef="$(instance_env_file "$inst")"
  token="$(read_env_value "$ef" "RUNTIME_QEMU_BRIDGE_TOKEN" || true)"
  port="$(read_env_value "$ef" "RUNTIME_QEMU_BRIDGE_PORT" || true)"
  pid_file="$(instance_qemu_bridge_pid_file "$inst")"
  log_file="$(instance_qemu_bridge_log_file "$inst")"

  mkdir -p "$(dirname "$pid_file")"
  if [[ -f "$pid_file" ]]; then
    local existing_pid
    existing_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" >/dev/null 2>&1; then
      if is_qemu_bridge_healthy "$inst"; then
        return 0
      fi
      kill "$existing_pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pid_file"
  fi

  if ! require_cmd python3; then
    error "python3 is required to run the QEMU bridge."
    return 1
  fi
  if ! require_cmd qemu-system-aarch64 || ! require_cmd qemu-img; then
    error "QEMU is required for the QEMU runtime backend."
    return 1
  fi

  nohup python3 "$ROOT_DIR/scripts/qemu_bridge.py" \
    --port "$port" \
    --token "$token" \
    >"$log_file" 2>&1 &
  echo $! > "$pid_file"

  for _ in {1..20}; do
    if is_qemu_bridge_healthy "$inst"; then
      success "QEMU bridge is running for '$inst' on port $port."
      return 0
    fi
    sleep 0.25
  done

  error "QEMU bridge failed to start for '$inst'."
  return 1
}

stop_qemu_bridge() {
  local inst="$1"
  local pid_file
  pid_file="$(instance_qemu_bridge_pid_file "$inst")"
  [[ -f "$pid_file" ]] || return 0
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$pid_file"
}

write_instance_defaults() {
  local inst="$1"
  local ef
  ef="$(instance_env_file "$inst")"
  ensure_instance_structure "$inst"

  local stack_port postgres_db postgres_user postgres_password jwt_secret jwt_algorithm runtime_backend
  stack_port="$(read_env_value "$ef" "STACK_PORT" || true)"
  postgres_db="$(read_env_value "$ef" "POSTGRES_DB" || true)"
  postgres_user="$(read_env_value "$ef" "POSTGRES_USER" || true)"
  postgres_password="$(read_env_value "$ef" "POSTGRES_PASSWORD" || true)"
  jwt_secret="$(read_env_value "$ef" "JWT_SECRET_KEY" || true)"
  jwt_algorithm="$(read_env_value "$ef" "JWT_ALGORITHM" || true)"
  runtime_backend="$(read_env_value "$ef" "RUNTIME_EXEC_BACKEND" || true)"

  upsert_env_value "$ef" "STACK_PORT" "${stack_port:-4747}"
  upsert_env_value "$ef" "POSTGRES_DB" "${postgres_db:-arai_stack}"
  upsert_env_value "$ef" "POSTGRES_USER" "${postgres_user:-arai_stack}"
  upsert_env_value "$ef" "POSTGRES_PASSWORD" "${postgres_password:-$(generate_secret 12)}"
  upsert_env_value "$ef" "JWT_SECRET_KEY" "${jwt_secret:-$(generate_secret 32)}"
  upsert_env_value "$ef" "JWT_ALGORITHM" "${jwt_algorithm:-HS256}"
  upsert_env_value "$ef" "RUNTIME_EXEC_BACKEND" "${runtime_backend:-docker}"
  upsert_env_value "$ef" "RUNTIME_WORKSPACES_HOST_DIR" "$(instance_workspaces_dir "$inst")"
}

sql_quote_identifier() {
  local value="$1"
  value="${value//\"/\"\"}"
  printf "\"%s\"" "$value"
}

file_sha256() {
  local file="$1"
  if require_cmd shasum; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif require_cmd openssl; then
    openssl dgst -sha256 "$file" | awk '{print $NF}'
  else
    echo ""
  fi
}

load_instance_db_credentials() {
  local inst="$1"
  local ef
  ef="$(instance_env_file "$inst")"
  DB_NAME="$(read_env_value "$ef" "POSTGRES_DB" || true)"
  DB_USER="$(read_env_value "$ef" "POSTGRES_USER" || true)"
  DB_PASSWORD="$(read_env_value "$ef" "POSTGRES_PASSWORD" || true)"

  if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_PASSWORD" ]]; then
    error "Missing DB credentials in '$ef'."
    return 1
  fi
  return 0
}

ensure_instance_postgres_ready() {
  local inst="$1"
  if ! compose_instance "$inst" ps --services --status running 2>/dev/null | grep -q '^postgres$'; then
    error "Postgres for '$inst' is not running."
    return 1
  fi

  if ! compose_instance "$inst" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
    psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" >/dev/null 2>&1; then
    error "Cannot connect to Postgres for '$inst'."
    return 1
  fi
  return 0
}

get_instance_backups() {
  local inst="$1"
  local dir
  dir="$(instance_backup_dir "$inst")"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -type f -name "*.sql.gz" | sort -r
}

pick_backup_interactive() {
  local inst="$1"
  local title="$2"
  local backups=()
  while IFS= read -r file; do [[ -n "$file" ]] && backups+=("$file"); done < <(get_instance_backups "$inst")

  if [[ ${#backups[@]} -eq 0 ]]; then
    warn "No backups found for '$inst' in $(instance_backup_dir "$inst")."
    return 1
  fi

  local options=()
  local file
  for file in "${backups[@]}"; do
    options+=("$(basename "$file")")
  done
  options+=("⬅️  Go Back")

  select_option "$title" "${options[@]}"
  local idx=$?
  if [[ $idx -eq ${#backups[@]} ]]; then
    return 1
  fi

  echo "${backups[$idx]}" > "$TMP_PICK"
  return 0
}

verify_backup_checksum() {
  local backup_file="$1"
  local checksum_file="${backup_file}.sha256"
  [[ -f "$checksum_file" ]] || return 0

  local expected actual
  expected="$(awk '{print $1}' "$checksum_file" | head -n 1)"
  actual="$(file_sha256 "$backup_file")"

  if [[ -z "$expected" || -z "$actual" || "$expected" != "$actual" ]]; then
    error "Checksum mismatch for $(basename "$backup_file")."
    return 1
  fi
  return 0
}

select_option() {
  local title="$1"
  shift
  local options=("$@")
  local current=0
  local last=$(( ${#options[@]} - 1 ))
  local key

  echo -n "$CURSOR_OFF"
  while true; do
    echo -n "$GOTO_TOP"
    print_header_content
    build_status_header
    print_menu_note
    printf "${BOLD}%s${RESET}${CLEAR_LINE}\n" "$title"
    for i in "${!options[@]}"; do
      if [[ $i -eq $current ]]; then
        printf "${CLEAR_LINE}  ${BG_BLUE}${BOLD} ❯ %-30s ${RESET}\n" "${options[$i]}"
      else
        printf "${CLEAR_LINE}    %-30s \n" "${options[$i]}"
      fi
    done
    printf "${CLEAR_LINE}\n"

    read -rsn1 key < /dev/tty
    if [[ $key == $'\e' ]]; then
      read -rsn2 key < /dev/tty
      if [[ $key == "[A" ]]; then ((current--)); [[ $current -lt 0 ]] && current=$last
      elif [[ $key == "[B" ]]; then ((current++)); [[ $current -gt $last ]] && current=0; fi
    elif [[ $key == "" ]]; then echo -n "$CURSOR_ON"; return "$current"; fi
  done
}

pick_instance_interactive() {
  local instances=()
  while IFS= read -r line; do [[ -n "$line" ]] && instances+=("$line"); done < <(get_instances)
  
  if [[ ${#instances[@]} -eq 0 ]]; then
    warn "No instances found."
    return 1
  fi
  
  if [[ ${#instances[@]} -eq 1 ]]; then
    echo "${instances[0]}" > "$TMP_PICK"
    return 0
  fi

  local options=("${instances[@]}" "⬅️  Go Back")
  select_option "CHOOSE INSTANCE" "${options[@]}"
  local idx=$?
  
  if [[ $idx -eq ${#instances[@]} ]]; then
    return 1
  fi
  
  echo "${instances[$idx]}" > "$TMP_PICK"
  return 0
}

compose_instance() {
  local inst="$1"
  shift
  docker compose --project-name "$(instance_project_name "$inst")" --env-file "$(instance_env_file "$inst")" "$@"
}

compose_instance_dev() {
  local inst="$1"
  shift
  local project_name
  project_name="$(instance_project_name "$inst")"
  local shared_pg_volume="${project_name}_pgdata"

  docker compose \
    -f docker-compose.dev.yml \
    -f <(cat <<EOF
volumes:
  pgdata_dev:
    name: ${shared_pg_volume}
EOF
) \
    --project-name "$project_name" \
    --env-file "$(instance_env_file "$inst")" \
    "$@"
}

trim_lower() {
  echo "$1" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | tr '[:upper:]' '[:lower:]'
}

hash_auth_secret() {
  local plain="$1"
  if ! require_cmd python3; then
    error "python3 is required for password hashing."
    return 1
  fi
  AUTH_PASSWORD="$plain" python3 - <<'PY'
import hashlib
import os
import secrets

password = os.environ["AUTH_PASSWORD"]
salt = secrets.token_hex(16)
digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
print(f"{salt}${digest.hex()}")
PY
}

sql_quote_literal() {
  local value="$1"
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

auth_sql() {
  local auth_user_lit="$1"
  local auth_hash_lit="$2"
  local sql=""
  sql+="WITH up AS (UPDATE system_settings SET value = ${auth_user_lit} WHERE key = 'sentinel.auth.username' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'sentinel.auth.username', ${auth_user_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  sql+="WITH up AS (UPDATE system_settings SET value = ${auth_hash_lit} WHERE key = 'sentinel.auth.password_hash' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'sentinel.auth.password_hash', ${auth_hash_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  echo "$sql"
}

apply_auth_managed_instance() {
  local inst="$1"
  local username_raw="$2"
  local password_raw="$3"
  local compose_runner="${4:-compose_instance}"
  local ef="$(instance_env_file "$inst")"
  local db_name="$(read_env_value "$ef" "POSTGRES_DB" || true)"
  local db_user="$(read_env_value "$ef" "POSTGRES_USER" || true)"
  local db_password="$(read_env_value "$ef" "POSTGRES_PASSWORD" || true)"

  if [[ -z "$db_name" || -z "$db_user" || -z "$db_password" ]]; then
    error "Missing DB credentials in '$ef'."
    return 1
  fi

  local username="$(trim_lower "$username_raw")"
  local password="$(echo "$password_raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [[ -z "$username" || -z "$password" ]]; then
    error "Username/password cannot be empty."
    return 1
  fi

  local password_hash
  password_hash="$(hash_auth_secret "$password")" || return 1
  local auth_user_lit auth_hash_lit
  auth_user_lit="$(sql_quote_literal "$username")"
  auth_hash_lit="$(sql_quote_literal "$password_hash")"
  local sql
  sql="$(auth_sql "$auth_user_lit" "$auth_hash_lit")"

  local last_error=""
  for _ in {1..30}; do
    local output
    if output="$(
      "$compose_runner" "$inst" exec -T postgres env PGPASSWORD="$db_password" \
        psql -X -v ON_ERROR_STOP=1 -U "$db_user" -d "$db_name" -c "$sql" 2>&1
    )"; then
      success "Auth credentials updated in DB."
      return 0
    fi
    last_error="$(echo "$output" | tail -n 3 | tr '\n' ' ')"
    sleep 1
  done

  error "Could not update auth credentials for '$inst'. Ensure DB is up and schema is initialized."
  [[ -n "$last_error" ]] && warn "Last DB error: $last_error"
  return 1
}

apply_auth_custom_instance() {
  local pg_host="$1"
  local pg_port="$2"
  local pg_db="$3"
  local pg_user="$4"
  local pg_password="$5"
  local pg_sslmode="$6"
  local username_raw="$7"
  local password_raw="$8"

  local username="$(trim_lower "$username_raw")"
  local password="$(echo "$password_raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [[ -z "$username" || -z "$password" ]]; then
    error "Username/password cannot be empty."
    return 1
  fi

  local password_hash
  password_hash="$(hash_auth_secret "$password")" || return 1
  local auth_user_lit auth_hash_lit
  auth_user_lit="$(sql_quote_literal "$username")"
  auth_hash_lit="$(sql_quote_literal "$password_hash")"
  local sql
  sql="$(auth_sql "$auth_user_lit" "$auth_hash_lit")"

  local host="$pg_host"
  if [[ "$host" == "localhost" || "$host" == "127.0.0.1" ]]; then
    host="host.docker.internal"
  fi

  if docker run --rm --add-host host.docker.internal:host-gateway -e PGPASSWORD="$pg_password" \
    postgres:16-alpine psql -X -v ON_ERROR_STOP=1 \
    "host=$host port=$pg_port dbname=$pg_db user=$pg_user sslmode=$pg_sslmode" \
    -c "$sql" >/dev/null; then
    success "Auth credentials updated in DB."
    return 0
  fi

  error "Could not update custom instance credentials. Verify DB connectivity and schema."
  return 1
}

action_up() {
  ensure_docker_ready || return 0
  local inst="${1:-}"
  local seed_user="${2:-}"
  local seed_password="${3:-}"
  local compose_runner="${4:-compose_instance}"
  local mode_label="${5:-}"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0

  info "${ICON_START} Launching '$inst'${mode_label:+ (${mode_label})}..."
  if ! ensure_supported_runtime_backend "$inst"; then
    return 0
  fi
  if [[ "$(current_instance_backend "$inst")" == "qemu" ]]; then
    if ! ensure_qemu_bridge_running "$inst"; then
      error "Cannot start '$inst' without a healthy QEMU bridge."
      return 0
    fi
  fi
  "$compose_runner" "$inst" build sentinel-runtime 2>/dev/null || true
  if "$compose_runner" "$inst" up --build -d; then
    success "'$inst' is running."
    invalidate_status_cache
    local seed_status="not_requested"
    if [[ -n "$seed_user" && -n "$seed_password" ]]; then
      info "Initializing auth credentials in DB..."
      if apply_auth_managed_instance "$inst" "$seed_user" "$seed_password" "$compose_runner"; then
        seed_status="ok"
      else
        seed_status="failed"
      fi
    fi

    local ef="$(instance_env_file "$inst")"
    local p="$(read_env_value "$ef" "STACK_PORT" || echo "4747")"

    printf "\n${CYAN}${BOLD}🚀  S T A C K   R E A D Y${RESET}\n"
    printf "${DIM}---------------------------------------${RESET}\n"
    printf "1. Open Sentinel: ${MAGENTA}http://localhost:$p/${RESET}\n"
    if [[ -n "$seed_user" && "$seed_status" == "ok" ]]; then
      printf "2. Log in with your admin credentials.\n"
      printf "   👤 Username: ${YELLOW}${BOLD}%s${RESET}\n" "$(trim_lower "$seed_user")"
    elif [[ -n "$seed_user" && "$seed_status" == "failed" ]]; then
      printf "2. Auth initialization failed; use existing credentials or run ${BOLD}Instance Config → Reset Auth${RESET}.\n"
    else
      printf "2. Log in with your existing credentials.\n"
    fi
    printf "${DIM}---------------------------------------${RESET}\n"

    local ready_note
    ready_note="${GREEN}${inst}${RESET} running at ${BOLD}http://localhost:${p}/${RESET}"
    if [[ -n "$seed_user" && "$seed_status" == "ok" ]]; then
      ready_note+=$'\n'"Admin: ${BOLD}$(trim_lower "$seed_user")${RESET}"
    elif [[ -n "$seed_user" && "$seed_status" == "failed" ]]; then
      ready_note+=$'\n'"${YELLOW}Auth initialization failed.${RESET} Use ${BOLD}Instance Config → Reset Auth${RESET}."
    fi
    set_menu_note "$ready_note"
  else
    error "Failed to start '$inst'."
    set_menu_note "${RED}Failed to start '${inst}'.${RESET}"
  fi
  return 0
}

action_create() {
  local inst="${1:-}"
  echo -n "$CURSOR_ON"

  if [[ -z "$inst" ]]; then
    local suggested_name="main"
    local raw_name
    raw_name="$(prompt_default "Instance Name" "$suggested_name")"
    inst="$(sanitize_instance_name "$raw_name")"
  fi

  if [[ -z "$inst" ]]; then
    warn "Instance name cannot be empty."
    set_menu_note "${YELLOW}Instance name cannot be empty.${RESET}"
    return 0
  fi

  local ef
  ef="$(instance_env_file "$inst")"
  if [[ -f "$ef" ]]; then
    warn "Instance '$inst' already exists. Use Instance Config to edit it."
    set_menu_note "${YELLOW}Instance '${inst}' already exists.${RESET} Use ${BOLD}Instance Config${RESET} to edit it."
    echo -n "$CURSOR_OFF"
    return 0
  fi

  write_instance_defaults "$inst"

  local stack_port postgres_db postgres_user postgres_password
  stack_port="$(prompt_default "Stack Port" "$(read_env_value "$ef" "STACK_PORT" || echo 4747)")"
  postgres_db="$(prompt_default "Postgres DB Name" "$(read_env_value "$ef" "POSTGRES_DB" || echo arai_stack)")"
  postgres_user="$(prompt_default "Postgres User" "$(read_env_value "$ef" "POSTGRES_USER" || echo arai_stack)")"
  postgres_password="$(prompt_default "Postgres Password" "$(read_env_value "$ef" "POSTGRES_PASSWORD" || generate_secret 12)")"

  upsert_env_value "$ef" "STACK_PORT" "$stack_port"
  upsert_env_value "$ef" "POSTGRES_DB" "$postgres_db"
  upsert_env_value "$ef" "POSTGRES_USER" "$postgres_user"
  upsert_env_value "$ef" "POSTGRES_PASSWORD" "$postgres_password"
  upsert_env_value "$ef" "RUNTIME_WORKSPACES_HOST_DIR" "$(instance_workspaces_dir "$inst")"

  success "Instance '$inst' created."
  info "Current runtime backend: $(backend_label "$(current_instance_backend "$inst")")"
  invalidate_status_cache
  set_menu_note "${GREEN}Instance '${inst}' created.${RESET} Runtime backend: ${BOLD}$(backend_label "$(current_instance_backend "$inst")")${RESET}"
  echo -n "$CURSOR_OFF"
  return 0
}

action_edit_instance() {
  local inst="$1"
  [[ -z "$inst" ]] && return 0

  local ef
  ef="$(instance_env_file "$inst")"
  write_instance_defaults "$inst"

  echo -n "$CURSOR_ON"
  local stack_port postgres_db postgres_user postgres_password
  stack_port="$(prompt_default "Stack Port" "$(read_env_value "$ef" "STACK_PORT" || echo 4747)")"
  postgres_db="$(prompt_default "Postgres DB Name" "$(read_env_value "$ef" "POSTGRES_DB" || echo arai_stack)")"
  postgres_user="$(prompt_default "Postgres User" "$(read_env_value "$ef" "POSTGRES_USER" || echo arai_stack)")"
  postgres_password="$(prompt_default "Postgres Password" "$(read_env_value "$ef" "POSTGRES_PASSWORD" || generate_secret 12)")"

  upsert_env_value "$ef" "STACK_PORT" "$stack_port"
  upsert_env_value "$ef" "POSTGRES_DB" "$postgres_db"
  upsert_env_value "$ef" "POSTGRES_USER" "$postgres_user"
  upsert_env_value "$ef" "POSTGRES_PASSWORD" "$postgres_password"
  upsert_env_value "$ef" "RUNTIME_WORKSPACES_HOST_DIR" "$(instance_workspaces_dir "$inst")"

  success "Instance '$inst' updated."
  invalidate_status_cache
  set_menu_note "${GREEN}Instance '${inst}' updated.${RESET} Port ${BOLD}${stack_port}${RESET} • Backend ${BOLD}$(backend_label "$(current_instance_backend "$inst")")${RESET}"
  echo -n "$CURSOR_OFF"
  return 0
}

action_instance_runtime_backend() {
  local inst="$1"
  [[ -z "$inst" ]] && return 0

  local ef
  ef="$(instance_env_file "$inst")"
  write_instance_defaults "$inst"

  local current_backend
  current_backend="$(current_instance_backend "$inst")"
  local options=(
    "Docker"
    "QEMU"
    "Custom SSH"
    "⬅️  Back"
  )

  while true; do
    echo -n "$CLEAR_SCREEN"
    current_backend="$(current_instance_backend "$inst")"
    select_option "RUNTIME BACKEND: $inst ($(backend_label "$current_backend"))" "${options[@]}"
    local choice=$?
    printf "\n\n"

    case "$choice" in
      0)
        upsert_env_value "$ef" "RUNTIME_EXEC_BACKEND" "docker"
        upsert_env_value "$ef" "RUNTIME_WORKSPACES_HOST_DIR" "$(instance_workspaces_dir "$inst")"
        invalidate_status_cache
        set_menu_note "${GREEN}${inst}${RESET} now uses ${BOLD}Docker${RESET} for runtimes."
        ;;
      1)
        local qemu_image_path qemu_key_path
        qemu_image_path="$(default_qemu_image_path)"
        qemu_key_path="$(default_qemu_key_path)"
        if [[ -z "$qemu_image_path" || -z "$qemu_key_path" ]]; then
          warn "QEMU baked image or SSH key is missing under $ROOT_DIR/qemu/output."
          set_menu_note "${YELLOW}Build the QEMU image first from ${BOLD}./qemu/build-base-image.sh${RESET}${YELLOW}.${RESET}"
        else
          upsert_env_value "$ef" "RUNTIME_EXEC_BACKEND" "qemu"
          upsert_env_value "$ef" "RUNTIME_QEMU_IMAGE" "$qemu_image_path"
          upsert_env_value "$ef" "RUNTIME_QEMU_SSH_KEY_PATH" "/data/runtime/qemu-output/$(basename "$qemu_key_path")"
          upsert_env_value "$ef" "RUNTIME_QEMU_WORKSPACE_ROOT" "$(instance_workspaces_dir "$inst")"
          upsert_env_value "$ef" "RUNTIME_QEMU_RUN_ROOT" "$(instance_qemu_run_dir "$inst")"
          invalidate_status_cache
          set_menu_note "${GREEN}${inst}${RESET} now uses ${BOLD}QEMU${RESET} with baked image ${BOLD}$(basename "$qemu_image_path")${RESET}."
        fi
        ;;
      2)
        echo -n "$CURSOR_ON"
        local remote_host remote_port remote_user remote_key remote_workspace
        remote_host="$(prompt_default "Remote SSH Host" "$(read_env_value "$ef" "RUNTIME_SSH_HOST" || echo localhost)")"
        remote_port="$(prompt_default "Remote SSH Port" "$(read_env_value "$ef" "RUNTIME_SSH_PORT" || echo 22)")"
        remote_user="$(prompt_default "Remote SSH User" "$(read_env_value "$ef" "RUNTIME_SSH_USER" || echo sentinel)")"
        remote_key="$(prompt_default "Remote SSH Key Path" "$(read_env_value "$ef" "RUNTIME_SSH_KEY_PATH" || true)")"
        remote_workspace="$(prompt_default "Remote Workspace Path" "$(read_env_value "$ef" "RUNTIME_SSH_WORKSPACE" || echo /home/sentinel/workspace)")"
        echo -n "$CURSOR_OFF"

        if [[ -z "$remote_host" ]]; then
          warn "Remote SSH host cannot be empty."
          set_menu_note "${YELLOW}Remote SSH host cannot be empty.${RESET}"
        else
          upsert_env_value "$ef" "RUNTIME_EXEC_BACKEND" "remote"
          upsert_env_value "$ef" "RUNTIME_SSH_HOST" "$remote_host"
          upsert_env_value "$ef" "RUNTIME_SSH_PORT" "$remote_port"
          upsert_env_value "$ef" "RUNTIME_SSH_USER" "$remote_user"
          upsert_env_value "$ef" "RUNTIME_SSH_KEY_PATH" "$remote_key"
          upsert_env_value "$ef" "RUNTIME_SSH_WORKSPACE" "$remote_workspace"
          invalidate_status_cache
          set_menu_note "${GREEN}${inst}${RESET} now uses ${BOLD}Custom SSH${RESET} for runtimes."
        fi
        ;;
      3)
        return 0
        ;;
    esac
  done
}

action_instance_config() {
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0

  local options=(
    "✏️  Edit Instance"
    "🧩  Runtime Backend"
    "🔐  Reset Auth"
    "📜  Tail Logs"
    "🗑️   Delete Instance"
    "⬅️  Back"
  )

  while true; do
    echo -n "$CLEAR_SCREEN"
    local ef backend port
    ef="$(instance_env_file "$inst")"
    backend="$(backend_label "$(current_instance_backend "$inst")")"
    port="$(read_env_value "$ef" "STACK_PORT" || echo "4747")"
    select_option "INSTANCE CONFIG: $inst (${backend} • :${port})" "${options[@]}"
    local choice=$?
    printf "\n\n"

    case "$choice" in
      0) action_edit_instance "$inst" ;;
      1) action_instance_runtime_backend "$inst" ;;
      2) action_reset_auth_managed "$inst" ;;
      3) action_logs "$inst" ;;
      4)
        action_delete "$inst"
        if [[ ! -f "$(instance_env_file "$inst")" ]]; then
          return 0
        fi
        ;;
      5) return 0 ;;
    esac
  done
}

action_advanced_mode() {
  local options=(
    "${ICON_START}  Start Instance (Dev Mode)"
    "🧩  Manage Custom Instance Auth"
    "⬅️  Back"
  )

  while true; do
    echo -n "$CLEAR_SCREEN"
    select_option "ADVANCED MODE" "${options[@]}"
    local choice=$?

    printf "\n\n"
    case "$choice" in
      0) action_up "" "" "" "compose_instance_dev" "dev mode" ;;
      1) action_manage_custom_auth ;;
      2) return 0 ;;
    esac
  done
}

action_down() {
  ensure_docker_ready || return 0
  local inst="${1:-}"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0
  
  info "${ICON_STOP} Stopping '$inst'..."
  if compose_instance "$inst" down; then
    stop_qemu_bridge "$inst"
    success "Stopped."
    invalidate_status_cache
    set_menu_note "${GREEN}${inst}${RESET} stopped."
  else
    error "Failed to stop '$inst'."
    set_menu_note "${RED}Failed to stop '${inst}'.${RESET}"
  fi
  return 0
}

action_list() {
  local instances=()
  while IFS= read -r line; do [[ -n "$line" ]] && instances+=("$line"); done < <(get_instances)
  
  if [[ ${#instances[@]} -eq 0 ]]; then
    info "No instances found. Use 'New Instance' to get started."
    return 0
  fi

  printf "\n${BOLD}GLOBAL STATUS${RESET}\n"
  local docker_ok=true
  docker info >/dev/null 2>&1 || docker_ok=false

  for inst in "${instances[@]}"; do
    local state="${RED}STOPPED${RESET}"
    local running="0"
    if [[ "$docker_ok" == "true" ]]; then
      running="$(count_running_instance_services "$inst")"
      [[ "$running" -gt 0 ]] && state="${GREEN}RUNNING${RESET}"
    else
      state="${YELLOW}DOCKER OFF${RESET}"
    fi
    printf "  • %-15s [%b] (%s services active)\n" "$inst" "$state" "$running"
  done
  return 0
}

action_logs() {
  ensure_docker_ready || return 0
  local inst="${1:-}"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0
  
  info "Attaching to logs for '$inst' (Ctrl+C to detach)..."
  compose_instance "$inst" logs -f
  set_menu_note "${DIM}Stopped tailing logs for ${inst}.${RESET}"
  return 0
}

action_reset_auth_managed() {
  ensure_docker_ready || return 0
  local inst="${1:-}"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0

  echo -n "$CURSOR_ON"
  printf "\n${CYAN}RESET INSTANCE AUTH${RESET}\n"
  local auth_user auth_password
  auth_user="$(prompt_default "Admin Username" "admin")"
  auth_password="$(prompt_default "Admin Password" "$(generate_secret 12)")"

  if apply_auth_managed_instance "$inst" "$auth_user" "$auth_password"; then
    success "Auth reset completed for '$inst'."
    set_menu_note "${GREEN}Auth reset completed for ${inst}.${RESET} Admin: ${BOLD}$(trim_lower "$auth_user")${RESET}"
  fi
  return 0
}

action_db_backup_create() {
  ensure_docker_ready || return 0
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0

  load_instance_db_credentials "$inst" || return 0
  ensure_instance_postgres_ready "$inst" || return 0

  local backup_dir ts backup_file temp_file checksum
  backup_dir="$(instance_backup_dir "$inst")"
  mkdir -p "$backup_dir"
  ts="$(date +%Y%m%d-%H%M%S)"
  backup_file="${backup_dir}/${ts}_${inst}.sql.gz"
  temp_file="${backup_file}.tmp"

  info "Creating DB backup for '$inst'..."
  if compose_instance "$inst" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
      pg_dump -U "$DB_USER" "$DB_NAME" | gzip -c > "$temp_file"; then
    mv "$temp_file" "$backup_file"
    checksum="$(file_sha256 "$backup_file")"
    if [[ -n "$checksum" ]]; then
      printf "%s  %s\n" "$checksum" "$(basename "$backup_file")" > "${backup_file}.sha256"
    fi
    success "Backup created: $backup_file"
    set_menu_note "${GREEN}Backup created for ${inst}.${RESET} $(basename "$backup_file")"
  else
    rm -f "$temp_file"
    error "Backup failed for '$inst'."
    set_menu_note "${RED}Backup failed for ${inst}.${RESET}"
  fi
  return 0
}

action_db_backup_list() {
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0

  local backups=()
  while IFS= read -r file; do [[ -n "$file" ]] && backups+=("$file"); done < <(get_instance_backups "$inst")
  if [[ ${#backups[@]} -eq 0 ]]; then
    info "No backups found for '$inst' in $(instance_backup_dir "$inst")."
    set_menu_note "${DIM}No backups found for ${inst}.${RESET}"
    return 0
  fi

  local summary
  summary="${BOLD}Backups for ${inst}:${RESET}"
  local file size mod
  for file in "${backups[@]}"; do
    size="$(du -h "$file" | awk '{print $1}')"
    mod="$(date -r "$file" "+%Y-%m-%d %H:%M:%S")"
    summary+=$'\n'"• $(basename "$file")  ${size}  ${mod}"
  done
  set_menu_note "$summary"
  return 0
}

action_db_backup_restore() {
  ensure_docker_ready || return 0
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0

  rm -f "$TMP_PICK"
  if ! pick_backup_interactive "$inst" "RESTORE BACKUP"; then
    return 0
  fi
  local backup_file
  backup_file="$(cat "$TMP_PICK")"

  load_instance_db_credentials "$inst" || return 0
  ensure_instance_postgres_ready "$inst" || return 0
  verify_backup_checksum "$backup_file" || return 0

  warn "This will replace DB '$DB_NAME' for instance '$inst'."
  warn "Backup selected: $(basename "$backup_file")"
  read -r -p "Type RESTORE to confirm: " confirm < /dev/tty
  [[ "$confirm" != "RESTORE" ]] && { info "Restore aborted."; set_menu_note "${DIM}Restore aborted for ${inst}.${RESET}"; return 0; }

  local db_lit db_ident user_ident
  db_lit="$(sql_quote_literal "$DB_NAME")"
  db_ident="$(sql_quote_identifier "$DB_NAME")"
  user_ident="$(sql_quote_identifier "$DB_USER")"

  info "Terminating active DB sessions..."
  if ! compose_instance "$inst" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
    psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = ${db_lit} AND pid <> pg_backend_pid();" >/dev/null; then
    error "Failed to terminate active sessions."
    return 0
  fi

  info "Recreating database '$DB_NAME'..."
  if ! compose_instance "$inst" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
    psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS ${db_ident};" \
    -c "CREATE DATABASE ${db_ident} OWNER ${user_ident};" >/dev/null; then
    error "Failed to recreate database '$DB_NAME'."
    return 0
  fi

  info "Restoring from $(basename "$backup_file")..."
  if gunzip -c "$backup_file" | compose_instance "$inst" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
    psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" >/dev/null; then
    success "Restore completed for '$inst'."
    set_menu_note "${GREEN}Restore completed for ${inst}.${RESET} Source: $(basename "$backup_file")"
  else
    error "Restore failed for '$inst'."
    set_menu_note "${RED}Restore failed for ${inst}.${RESET}"
    return 0
  fi

  if compose_instance "$inst" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
      psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" >/dev/null 2>&1; then
    success "Post-restore DB check passed."
  else
    warn "Post-restore DB check failed."
  fi
  return 0
}

action_db_backup_delete() {
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0

  rm -f "$TMP_PICK"
  if ! pick_backup_interactive "$inst" "DELETE BACKUP"; then
    return 0
  fi
  local backup_file
  backup_file="$(cat "$TMP_PICK")"

  warn "Delete backup $(basename "$backup_file")?"
  read -r -p "Type DELETE to confirm: " confirm < /dev/tty
  if [[ "$confirm" == "DELETE" ]]; then
    rm -f "$backup_file" "${backup_file}.sha256"
    success "Backup deleted."
    set_menu_note "${GREEN}Deleted backup for ${inst}.${RESET} $(basename "$backup_file")"
  else
    info "Delete aborted."
    set_menu_note "${DIM}Backup delete aborted for ${inst}.${RESET}"
  fi
  return 0
}

action_db_backups_menu() {
  local options=(
    "📦  Backup Current Instance DB"
    "🧾  List Backups"
    "♻️  Restore Backup"
    "🗑️   Delete Backup"
    "⬅️  Back"
  )

  while true; do
    echo -n "$CLEAR_SCREEN"
    select_option "DATABASE BACKUPS" "${options[@]}"
    local choice=$?

    printf "\n\n"
    case "$choice" in
      0) action_db_backup_create ;;
      1) action_db_backup_list ;;
      2) action_db_backup_restore ;;
      3) action_db_backup_delete ;;
      4) return 0 ;;
    esac
  done
}

action_manage_custom_auth() {
  ensure_docker_ready || return 0
  echo -n "$CURSOR_ON"
  printf "\n${CYAN}MANAGE CUSTOM INSTANCE AUTH${RESET}\n"
  printf "${DIM}Provide direct PostgreSQL connection values.${RESET}\n"

  local pg_host pg_port pg_db pg_user pg_sslmode pg_password
  pg_host="$(prompt_default "Postgres Host" "localhost")"
  pg_port="$(prompt_default "Postgres Port" "5432")"
  pg_db="$(prompt_default "Postgres DB Name" "arai_stack")"
  pg_user="$(prompt_default "Postgres User" "arai_stack")"
  pg_sslmode="$(prompt_default "SSL Mode (disable|prefer|require)" "prefer")"

  printf "%s" "$CURSOR_ON"
  read -r -s -p "${BOLD}Postgres Password${RESET}: " pg_password < /dev/tty
  printf "\n%s" "$CURSOR_OFF"
  if [[ -z "$pg_password" ]]; then
    warn "Empty DB password; aborting."
    return 0
  fi

  local auth_user auth_password
  auth_user="$(prompt_default "Admin Username" "admin")"
  auth_password="$(prompt_default "Admin Password" "$(generate_secret 12)")"

  apply_auth_custom_instance \
    "$pg_host" "$pg_port" "$pg_db" "$pg_user" "$pg_password" "$pg_sslmode" \
    "$auth_user" "$auth_password"
  return 0
}

action_delete() {
  local inst="${1:-}"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0
  
  warn "This will delete '$inst' config, workspaces, and data volumes."
  read -r -p "Type DELETE to confirm: " confirm < /dev/tty
  if [[ "$confirm" == "DELETE" ]]; then
    if docker info >/dev/null 2>&1; then
      compose_instance "$inst" down -v --remove-orphans || true
    fi
    stop_qemu_bridge "$inst"
    rm -rf "$(instance_dir "$inst")"
    success "Deleted."
    invalidate_status_cache
    set_menu_note "${GREEN}Deleted instance '${inst}'.${RESET}"
  else
    info "Aborted."
    set_menu_note "${DIM}Delete aborted for ${inst}.${RESET}"
  fi
  return 0
}

menu_loop() {
  local options=(
    "${ICON_CONFIG}  New Instance"
    "🧭  Instance Config"
    "${ICON_START}  Start Instance"
    "${ICON_STOP}  Stop Instance"
    "🗄️  Database Backups"
    "🛠️  Advanced Mode"
    "🚪  Exit"
  )

  while true; do
    echo -n "$CLEAR_SCREEN"
    select_option "MAIN MENU" "${options[@]}"
    local choice=$?
    
    printf "\n\n"

    case "$choice" in
      0) action_create "" ;;
      1) action_instance_config ;;
      2) action_up "" ;;
      3) action_down ;;
      4) action_db_backups_menu ;;
      5) action_advanced_mode ;;
      6) echo "Goodbye!"; exit 0 ;;
    esac

    while read -r -t 0; do read -r; done < /dev/tty
  done
}

trap "echo -n '$CURSOR_ON'; rm -f '$TMP_PICK'; exit" INT TERM EXIT
menu_loop
