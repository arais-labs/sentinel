#!/usr/bin/env bash
set -uo pipefail

# Sentinel Stack CLI - Smooth Transition Edition
# Fixes the buffer-skip glitch and ensures onboarding info is readable.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

INSTANCES_DIR="$ROOT_DIR/.instances"
mkdir -p "$INSTANCES_DIR"
TMP_PICK="/tmp/sentinel_pick_$(id -u)"

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
  sql+="WITH up AS (UPDATE system_settings SET value = ${auth_user_lit} WHERE key = 'araios.auth.username' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'araios.auth.username', ${auth_user_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  sql+="WITH up AS (UPDATE system_settings SET value = ${auth_hash_lit} WHERE key = 'araios.auth.password_hash' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'araios.auth.password_hash', ${auth_hash_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
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
  "$compose_runner" "$inst" build sentinel-runtime 2>/dev/null || true
  if "$compose_runner" "$inst" up --build -d; then
    success "'$inst' is running."
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
      printf "2. Auth initialization failed; use existing credentials or run ${BOLD}Reset Auth${RESET}.\n"
    else
      printf "2. Log in with your existing credentials.\n"
    fi
    printf "${DIM}---------------------------------------${RESET}\n"
  else
    error "Failed to start '$inst'."
  fi
  return 0
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

    while read -r -t 0; do read -r; done < /dev/tty
    printf "\n${DIM}Press Enter to return to Advanced Mode...${RESET}"
    read -r _ < /dev/tty
  done
}

action_down() {
  ensure_docker_ready || return 0
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0
  
  info "${ICON_STOP} Stopping '$inst'..."
  if compose_instance "$inst" down; then
    success "Stopped."
  else
    error "Failed to stop '$inst'."
  fi
  return 0
}

action_list() {
  local instances=()
  while IFS= read -r line; do [[ -n "$line" ]] && instances+=("$line"); done < <(get_instances)
  
  if [[ ${#instances[@]} -eq 0 ]]; then
    info "No instances found. Use 'New/Edit Instance' to get started."
    return 0
  fi

  printf "\n${BOLD}GLOBAL STATUS${RESET}\n"
  local docker_ok=true
  docker info >/dev/null 2>&1 || docker_ok=false

  for inst in "${instances[@]}"; do
    local state="${RED}STOPPED${RESET}"
    local running="0"
    if [[ "$docker_ok" == "true" ]]; then
      running="$(compose_instance "$inst" ps --services --status running 2>/dev/null | wc -l | tr -d ' ')"
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
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0
  
  info "Attaching to logs for '$inst' (Ctrl+C to detach)..."
  compose_instance "$inst" logs -f
  return 0
}

action_reset_auth_managed() {
  ensure_docker_ready || return 0
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0

  echo -n "$CURSOR_ON"
  printf "\n${CYAN}RESET INSTANCE AUTH${RESET}\n"
  local auth_user auth_password
  auth_user="$(prompt_default "Admin Username" "admin")"
  auth_password="$(prompt_default "Admin Password" "$(generate_secret 12)")"

  if apply_auth_managed_instance "$inst" "$auth_user" "$auth_password"; then
    success "Auth reset completed for '$inst'."
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
  else
    rm -f "$temp_file"
    error "Backup failed for '$inst'."
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
    return 0
  fi

  printf "\n${BOLD}BACKUPS FOR %s${RESET}\n" "$inst"
  local file size mod
  for file in "${backups[@]}"; do
    size="$(du -h "$file" | awk '{print $1}')"
    mod="$(date -r "$file" "+%Y-%m-%d %H:%M:%S")"
    printf "  • %-36s  %8s  %s\n" "$(basename "$file")" "$size" "$mod"
  done
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
  [[ "$confirm" != "RESTORE" ]] && { info "Restore aborted."; return 0; }

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
  else
    error "Restore failed for '$inst'."
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
  else
    info "Delete aborted."
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

    while read -r -t 0; do read -r; done < /dev/tty
    printf "\n${DIM}Press Enter to return to Database Backups...${RESET}"
    read -r _ < /dev/tty
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
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0
  
  warn "This will delete '$inst' config, workspaces, and data volumes."
  read -r -p "Type DELETE to confirm: " confirm < /dev/tty
  if [[ "$confirm" == "DELETE" ]]; then
    if docker info >/dev/null 2>&1; then
      compose_instance "$inst" down -v --remove-orphans || true
    fi
    rm -rf "$(instance_dir "$inst")"
    success "Deleted."
  else
    info "Aborted."
  fi
  return 0
}

menu_loop() {
  local options=(
    "${ICON_CONFIG}  New/Edit Instance"
    "${ICON_START}  Start Instance"
    "${ICON_STOP}  Stop Instance"
    "🔐  Reset Auth (Managed Instance)"
    "🗄️  Database Backups"
    "${ICON_LIST}  Global Status"
    "📜  Tail Logs"
    "🗑️   Delete Instance"
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
      1) action_up "" ;;
      2) action_down ;;
      3) action_reset_auth_managed ;;
      4) action_db_backups_menu ;;
      5) action_list ;;
      6) action_logs ;;
      7) action_delete ;;
      8) action_advanced_mode ;;
      9) echo "Goodbye!"; exit 0 ;;
    esac
    
    # BUFFER FLUSH: Prevents skipping the "Press Enter" prompt due to trailing characters from Docker
    while read -r -t 0; do read -r; done < /dev/tty
    
    printf "\n${DIM}Press Enter to return to menu...${RESET}"
    read -r _ < /dev/tty
  done
}

trap "echo -n '$CURSOR_ON'; rm -f '$TMP_PICK'; exit" INT TERM EXIT
menu_loop
