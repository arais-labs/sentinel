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
  local files
  shopt -s nullglob
  files=("$INSTANCES_DIR"/*.env)
  shopt -u nullglob
  [[ ${#files[@]} -eq 0 ]] && return 0
  for file in "${files[@]}"; do basename "$file" .env; done
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

instance_env_file() { echo "$INSTANCES_DIR/${1}.env"; }
instance_project_name() { echo "sentinel-${1}"; }

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
  docker compose -f docker-compose.dev.yml --project-name "$(instance_project_name "$inst")" --env-file "$(instance_env_file "$inst")" "$@"
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

auth_sql_for_target() {
  local target="$1"
  local auth_user_lit="$2"
  local auth_hash_lit="$3"
  local sql=""
  if [[ "$target" == "both" || "$target" == "sentinel" ]]; then
    sql+="WITH up AS (UPDATE system_settings SET value = ${auth_user_lit} WHERE key = 'sentinel.auth.username' RETURNING 1) "
    sql+="INSERT INTO system_settings(key, value) SELECT 'sentinel.auth.username', ${auth_user_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
    sql+="WITH up AS (UPDATE system_settings SET value = ${auth_hash_lit} WHERE key = 'sentinel.auth.password_hash' RETURNING 1) "
    sql+="INSERT INTO system_settings(key, value) SELECT 'sentinel.auth.password_hash', ${auth_hash_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  fi
  if [[ "$target" == "both" || "$target" == "araios" ]]; then
    sql+="WITH up AS (UPDATE system_settings SET value = ${auth_user_lit} WHERE key = 'araios.auth.username' RETURNING 1) "
    sql+="INSERT INTO system_settings(key, value) SELECT 'araios.auth.username', ${auth_user_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
    sql+="WITH up AS (UPDATE system_settings SET value = ${auth_hash_lit} WHERE key = 'araios.auth.password_hash' RETURNING 1) "
    sql+="INSERT INTO system_settings(key, value) SELECT 'araios.auth.password_hash', ${auth_hash_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  fi
  echo "$sql"
}

choose_auth_target() {
  local options=("Both apps (Recommended)" "Sentinel only" "araiOS only" "⬅️  Go Back")
  # Keep menu rendering on the terminal, not in command-substitution output.
  select_option "AUTH TARGET" "${options[@]}" > /dev/tty
  local idx=$?
  case "$idx" in
    0) echo "both"; return 0 ;;
    1) echo "sentinel"; return 0 ;;
    2) echo "araios"; return 0 ;;
    *) return 1 ;;
  esac
}

apply_auth_managed_instance() {
  local inst="$1"
  local target="$2"
  local username_raw="$3"
  local password_raw="$4"
  local compose_runner="${5:-compose_instance}"
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
  sql="$(auth_sql_for_target "$target" "$auth_user_lit" "$auth_hash_lit")"
  if [[ -z "$sql" ]]; then
    error "Invalid auth target."
    return 1
  fi

  local last_error=""
  for _ in {1..30}; do
    local output
    if output="$(
      "$compose_runner" "$inst" exec -T postgres env PGPASSWORD="$db_password" \
        psql -X -v ON_ERROR_STOP=1 -U "$db_user" -d "$db_name" -c "$sql" 2>&1
    )"; then
      success "Auth credentials updated in DB ($target)."
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
  local target="$7"
  local username_raw="$8"
  local password_raw="$9"

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
  sql="$(auth_sql_for_target "$target" "$auth_user_lit" "$auth_hash_lit")"
  if [[ -z "$sql" ]]; then
    error "Invalid auth target."
    return 1
  fi

  local host="$pg_host"
  if [[ "$host" == "localhost" || "$host" == "127.0.0.1" ]]; then
    host="host.docker.internal"
  fi

  if docker run --rm --add-host host.docker.internal:host-gateway -e PGPASSWORD="$pg_password" \
    postgres:16-alpine psql -X -v ON_ERROR_STOP=1 \
    "host=$host port=$pg_port dbname=$pg_db user=$pg_user sslmode=$pg_sslmode" \
    -c "$sql" >/dev/null; then
    success "Custom instance auth credentials updated in DB ($target)."
    return 0
  fi

  error "Could not update custom instance credentials. Verify DB connectivity and schema."
  return 1
}

seed_araios_url_settings_managed_instance() {
  local inst="$1"
  local gateway_port="$2"
  local compose_runner="${3:-compose_instance}"
  local ef="$(instance_env_file "$inst")"
  local db_name="$(read_env_value "$ef" "POSTGRES_DB" || true)"
  local db_user="$(read_env_value "$ef" "POSTGRES_USER" || true)"
  local db_password="$(read_env_value "$ef" "POSTGRES_PASSWORD" || true)"

  if [[ -z "$db_name" || -z "$db_user" || -z "$db_password" ]]; then
    error "Missing DB credentials in '$ef'."
    return 1
  fi

  local sentinel_frontend_url="http://localhost:${gateway_port}/sentinel"
  local araios_frontend_url="http://localhost:${gateway_port}/araios"
  local araios_backend_url="http://araios-backend:9000"

  local sentinel_frontend_lit araios_frontend_lit araios_backend_lit
  sentinel_frontend_lit="$(sql_quote_literal "$sentinel_frontend_url")"
  araios_frontend_lit="$(sql_quote_literal "$araios_frontend_url")"
  araios_backend_lit="$(sql_quote_literal "$araios_backend_url")"

  local sql=""
  sql+="WITH up AS (UPDATE system_settings SET value = ${sentinel_frontend_lit} WHERE key = 'sentinel_frontend_url' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'sentinel_frontend_url', ${sentinel_frontend_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  sql+="WITH up AS (UPDATE system_settings SET value = ${araios_frontend_lit} WHERE key = 'araios_frontend_url' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'araios_frontend_url', ${araios_frontend_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "
  sql+="WITH up AS (UPDATE system_settings SET value = ${araios_backend_lit} WHERE key = 'araios_backend_url' RETURNING 1) "
  sql+="INSERT INTO system_settings(key, value) SELECT 'araios_backend_url', ${araios_backend_lit} WHERE NOT EXISTS (SELECT 1 FROM up); "

  local last_error=""
  for _ in {1..30}; do
    local output
    if output="$(
      "$compose_runner" "$inst" exec -T postgres env PGPASSWORD="$db_password" \
        psql -X -v ON_ERROR_STOP=1 -U "$db_user" -d "$db_name" -c "$sql" 2>&1
    )"; then
      success "AraiOS URL settings seeded in DB."
      return 0
    fi
    last_error="$(echo "$output" | tail -n 3 | tr '\n' ' ')"
    sleep 1
  done

  warn "Could not seed AraiOS URL settings in DB for '$inst'."
  [[ -n "$last_error" ]] && warn "Last DB error: $last_error"
  return 1
}

create_bootstrap_agent_token_managed() {
  local inst="$1"
  local gateway_port="$2"
  local username_raw="$3"
  local password_raw="$4"

  if ! require_cmd curl; then
    warn "curl is required to create bootstrap araiOS agent token."
    return 1
  fi
  if ! require_cmd python3; then
    warn "python3 is required to parse bootstrap araiOS agent token response."
    return 1
  fi

  local username password
  username="$(trim_lower "$username_raw")"
  password="$(echo "$password_raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [[ -z "$username" || -z "$password" ]]; then
    return 1
  fi

  local base_url="http://localhost:${gateway_port}"
  local token_suffix="${RANDOM}${RANDOM}"
  local agent_id="${inst}-bootstrap-${token_suffix}"
  local label="sentinel-${inst}-bootstrap"
  local subject="${inst}-bootstrap"

  local login_payload create_payload
  login_payload="$(
    LOGIN_USERNAME="$username" LOGIN_PASSWORD="$password" python3 - <<'PY'
import json
import os

print(json.dumps({"username": os.environ["LOGIN_USERNAME"], "password": os.environ["LOGIN_PASSWORD"]}))
PY
  )" || return 1
  create_payload="$(
    AGENT_LABEL="$label" AGENT_ID="$agent_id" AGENT_SUBJECT="$subject" python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "label": os.environ["AGENT_LABEL"],
            "agent_id": os.environ["AGENT_ID"],
            "subject": os.environ["AGENT_SUBJECT"],
        }
    )
)
PY
  )" || return 1

  local cookie_jar login_body create_body
  cookie_jar="$(mktemp /tmp/sentinel-cookie.XXXXXX)" || return 1
  login_body="$(mktemp /tmp/sentinel-login-body.XXXXXX)" || {
    rm -f "$cookie_jar"
    return 1
  }
  create_body="$(mktemp /tmp/sentinel-create-body.XXXXXX)" || {
    rm -f "$cookie_jar" "$login_body"
    return 1
  }

  local login_status login_ok="false"
  for _ in {1..40}; do
    login_status="$(
      curl -sS -o "$login_body" -w "%{http_code}" \
        -X POST "${base_url}/platform/auth/login" \
        -H "Content-Type: application/json" \
        --data "$login_payload" \
        --cookie-jar "$cookie_jar" \
        --cookie "$cookie_jar" || true
    )"
    if [[ "$login_status" == "200" ]]; then
      login_ok="true"
      break
    fi
    sleep 1
  done
  if [[ "$login_ok" != "true" ]]; then
    rm -f "$cookie_jar" "$login_body" "$create_body"
    return 1
  fi

  local create_status
  create_status="$(
    curl -sS -o "$create_body" -w "%{http_code}" \
      -X POST "${base_url}/platform/auth/agents" \
      -H "Content-Type: application/json" \
      --data "$create_payload" \
      --cookie "$cookie_jar" \
      --cookie-jar "$cookie_jar" || true
  )"
  if [[ "$create_status" != "201" ]]; then
    rm -f "$cookie_jar" "$login_body" "$create_body"
    return 1
  fi

  local api_key
  api_key="$(
    python3 - "$create_body" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    print("")
    raise SystemExit(0)

value = payload.get("api_key")
print(value.strip() if isinstance(value, str) else "")
PY
  )"
  rm -f "$cookie_jar" "$login_body" "$create_body"

  if [[ -z "$api_key" ]]; then
    return 1
  fi

  printf "%s" "$api_key"
  return 0
}

seed_cross_app_urls_via_api_managed_instance() {
  local gateway_port="$1"
  local username_raw="$2"
  local password_raw="$3"
  local agent_api_key_raw="${4:-}"

  if ! require_cmd curl; then
    warn "curl is required to seed cross-app URL settings."
    return 1
  fi
  if ! require_cmd python3; then
    warn "python3 is required to seed cross-app URL settings."
    return 1
  fi

  local username password agent_api_key
  username="$(trim_lower "$username_raw")"
  password="$(echo "$password_raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  agent_api_key="$(echo "$agent_api_key_raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [[ -z "$username" || -z "$password" ]]; then
    return 1
  fi

  local base_url="http://localhost:${gateway_port}"
  local sentinel_frontend_url="${base_url}/sentinel"
  local araios_frontend_url="${base_url}/araios"
  local araios_backend_url="http://araios-backend:9000"

  local login_payload sentinel_frontend_payload araios_frontend_payload sentinel_payload
  login_payload="$(
    LOGIN_USERNAME="$username" LOGIN_PASSWORD="$password" python3 - <<'PY'
import json
import os

print(json.dumps({"username": os.environ["LOGIN_USERNAME"], "password": os.environ["LOGIN_PASSWORD"]}))
PY
  )" || return 1
  sentinel_frontend_payload="$(
    URL_VALUE="$sentinel_frontend_url" python3 - <<'PY'
import json
import os

print(json.dumps({"value": os.environ["URL_VALUE"]}))
PY
  )" || return 1
  araios_frontend_payload="$(
    URL_VALUE="$araios_frontend_url" python3 - <<'PY'
import json
import os

print(json.dumps({"value": os.environ["URL_VALUE"]}))
PY
  )" || return 1
  if [[ -n "$agent_api_key" ]]; then
    sentinel_payload="$(
      ARAIOS_FRONTEND_URL="$araios_frontend_url" \
      ARAIOS_BACKEND_URL="$araios_backend_url" \
      AGENT_API_KEY="$agent_api_key" \
      python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "enabled": True,
            "araios_frontend_url": os.environ["ARAIOS_FRONTEND_URL"],
            "araios_backend_url": os.environ["ARAIOS_BACKEND_URL"],
            "agent_api_key": os.environ["AGENT_API_KEY"],
        }
    )
)
PY
    )" || return 1
  else
    sentinel_payload="$(
      ARAIOS_FRONTEND_URL="$araios_frontend_url" python3 - <<'PY'
import json
import os

print(json.dumps({"enabled": False, "araios_frontend_url": os.environ["ARAIOS_FRONTEND_URL"]}))
PY
    )" || return 1
  fi

  local araios_login_body sentinel_login_body
  araios_login_body="$(mktemp /tmp/sentinel-araios-login.XXXXXX)" || return 1
  sentinel_login_body="$(mktemp /tmp/sentinel-sentinel-login.XXXXXX)" || {
    rm -f "$araios_login_body"
    return 1
  }

  local araios_login_status araios_login_ok="false"
  for _ in {1..40}; do
    araios_login_status="$(
      curl -sS -o "$araios_login_body" -w "%{http_code}" \
        -X POST "${base_url}/platform/auth/login" \
        -H "Content-Type: application/json" \
        --data "$login_payload" || true
    )"
    if [[ "$araios_login_status" == "200" ]]; then
      araios_login_ok="true"
      break
    fi
    sleep 1
  done
  if [[ "$araios_login_ok" != "true" ]]; then
    rm -f "$araios_login_body" "$sentinel_login_body"
    return 1
  fi

  local araios_access_token
  araios_access_token="$(
    python3 - "$araios_login_body" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    print("")
    raise SystemExit(0)

value = payload.get("access_token")
print(value.strip() if isinstance(value, str) else "")
PY
  )"
  if [[ -z "$araios_access_token" ]]; then
    rm -f "$araios_login_body" "$sentinel_login_body"
    return 1
  fi

  local araios_set_status
  araios_set_status="$(
    curl -sS -o /dev/null -w "%{http_code}" \
      -X PUT "${base_url}/api/settings/sentinel_frontend_url" \
      -H "Authorization: Bearer ${araios_access_token}" \
      -H "Content-Type: application/json" \
      --data "$sentinel_frontend_payload" || true
  )"
  if [[ "$araios_set_status" != "200" ]]; then
    rm -f "$araios_login_body" "$sentinel_login_body"
    return 1
  fi

  araios_set_status="$(
    curl -sS -o /dev/null -w "%{http_code}" \
      -X PUT "${base_url}/api/settings/araios_frontend_url" \
      -H "Authorization: Bearer ${araios_access_token}" \
      -H "Content-Type: application/json" \
      --data "$araios_frontend_payload" || true
  )"
  if [[ "$araios_set_status" != "200" ]]; then
    rm -f "$araios_login_body" "$sentinel_login_body"
    return 1
  fi

  local sentinel_login_status sentinel_login_ok="false"
  for _ in {1..40}; do
    sentinel_login_status="$(
      curl -sS -o "$sentinel_login_body" -w "%{http_code}" \
        -X POST "${base_url}/sentinel/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        --data "$login_payload" || true
    )"
    if [[ "$sentinel_login_status" == "200" ]]; then
      sentinel_login_ok="true"
      break
    fi
    sleep 1
  done
  if [[ "$sentinel_login_ok" != "true" ]]; then
    rm -f "$araios_login_body" "$sentinel_login_body"
    return 1
  fi

  local sentinel_access_token
  sentinel_access_token="$(
    python3 - "$sentinel_login_body" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    print("")
    raise SystemExit(0)

value = payload.get("access_token")
print(value.strip() if isinstance(value, str) else "")
PY
  )"
  if [[ -z "$sentinel_access_token" ]]; then
    rm -f "$araios_login_body" "$sentinel_login_body"
    return 1
  fi

  local sentinel_set_status
  sentinel_set_status="$(
    curl -sS -o /dev/null -w "%{http_code}" \
      -X POST "${base_url}/sentinel/api/v1/settings/araios" \
      -H "Authorization: Bearer ${sentinel_access_token}" \
      -H "Content-Type: application/json" \
      --data "$sentinel_payload" || true
  )"
  rm -f "$araios_login_body" "$sentinel_login_body"
  if [[ "$sentinel_set_status" != "200" ]]; then
    return 1
  fi

  return 0
}

action_create() {
  echo -n "$CURSOR_ON"
  printf "\n${CYAN}SETTING UP NEW INSTANCE${RESET}\n"
  read -r -p "${BOLD}Instance name${RESET} [main]: " inst < /dev/tty
  inst="$(sanitize_instance_name "${inst:-main}")"
  local ef="$(instance_env_file "$inst")"
  
  if [[ -f "$ef" ]]; then
    warn "Instance '$inst' already exists."
    read -r -p "Overwrite configuration? [y/N]: " ov < /dev/tty
    if [[ ! "$ov" =~ ^[Yy]$ ]]; then
      action_up "$inst"
      return 0
    fi
  fi

  local p="$(prompt_default "Gateway Port" "4747")"
  if check_port_occupied "$p"; then
    warn "Port $p is already occupied."
    read -r -p "Continue anyway? [y/N]: " cont < /dev/tty
    [[ ! "$cont" =~ ^[Yy]$ ]] && return 0
  fi

  local db="$(prompt_default "DB Name" "arai_stack")"
  local u="$(prompt_default "DB User" "arai_stack")"
  local pw="$(prompt_default "DB Pass" "$(generate_secret 12)")"
  local jwt="$(prompt_default "JWT Secret" "$(generate_secret 32)")"
  local auth_user="$(prompt_default "Admin Username (both apps)" "admin")"
  local auth_password="$(prompt_default "Admin Password (both apps)" "$(generate_secret 12)")"

  cat > "$ef" <<EOF
STACK_PORT=$p
POSTGRES_DB=$db
POSTGRES_USER=$u
POSTGRES_PASSWORD=$pw
JWT_SECRET_KEY=$jwt
JWT_ALGORITHM=HS256
EOF
  chmod 600 "$ef"
  success "Config saved for '$inst'."
  action_up "$inst" "$auth_user" "$auth_password" "both"
  return 0
}

action_up() {
  ensure_docker_ready || return 0
  local inst="${1:-}"
  local seed_user="${2:-}"
  local seed_password="${3:-}"
  local seed_target="${4:-both}"
  local compose_runner="${5:-compose_instance}"
  local mode_label="${6:-}"
  local seed_status="not_requested"
  local bootstrap_agent_token=""
  local bootstrap_status="not_requested"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0

  info "${ICON_START} Launching '$inst'${mode_label:+ (${mode_label})}..."
  if "$compose_runner" "$inst" up --build -d; then
    success "'$inst' is running."
    if [[ -n "$seed_user" && -n "$seed_password" ]]; then
      info "Initializing auth credentials in DB..."
      if apply_auth_managed_instance "$inst" "$seed_target" "$seed_user" "$seed_password" "$compose_runner"; then
        seed_status="ok"
      else
        seed_status="failed"
      fi
    fi
    
    local ef="$(instance_env_file "$inst")"
    local p="$(read_env_value "$ef" "STACK_PORT" || echo "4747")"
    if [[ -n "$seed_user" && "$seed_status" == "ok" ]]; then
      info "Creating bootstrap araiOS agent token for '$inst'..."
      if bootstrap_agent_token="$(create_bootstrap_agent_token_managed "$inst" "$p" "$seed_user" "$seed_password")"; then
        bootstrap_status="ok"
        success "Bootstrap araiOS agent token created."
      else
        bootstrap_status="failed"
        warn "Could not create bootstrap araiOS agent token automatically."
      fi
    fi
    info "Seeding cross-app URL settings..."
    if [[ -n "$seed_user" && "$seed_status" == "ok" && "$bootstrap_status" == "ok" ]]; then
      if seed_cross_app_urls_via_api_managed_instance "$p" "$seed_user" "$seed_password" "$bootstrap_agent_token"; then
        success "Cross-app URL settings seeded via service APIs."
      else
        warn "Could not seed cross-app URL settings via service APIs. Falling back to DB seeding."
        seed_araios_url_settings_managed_instance "$inst" "$p" "$compose_runner" || true
      fi
    else
      seed_araios_url_settings_managed_instance "$inst" "$p" "$compose_runner" || true
    fi
    
    printf "\n${CYAN}${BOLD}🚀  S T A C K   O N B O A R D I N G${RESET}\n"
    printf "${DIM}---------------------------------------${RESET}\n"
    printf "1. Open the Gateway: ${MAGENTA}http://localhost:$p/${RESET}\n"
    if [[ -n "$seed_user" && "$seed_status" == "ok" ]]; then
      printf "2. Log in with your configured admin credentials.\n"
      printf "   👤 Username: ${YELLOW}${BOLD}%s${RESET}\n" "$(trim_lower "$seed_user")"
    elif [[ -n "$seed_user" && "$seed_status" == "failed" ]]; then
      printf "2. Auth initialization failed; use existing credentials or run ${BOLD}Reset Auth${RESET}.\n"
      printf "   ${YELLOW}Configured username may not be active yet:${RESET} %s\n" "$(trim_lower "$seed_user")"
    else
      printf "2. Log in with your existing DB credentials.\n"
    fi
    if [[ "$bootstrap_status" == "ok" ]]; then
      printf "3. Initial araiOS agent token for this instance (save it now):\n"
      printf "   🔑 ${YELLOW}${BOLD}%s${RESET}\n" "$bootstrap_agent_token"
      printf "4. In Sentinel onboarding, paste it into ${BOLD}AraiOS -> Agent API Key${RESET}.\n"
      printf "5. If needed later, open ${MAGENTA}http://localhost:$p/manage/${RESET} to rotate or create another token.\n"
    else
      printf "3. If no token is available, open ${MAGENTA}http://localhost:$p/araios/${RESET} then manage tokens at ${MAGENTA}http://localhost:$p/manage/${RESET}.\n"
      printf "4. Paste the token into Sentinel onboarding under ${BOLD}AraiOS -> Agent API Key${RESET}.\n"
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
      0) action_up "" "" "" "both" "compose_instance_dev" "dev mode" ;;
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

  local target
  target="$(choose_auth_target)" || return 0

  echo -n "$CURSOR_ON"
  printf "\n${CYAN}RESET MANAGED INSTANCE AUTH${RESET}\n"
  local auth_user auth_password
  auth_user="$(prompt_default "Admin Username" "admin")"
  auth_password="$(prompt_default "Admin Password" "$(generate_secret 12)")"

  if apply_auth_managed_instance "$inst" "$target" "$auth_user" "$auth_password"; then
    success "Managed auth reset completed for '$inst'."
  fi
  return 0
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

  local target
  target="$(choose_auth_target)" || return 0

  local auth_user auth_password
  auth_user="$(prompt_default "Admin Username" "admin")"
  auth_password="$(prompt_default "Admin Password" "$(generate_secret 12)")"

  apply_auth_custom_instance \
    "$pg_host" "$pg_port" "$pg_db" "$pg_user" "$pg_password" "$pg_sslmode" \
    "$target" "$auth_user" "$auth_password"
  return 0
}

action_delete() {
  local inst=""
  rm -f "$TMP_PICK"
  if pick_instance_interactive; then
    inst=$(cat "$TMP_PICK")
  fi
  [[ -z "$inst" ]] && return 0
  
  warn "This will delete '$inst' config and data volumes."
  read -r -p "Type DELETE to confirm: " confirm < /dev/tty
  if [[ "$confirm" == "DELETE" ]]; then
    if docker info >/dev/null 2>&1; then
      compose_instance "$inst" down -v --remove-orphans || true
    fi
    rm -f "$(instance_env_file "$inst")"
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
      4) action_list ;;
      5) action_logs ;;
      6) action_delete ;;
      7) action_advanced_mode ;;
      8) echo "Goodbye!"; exit 0 ;;
    esac
    
    # BUFFER FLUSH: Prevents skipping the "Press Enter" prompt due to trailing characters from Docker
    while read -r -t 0; do read -r; done < /dev/tty
    
    printf "\n${DIM}Press Enter to return to menu...${RESET}"
    read -r _ < /dev/tty
  done
}

trap "echo -n '$CURSOR_ON'; rm -f '$TMP_PICK'; exit" INT TERM EXIT
menu_loop
