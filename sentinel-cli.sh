#!/usr/bin/env bash
# Sentinel CLI — control stack lifecycle and manage instances.
# Interactive when launched without args; scriptable via subcommands.

set -u
set -o pipefail
# -e intentionally omitted: interactive paths surface errors via menu notes
# instead of exiting. One-shot subcommands check exit codes explicitly.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || { printf 'Failed to enter %s\n' "$ROOT_DIR" >&2; exit 1; }

load_root_env() {
  local force="${1:-false}"
  local env_file="$ROOT_DIR/.env"
  [[ -f "$env_file" ]] || return 0

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    if [[ "$force" == "true" || -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$env_file"
}

load_root_env

# === Configuration ===
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-sentinel}"
SENTINEL_MODE="${SENTINEL_MODE:-prod}"
SENTINEL_COMPOSE_FILE="${SENTINEL_COMPOSE_FILE:-}"
COMPOSE_FILE=""
STACK_PORT="${STACK_PORT:-4747}"
STACK_URL="${SENTINEL_URL:-http://localhost:${STACK_PORT}}"
API_BASE="${STACK_URL%/}/api/v1"
HEALTH_READY_URL="${STACK_URL%/}/health/ready"
READY_TIMEOUT="${SENTINEL_READY_TIMEOUT:-60}"
CACHE_TTL="${SENTINEL_STATUS_TTL:-5}"
SENTINEL_RUNTIME_WORKSPACES_DIR="${SENTINEL_RUNTIME_WORKSPACES_DIR:-}"
SENTINEL_AUTH_USERNAME="${SENTINEL_AUTH_USERNAME:-}"
SENTINEL_AUTH_PASSWORD="${SENTINEL_AUTH_PASSWORD:-}"

# === Terminal capabilities ===
if [[ -t 1 ]]; then
  TTY_OUT=1
  BOLD=$'\033[1m'
  DIM=$'\033[2m'
  RED=$'\033[31m'
  GREEN=$'\033[32m'
  YELLOW=$'\033[33m'
  BLUE=$'\033[34m'
  CYAN=$'\033[36m'
  BG_BLUE=$'\033[44m'
  RESET=$'\033[0m'
  CLEAR_SCREEN=$'\033[2J\033[H'
  GOTO_TOP=$'\033[H'
  CLEAR_TO_END=$'\033[J'
  CLEAR_LINE=$'\033[K'
  CURSOR_OFF=$'\033[?25l'
  CURSOR_ON=$'\033[?25h'
else
  TTY_OUT=0
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; BG_BLUE=""; RESET=""
  CLEAR_SCREEN=""; GOTO_TOP=""; CLEAR_TO_END=""; CLEAR_LINE=""; CURSOR_OFF=""; CURSOR_ON=""
fi

# === Globals ===
TMP_DIR=""
MENU_NOTE=""
STATUS_CACHE_TS=0
STATUS_CACHE_TEXT=""
HEADER_CACHE=""
HAVE_CURL=""
HAVE_DOCKER=""
HAVE_PYTHON=""
CURSOR_HIDDEN=0
LAST_ERROR=""        # Multi-line error panel shown in the next menu frame.
ERROR_TAIL_LINES="${SENTINEL_ERROR_TAIL_LINES:-15}"

# === Icons (Unicode geometric — opinionated, no fallback) ===
# These render in any UTF-8 terminal without requiring a Nerd Font.
ICON_DOCKER="⬢"
ICON_API="◉"
ICON_STACK="▣"
ICON_UI="◧"
ICON_OK="✓"
ICON_INFO="ⓘ"
ICON_WARN="⚠"
ICON_ERROR="✗"
ICON_START="▶"
ICON_STOP="■"
ICON_RESTART="↻"
ICON_RESET="!"
ICON_INSTANCES="⬡"
ICON_STATUS="ⓘ"
ICON_LOGS="≡"
ICON_REFRESH="⟳"
ICON_EXIT="⏻"
ICON_BACK="←"
ICON_LIST="☰"
ICON_CREATE="+"
ICON_RENAME="✎"
ICON_DELETE="✗"
ICON_SELECTED="❯"

# === Cleanup ===
restore_terminal() {
  if [[ $TTY_OUT -eq 1 && $CURSOR_HIDDEN -eq 1 ]]; then
    printf '%s' "$CURSOR_ON"
    CURSOR_HIDDEN=0
  fi
}

cleanup() {
  local code=$?
  restore_terminal
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR" 2>/dev/null || true
  fi
  return $code
}

on_int() {
  cleanup
  printf '\n' >&2
  exit 130
}

on_term() {
  cleanup
  exit 143
}

trap cleanup EXIT
trap on_int INT
trap on_term TERM

ensure_tmp_dir() {
  [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && return 0
  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sentinel-cli.XXXXXX" 2>/dev/null)" || {
    printf 'Failed to create temp directory\n' >&2
    return 1
  }
}

hide_cursor() {
  [[ $TTY_OUT -eq 1 && $CURSOR_HIDDEN -eq 0 ]] || return 0
  printf '%s' "$CURSOR_OFF"
  CURSOR_HIDDEN=1
}

show_cursor() {
  [[ $TTY_OUT -eq 1 && $CURSOR_HIDDEN -eq 1 ]] || return 0
  printf '%s' "$CURSOR_ON"
  CURSOR_HIDDEN=0
}

# === Tool detection ===
detect_tools() {
  command -v curl >/dev/null 2>&1 && HAVE_CURL=1
  command -v docker >/dev/null 2>&1 && HAVE_DOCKER=1
  command -v python3 >/dev/null 2>&1 && HAVE_PYTHON=1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

# === Logging (one-shot output) ===
info() { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"; }
ok() { printf '%s[OK]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
err() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$*" >&2; }
die() { err "$*"; exit 1; }

# === Menu notes (soft errors for interactive paths) ===
ui_note_ok() { MENU_NOTE="${GREEN}${BOLD}${ICON_OK}${RESET}  $*"; }
ui_note_info() { MENU_NOTE="${BLUE}${BOLD}${ICON_INFO}${RESET}  $*"; }
ui_note_warn() { MENU_NOTE="${YELLOW}${BOLD}${ICON_WARN}${RESET}  $*"; }
ui_note_error() { MENU_NOTE="${RED}${BOLD}${ICON_ERROR}${RESET}  $*"; }
ui_note_clear() { MENU_NOTE=""; }

# === Error panel (multi-line, rendered into the next menu frame) ===
ui_clear_error() { LAST_ERROR=""; }

# ui_set_error_from_file <title> <path> [max_lines]
ui_set_error_from_file() {
  local title="$1"
  local file="$2"
  local max="${3:-$ERROR_TAIL_LINES}"
  local panel="${RED}${BOLD}${ICON_ERROR}  ${title}${RESET}${CLEAR_LINE}"$'\n'
  panel+="${DIM}┌─ last ${max} line(s) ─────────────────────────────${RESET}${CLEAR_LINE}"$'\n'
  if [[ -s "$file" ]]; then
    local line
    # tail keeps the most relevant context for the failure.
    while IFS= read -r line; do
      panel+="${DIM}│${RESET} ${line}${CLEAR_LINE}"$'\n'
    done < <(tail -n "$max" "$file" 2>/dev/null)
  else
    panel+="${DIM}│ (no output captured)${RESET}${CLEAR_LINE}"$'\n'
  fi
  panel+="${DIM}└──────────────────────────────────────────────────${RESET}${CLEAR_LINE}"$'\n'
  panel+="${CLEAR_LINE}"$'\n'
  LAST_ERROR="$panel"
}

# ui_set_error_text <title> <text>  (text is treated as a single block)
ui_set_error_text() {
  local title="$1"
  local text="$2"
  local panel="${RED}${BOLD}${ICON_ERROR}  ${title}${RESET}${CLEAR_LINE}"$'\n'
  panel+="${DIM}┌──────────────────────────────────────────────────${RESET}${CLEAR_LINE}"$'\n'
  local line
  while IFS= read -r line; do
    panel+="${DIM}│${RESET} ${line}${CLEAR_LINE}"$'\n'
  done <<< "$text"
  panel+="${DIM}└──────────────────────────────────────────────────${RESET}${CLEAR_LINE}"$'\n'
  panel+="${CLEAR_LINE}"$'\n'
  LAST_ERROR="$panel"
}

build_error_panel() {
  [[ -n "$LAST_ERROR" ]] || return 0
  printf '%s' "$LAST_ERROR"
}

usage() {
  cat <<'EOF'
Sentinel CLI

Usage:
  ./sentinel-cli.sh
  ./sentinel-cli.sh --dev
  ./sentinel-cli.sh up
  ./sentinel-cli.sh --dev up
  ./sentinel-cli.sh down
  ./sentinel-cli.sh restart
  ./sentinel-cli.sh reset [--yes] [--prod-confirm]
  ./sentinel-cli.sh logs [service]
  ./sentinel-cli.sh status
  ./sentinel-cli.sh instances list
  ./sentinel-cli.sh instances create <name> [display-name]
  ./sentinel-cli.sh instances rename <old-name> <new-name>
  ./sentinel-cli.sh instances delete <name>

Configuration:
  Root .env is the source of truth for stack credentials, workspace path,
  COMPOSE_PROJECT_NAME, and STACK_PORT. The CLI creates or reconciles it on
  startup before showing the menu or running commands.

  COMPOSE_PROJECT_NAME       Compose project name (default: sentinel).
                             Runtime Docker network remains sentinel_default.
  SENTINEL_RUNTIME_WORKSPACES_DIR
                             Absolute host directory for runtime session
                             workspaces. Required in prod. Dev defaults to
                             .sentinel/runtime/workspaces under this checkout.
  SENTINEL_AUTH_USERNAME     App admin username from root .env.
                             Required in both modes; prod rejects defaults.
  SENTINEL_AUTH_PASSWORD     App admin password from root .env.
                             Required in both modes; prod rejects defaults.
  STACK_PORT                 Published frontend/API port (default: 4747).

Launch/debug overrides:
  SENTINEL_MODE              prod or dev (default: prod).
                             Use --dev for local development mode.
  SENTINEL_COMPOSE_FILE      Expert override for CLI stack controls.
                             Defaults to docker-compose.yml in prod mode and
                             docker-compose.dev.yml in dev mode.
  SENTINEL_URL               API root URL (default: http://localhost:$STACK_PORT).
  SENTINEL_TOKEN             Bearer token for non-interactive API calls.
  SENTINEL_READY_TIMEOUT     Seconds to wait for backend readiness (default: 60).
  SENTINEL_STATUS_TTL        Status cache TTL in seconds (default: 2).

Interactive keys:
  ↑/↓ or j/k     Navigate          Enter / Space    Select
  1–9            Jump to option    q or Esc         Back / cancel

Main-menu letter shortcuts (jump + select in one keystroke):
  u  Start Stack   d  Stop Stack   r  Restart Stack   x  Reset Stack
  i  Instances     s  Status       l  Logs            f  Refresh     e  Exit

Instances submenu shortcuts:
  l  List   c  Create   r  Rename   d  Delete   b  Back
EOF
}

# === Status probes ===
backend_ready() {
  [[ -n "$HAVE_CURL" ]] || return 1
  curl -fsS --max-time 1 "${HEALTH_READY_URL}" >/dev/null 2>&1
}

docker_ready() {
  [[ -n "$HAVE_DOCKER" ]] || return 1
  docker info >/dev/null 2>&1
}

resolve_compose_file() {
  case "$SENTINEL_MODE" in
    prod|production)
      SENTINEL_MODE="prod"
      COMPOSE_FILE="${SENTINEL_COMPOSE_FILE:-docker-compose.yml}"
      ;;
    dev|development)
      SENTINEL_MODE="dev"
      COMPOSE_FILE="${SENTINEL_COMPOSE_FILE:-docker-compose.dev.yml}"
      ;;
    *)
      die "Invalid SENTINEL_MODE '${SENTINEL_MODE}'. Use prod or dev."
      ;;
  esac
}

is_absolute_path() {
  [[ "$1" == /* ]]
}

resolve_runtime_workspace_dir() {
  if [[ -z "$SENTINEL_RUNTIME_WORKSPACES_DIR" && "$SENTINEL_MODE" == "dev" ]]; then
    SENTINEL_RUNTIME_WORKSPACES_DIR="$ROOT_DIR/.sentinel/runtime/workspaces"
  fi
  export SENTINEL_RUNTIME_WORKSPACES_DIR
}

resolve_auth_config() {
  export SENTINEL_AUTH_USERNAME SENTINEL_AUTH_PASSWORD
}

ensure_runtime_workspace_config() {
  resolve_runtime_workspace_dir
  if [[ -z "$SENTINEL_RUNTIME_WORKSPACES_DIR" ]]; then
    err "SENTINEL_RUNTIME_WORKSPACES_DIR is required in prod mode."
    err "Set it to an absolute host path in .env before using docker-compose.yml."
    return 1
  fi
  if ! is_absolute_path "$SENTINEL_RUNTIME_WORKSPACES_DIR"; then
    err "SENTINEL_RUNTIME_WORKSPACES_DIR must be an absolute host path: ${SENTINEL_RUNTIME_WORKSPACES_DIR}"
    return 1
  fi
}

is_placeholder_value() {
  local value="$1"
  [[ -z "$value" || "$value" == replace-with-* || "$value" == CHANGE_ME* ]]
}

validate_prod_required_value() {
  local key="$1"
  local description="$2"
  local value="${!key:-}"
  if is_placeholder_value "$value"; then
    err "${key} must be set to a real ${description} before using prod mode."
    return 1
  fi
}

validate_auth_config() {
  resolve_auth_config
  if [[ -z "$SENTINEL_AUTH_USERNAME" || -z "$SENTINEL_AUTH_PASSWORD" ]]; then
    err "SENTINEL_AUTH_USERNAME and SENTINEL_AUTH_PASSWORD must be set in root .env."
    return 1
  fi
}

refresh_urls() {
  STACK_URL="${SENTINEL_URL:-http://localhost:${STACK_PORT}}"
  API_BASE="${STACK_URL%/}/api/v1"
  HEALTH_READY_URL="${STACK_URL%/}/health/ready"
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  else
    printf 'sentinel-%s-%s\n' "$(date +%s)" "$RANDOM"
  fi
}

is_weak_prod_value() {
  case "$1" in
    admin|sentinel|test|secret|password|changeme|change-me)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

root_env_has_key() {
  local key="$1"
  [[ -f "$ROOT_DIR/.env" ]] || return 1
  grep -Eq "^[[:space:]]*${key}=" "$ROOT_DIR/.env"
}

env_value_invalid() {
  local key="$1"
  local value="${!key:-}"
  [[ -n "$value" ]] || return 0
  is_placeholder_value "$value" && return 0
  case "$key" in
    STACK_PORT)
      [[ "$value" =~ ^[0-9]+$ ]] || return 0
      ;;
    SENTINEL_RUNTIME_WORKSPACES_DIR)
      is_absolute_path "$value" || return 0
      ;;
  esac
  if [[ "$SENTINEL_MODE" == "prod" ]]; then
    case "$key" in
      SENTINEL_POSTGRES_PASSWORD|SENTINEL_AUTH_PASSWORD)
        is_weak_prod_value "$value" && return 0
        (( ${#value} >= 12 )) || return 0
        ;;
      SENTINEL_JWT_SECRET_KEY)
        is_weak_prod_value "$value" && return 0
        (( ${#value} >= 32 )) || return 0
        ;;
      SENTINEL_AUTH_USERNAME)
        [[ "$value" != "admin" ]] || return 0
        ;;
    esac
  fi
  return 1
}

env_value_or_default() {
  local key="$1"
  local default="$2"
  if root_env_has_key "$key" && [[ -n "${!key:-}" ]] && ! env_value_invalid "$key"; then
    printf '%s' "${!key}"
  else
    printf '%s' "$default"
  fi
}

collect_env_errors() {
  ENV_ERRORS=""
  local key value
  for key in \
    COMPOSE_PROJECT_NAME \
    STACK_PORT \
    SENTINEL_POSTGRES_PASSWORD \
    SENTINEL_JWT_SECRET_KEY \
    SENTINEL_AUTH_USERNAME \
    SENTINEL_AUTH_PASSWORD \
    SENTINEL_RUNTIME_WORKSPACES_DIR
  do
    value="${!key:-}"
    if ! root_env_has_key "$key"; then
      ENV_ERRORS+="${key} is missing from .env"$'\n'
    elif [[ -z "$value" ]]; then
      ENV_ERRORS+="${key} is missing"$'\n'
    elif env_value_invalid "$key"; then
      ENV_ERRORS+="${key} is invalid for ${SENTINEL_MODE} mode"$'\n'
    fi
  done
  [[ -z "$ENV_ERRORS" ]]
}

reload_env_config() {
  unset COMPOSE_PROJECT_NAME STACK_PORT SENTINEL_POSTGRES_PASSWORD SENTINEL_JWT_SECRET_KEY
  unset SENTINEL_AUTH_USERNAME SENTINEL_AUTH_PASSWORD SENTINEL_RUNTIME_WORKSPACES_DIR
  load_root_env true
  COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-sentinel}"
  STACK_PORT="${STACK_PORT:-4747}"
  SENTINEL_RUNTIME_WORKSPACES_DIR="${SENTINEL_RUNTIME_WORKSPACES_DIR:-}"
  SENTINEL_AUTH_USERNAME="${SENTINEL_AUTH_USERNAME:-}"
  SENTINEL_AUTH_PASSWORD="${SENTINEL_AUTH_PASSWORD:-}"
  resolve_runtime_workspace_dir
  resolve_auth_config
  refresh_urls
}

validate_prod_stack_config() {
  [[ "$SENTINEL_MODE" == "prod" ]] || return 0
  validate_prod_required_value "SENTINEL_POSTGRES_PASSWORD" "database password" || return 1
  validate_prod_required_value "SENTINEL_JWT_SECRET_KEY" "JWT secret" || return 1
  if is_placeholder_value "$SENTINEL_AUTH_USERNAME" || [[ "$SENTINEL_AUTH_USERNAME" == "admin" ]]; then
    err "SENTINEL_AUTH_USERNAME must be changed from the default before using prod mode."
    return 1
  fi
  if is_placeholder_value "$SENTINEL_AUTH_PASSWORD" || [[ "$SENTINEL_AUTH_PASSWORD" == "admin" ]]; then
    err "SENTINEL_AUTH_PASSWORD must be changed from the default before using prod mode."
    return 1
  fi
}

prepare_runtime_workspace_dir() {
  ensure_runtime_workspace_config || return 1
  if ! mkdir -p "$SENTINEL_RUNTIME_WORKSPACES_DIR"; then
    err "Failed to create runtime workspace directory: ${SENTINEL_RUNTIME_WORKSPACES_DIR}"
    return 1
  fi
}

prepare_stack_config() {
  ensure_cli_env_ready || return 1
  prepare_runtime_workspace_dir || return 1
  validate_auth_config || return 1
  validate_prod_stack_config || return 1
}

write_dev_env_defaults() {
  upsert_root_env_value "COMPOSE_PROJECT_NAME" "$(env_value_or_default "COMPOSE_PROJECT_NAME" "sentinel")" || return 1
  upsert_root_env_value "STACK_PORT" "$(env_value_or_default "STACK_PORT" "4747")" || return 1
  upsert_root_env_value "SENTINEL_POSTGRES_PASSWORD" "$(env_value_or_default "SENTINEL_POSTGRES_PASSWORD" "sentinel")" || return 1
  upsert_root_env_value "SENTINEL_JWT_SECRET_KEY" "$(env_value_or_default "SENTINEL_JWT_SECRET_KEY" "sentinel-local-dev-secret-change-me")" || return 1
  upsert_root_env_value "SENTINEL_AUTH_USERNAME" "$(env_value_or_default "SENTINEL_AUTH_USERNAME" "admin")" || return 1
  upsert_root_env_value "SENTINEL_AUTH_PASSWORD" "$(env_value_or_default "SENTINEL_AUTH_PASSWORD" "admin")" || return 1
  upsert_root_env_value "SENTINEL_RUNTIME_WORKSPACES_DIR" "$(env_value_or_default "SENTINEL_RUNTIME_WORKSPACES_DIR" "$ROOT_DIR/.sentinel/runtime/workspaces")" || return 1
}

write_prod_env_interactive() {
  local compose_project stack_port db_password jwt_secret auth_username auth_password workspace_dir
  compose_project="$(prompt_default "Compose project name" "$(env_value_or_default "COMPOSE_PROJECT_NAME" "sentinel")")"
  stack_port="$(prompt_default "Stack port" "$(env_value_or_default "STACK_PORT" "4747")")"
  db_password="$(prompt_default "Database password" "$(env_value_or_default "SENTINEL_POSTGRES_PASSWORD" "$(generate_secret)")")"
  jwt_secret="$(prompt_default "JWT secret" "$(env_value_or_default "SENTINEL_JWT_SECRET_KEY" "$(generate_secret)")")"
  auth_username="$(prompt_default "Admin username" "$(env_value_or_default "SENTINEL_AUTH_USERNAME" "sentinel-admin")")"
  auth_password="$(prompt_default "Admin password" "$(env_value_or_default "SENTINEL_AUTH_PASSWORD" "$(generate_secret)")")"
  workspace_dir="$(prompt_default "Runtime workspaces directory" "$(env_value_or_default "SENTINEL_RUNTIME_WORKSPACES_DIR" "$ROOT_DIR/.sentinel/runtime/workspaces")")"

  upsert_root_env_value "COMPOSE_PROJECT_NAME" "$compose_project" || return 1
  upsert_root_env_value "STACK_PORT" "$stack_port" || return 1
  upsert_root_env_value "SENTINEL_POSTGRES_PASSWORD" "$db_password" || return 1
  upsert_root_env_value "SENTINEL_JWT_SECRET_KEY" "$jwt_secret" || return 1
  upsert_root_env_value "SENTINEL_AUTH_USERNAME" "$auth_username" || return 1
  upsert_root_env_value "SENTINEL_AUTH_PASSWORD" "$auth_password" || return 1
  upsert_root_env_value "SENTINEL_RUNTIME_WORKSPACES_DIR" "$workspace_dir" || return 1
}

print_env_errors() {
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && err "$line"
  done <<< "$ENV_ERRORS"
}

ensure_cli_env_ready() {
  reload_env_config
  if collect_env_errors; then
    return 0
  fi

  warn "Sentinel .env is not ready for ${SENTINEL_MODE} mode."
  print_env_errors
  if [[ ! -t 0 || ! -t 1 ]]; then
    err "Run ./sentinel-cli.sh${SENTINEL_MODE:+ --${SENTINEL_MODE}} in an interactive terminal to set up .env."
    return 1
  fi

  if [[ "$SENTINEL_MODE" == "dev" ]]; then
    if ! confirm_phrase "Write dev defaults to .env? [y/N]: " "y"; then
      err ".env setup aborted."
      return 1
    fi
    write_dev_env_defaults || return 1
  else
    warn "Prod setup will write generated values to .env. Review or override each prompt."
    write_prod_env_interactive || return 1
  fi

  reload_env_config
  if ! collect_env_errors; then
    err ".env is still not valid for ${SENTINEL_MODE} mode."
    print_env_errors
    return 1
  fi
  ok ".env is ready for ${SENTINEL_MODE} mode."
}

compose() {
  ensure_runtime_workspace_config || return 1
  resolve_auth_config
  docker compose --project-name "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

invalidate_status_cache() {
  STATUS_CACHE_TS=0
  STATUS_CACHE_TEXT=""
}

# === Rendering ===
# Header is fully static; precompute once to avoid per-frame work.
build_header() {
  if [[ -n "$HEADER_CACHE" ]]; then
    printf '%s' "$HEADER_CACHE"
    return 0
  fi
  local C="${CYAN}${BOLD}"
  local R="${RESET}"
  local CL="${CLEAR_LINE}"
  local buf=""
  # Breathing room above the wordmark.
  buf+="${CL}"$'\n'
  buf+="${C}  ███████╗███████╗███╗   ██╗████████╗██╗███╗   ██╗███████╗██╗     ${R}${CL}"$'\n'
  buf+="${C}  ██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║████╗  ██║██╔════╝██║     ${R}${CL}"$'\n'
  buf+="${C}  ███████╗█████╗  ██╔██╗ ██║   ██║   ██║██╔██╗ ██║█████╗  ██║     ${R}${CL}"$'\n'
  buf+="${C}  ╚════██║██╔══╝  ██║╚██╗██║   ██║   ██║██║╚██╗██║██╔══╝  ██║     ${R}${CL}"$'\n'
  buf+="${C}  ███████║███████╗██║ ╚████║   ██║   ██║██║ ╚████║███████╗███████╗${R}${CL}"$'\n'
  buf+="${C}  ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝${R}${CL}"$'\n'
  buf+="  ${BOLD}Stack Control Center${R}  ${DIM}·  manage instances, runtime, and logs${R}${CL}"$'\n'
  buf+="${CL}"$'\n'
  HEADER_CACHE="$buf"
  printf '%s' "$HEADER_CACHE"
}

build_status() {
  local now
  now="$(date +%s 2>/dev/null || printf '0')"
  if [[ -n "$STATUS_CACHE_TEXT" ]] && (( now - STATUS_CACHE_TS < CACHE_TTL )); then
    printf '%s' "$STATUS_CACHE_TEXT"
    return 0
  fi

  local docker_state api_state svcs running docker_up=0
  if [[ -z "$HAVE_DOCKER" ]]; then
    docker_state="${RED}missing${RESET}"
  elif docker_ready; then
    docker_up=1
    svcs="$(compose ps --services --status running 2>/dev/null \
            || compose ps --services --filter status=running 2>/dev/null \
            || true)"
    if [[ -n "$svcs" ]]; then
      running="$(printf '%s\n' "$svcs" | grep -c . 2>/dev/null || true)"
    else
      running="0"
    fi
    docker_state="${GREEN}running${RESET} ${DIM}(${running} svc)${RESET}"
  else
    docker_state="${YELLOW}unavailable${RESET}"
  fi

  # Perf: skip the 1s curl timeout when docker is clearly down — the API
  # cannot be reachable in that case (compose stack isn't running).
  local api_up=0
  if (( docker_up )) && backend_ready; then
    api_up=1
    api_state="${GREEN}ready${RESET} ${DIM}${HEALTH_READY_URL}${RESET}"
  else
    api_state="${YELLOW}offline${RESET} ${DIM}${HEALTH_READY_URL}${RESET}"
  fi

  # Frontend UI URL — bright and clickable when the API is up, dim otherwise.
  local ui_state
  if (( api_up )); then
    ui_state="${BOLD}${CYAN}${STACK_URL}${RESET}"
  else
    ui_state="${DIM}${STACK_URL}${RESET}"
  fi

  local buf=""
  buf+="  ${CYAN}${ICON_DOCKER}${RESET}  ${DIM}Docker${RESET}  ${docker_state}${CLEAR_LINE}"$'\n'
  buf+="  ${CYAN}${ICON_API}${RESET}  ${DIM}API${RESET}     ${api_state}${CLEAR_LINE}"$'\n'
  buf+="  ${CYAN}${ICON_UI}${RESET}  ${DIM}UI${RESET}      ${ui_state}${CLEAR_LINE}"$'\n'
  buf+="  ${CYAN}${ICON_STACK}${RESET}  ${DIM}Stack${RESET}   ${COMPOSE_PROJECT_NAME} on :${STACK_PORT} ${DIM}(${SENTINEL_MODE}, ${COMPOSE_FILE})${RESET}${CLEAR_LINE}"$'\n'
  buf+="${CLEAR_LINE}"$'\n'
  STATUS_CACHE_TEXT="$buf"
  STATUS_CACHE_TS="$now"
  printf '%s' "$STATUS_CACHE_TEXT"
}

build_note() {
  [[ -n "$MENU_NOTE" ]] || return 0
  printf '%s%s\n%s\n' "$MENU_NOTE" "$CLEAR_LINE" "$CLEAR_LINE"
}

# select_option title opts... -> returns index via $?, or 255 for cancel.
#
# Optional globals for callers:
#   MENU_KEY        sticky-position key; cursor starts where you left off
#   SHORTCUT_KEYS   string of single-char shortcuts, one per option
#                   (e.g. "udris" -> press 'u' to select option 0)
#
# Performance: the static frame (header + status + note) is computed once on
# entry. Navigation only redraws menu rows + footer — no subshell forks,
# no docker/curl per keystroke.
select_option() {
  local title="$1"
  shift
  local options=("$@")
  local n=${#options[@]}
  (( n > 0 )) || return 255
  local last=$(( n - 1 ))
  local key seq idx

  # Sticky cursor: restore last position for this menu key.
  local current=0
  if [[ -n "${MENU_KEY:-}" ]]; then
    local pos_var="MENU_POS_${MENU_KEY}"
    current="${!pos_var:-0}"
    (( current >= 0 && current <= last )) || current=0
  fi

  hide_cursor

  # Snapshot the static portion of the frame once. This is the expensive bit
  # (docker info, compose ps, curl health probe) — keep it out of the key loop.
  local static_block
  static_block="$(build_header)$(build_status)$(build_note)$(build_error_panel)"

  # First paint: jump to top and redraw in place. Per-line CLEAR_LINE plus
  # the frame-trailing CLEAR_TO_END wipe stale content without the visible
  # flash that CLEAR_SCREEN (`\033[2J`) causes on some terminals.
  printf '%s%s' "$GOTO_TOP" "$static_block"

  local first_paint=1
  local shortcut_hint=""
  if [[ -n "${SHORTCUT_KEYS:-}" ]]; then
    shortcut_hint="  ${CYAN}a-z${RESET}${DIM} quick"
  fi
  local footer="${CLEAR_LINE}"$'\n'
  footer+="${DIM}${CYAN}↑↓${RESET}${DIM}/${CYAN}jk${RESET}${DIM} navigate  ${CYAN}⏎${RESET}${DIM} select  ${CYAN}1-9${RESET}${DIM} jump${shortcut_hint}${DIM}  ${CYAN}q${RESET}${DIM} back  ${CYAN}^C${RESET}${DIM} exit${RESET}${CLEAR_LINE}"
  footer+="$CLEAR_TO_END"

  # Save cursor position helper — write the menu's chosen index into the
  # sticky-position slot keyed by MENU_KEY.
  _persist_position() {
    [[ -n "${MENU_KEY:-}" ]] || return 0
    printf -v "MENU_POS_${MENU_KEY}" '%d' "$1"
  }

  while true; do
    # Build only the dynamic portion (menu rows + footer).
    local buf
    if (( first_paint )); then
      # First frame: cursor sits immediately after static_block. Append rows.
      buf=""
      first_paint=0
    else
      # Subsequent frames: jump to top, reprint static (cheap string), redraw.
      buf="${GOTO_TOP}${static_block}"
    fi
    buf+="${BOLD}${title}${RESET}${CLEAR_LINE}"$'\n'
    local i
    for i in "${!options[@]}"; do
      if [[ $i -eq $current ]]; then
        buf+=" ${CYAN}${BOLD}${ICON_SELECTED}${RESET} ${BG_BLUE}${BOLD} ${options[$i]} ${RESET}${CLEAR_LINE}"$'\n'
      else
        buf+="    ${options[$i]}${CLEAR_LINE}"$'\n'
      fi
    done
    buf+="$footer"
    printf '%s' "$buf"

    if ! IFS= read -rsn1 key < /dev/tty; then
      show_cursor
      _persist_position "$current"
      return 255
    fi

    case "$key" in
      $'\e')
        # Could be arrow keys, function keys, or bare Esc.
        seq=""
        IFS= read -rsn2 -t 1 seq < /dev/tty 2>/dev/null || seq=""
        if [[ -z "$seq" ]]; then
          show_cursor
          _persist_position "$current"
          return 255
        fi
        case "$seq" in
          "[A") current=$(( current == 0 ? last : current - 1 )) ;;
          "[B") current=$(( current == last ? 0 : current + 1 )) ;;
          "[H"|"OH") current=0 ;;
          "[F"|"OF") current=$last ;;
          "[5"|"[6")
            IFS= read -rsn1 -t 1 _trail < /dev/tty 2>/dev/null || true
            if [[ "$seq" == "[5" ]]; then current=0; else current=$last; fi
            ;;
        esac
        ;;
      "k") current=$(( current == 0 ? last : current - 1 )) ;;
      "j") current=$(( current == last ? 0 : current + 1 )) ;;
      "g") current=0 ;;
      "G") current=$last ;;
      "q"|"Q")
        show_cursor
        _persist_position "$current"
        return 255
        ;;
      ""|" ")
        # Enter (empty key) or Space selects.
        show_cursor
        _persist_position "$current"
        return "$current"
        ;;
      [1-9])
        idx=$(( key - 1 ))
        if (( idx <= last )); then
          current=$idx
          show_cursor
          _persist_position "$current"
          return "$current"
        fi
        ;;
      [a-zA-Z])
        # Letter shortcut: find this char in SHORTCUT_KEYS, jump+select.
        if [[ -n "${SHORTCUT_KEYS:-}" ]]; then
          local pos
          for (( pos=0; pos < ${#SHORTCUT_KEYS}; pos++ )); do
            if [[ "${SHORTCUT_KEYS:pos:1}" == "$key" ]] && (( pos <= last )); then
              current=$pos
              show_cursor
              _persist_position "$current"
              return "$current"
            fi
          done
        fi
        ;;
    esac
  done
}

# === Prompts ===
prompt_default() {
  local label="$1"
  local default="${2:-}"
  local value
  show_cursor
  if [[ -n "$default" ]]; then
    printf '%s [%s]: ' "$label" "$default" > /dev/tty
    IFS= read -r value < /dev/tty || value=""
    value="${value:-$default}"
  else
    printf '%s: ' "$label" > /dev/tty
    IFS= read -r value < /dev/tty || value=""
  fi
  printf '%s' "$value"
}

confirm_phrase() {
  local prompt="$1"
  local expected="$2"
  local value
  show_cursor
  printf '%s' "$prompt" > /dev/tty
  IFS= read -r value < /dev/tty || value=""
  [[ "$value" == "$expected" ]]
}

press_enter_to_return() {
  show_cursor
  IFS= read -r -p "Press Enter to return... " _ < /dev/tty 2>/dev/null || true
}

# === Wait / spinner ===
wait_backend() {
  [[ -n "$HAVE_CURL" ]] || { warn "curl not available; cannot probe backend"; return 1; }
  local waited=0
  while (( waited < READY_TIMEOUT )); do
    if backend_ready; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

# Same as wait_backend, but with a spinner in interactive mode.
wait_backend_spinner() {
  [[ -n "$HAVE_CURL" ]] || { warn "curl not available; cannot probe backend"; return 1; }
  if [[ $TTY_OUT -ne 1 ]]; then
    wait_backend
    return $?
  fi
  local frames='|/-\'
  local i=0 waited=0 start
  start="$(date +%s 2>/dev/null || printf '0')"
  hide_cursor
  while (( waited < READY_TIMEOUT )); do
    if backend_ready; then
      printf '\r%s' "$CLEAR_LINE"
      show_cursor
      return 0
    fi
    printf '\r  %s%s%s Waiting for backend (%ds / %ds)...%s' \
      "$CYAN" "${frames:i:1}" "$RESET" "$waited" "$READY_TIMEOUT" "$CLEAR_LINE"
    sleep 0.2
    i=$(( (i + 1) % ${#frames} ))
    waited=$(( $(date +%s 2>/dev/null || printf '0') - start ))
  done
  printf '\r%s' "$CLEAR_LINE"
  show_cursor
  return 1
}

# === JSON helpers (python3 keeps us off jq) ===
require_python() {
  [[ -n "$HAVE_PYTHON" ]] || { err "python3 is required for API calls"; return 1; }
}

json_login_body() {
  require_python || return 1
  python3 -c 'import json,sys; print(json.dumps({"username":sys.argv[1],"password":sys.argv[2]}))' \
    "$1" "$2"
}

json_instance_create_body() {
  require_python || return 1
  python3 - "$1" "${2:-}" <<'PY'
import json, sys
payload = {"name": sys.argv[1]}
if sys.argv[2]:
    payload["display_name"] = sys.argv[2]
print(json.dumps(payload))
PY
}

json_instance_rename_body() {
  require_python || return 1
  python3 -c 'import json,sys; print(json.dumps({"name":sys.argv[1]}))' "$1"
}

parse_access_token() {
  require_python || return 1
  python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("access_token", ""))
except Exception:
    print("")
'
}

parse_error_detail() {
  require_python || { cat; return 0; }
  python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("")
    raise SystemExit(0)
try:
    data = json.loads(raw)
except Exception:
    print(raw[:500])
    raise SystemExit(0)
if isinstance(data, dict):
    detail = data.get("detail") or data.get("message") or data.get("error")
    if isinstance(detail, list):
        print("; ".join(str(d) for d in detail))
    elif detail is not None:
        print(detail)
    else:
        print(json.dumps(data)[:500])
else:
    print(json.dumps(data)[:500])
'
}

format_instances() {
  require_python || { cat; return 0; }
  python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    print("Failed to parse instance list.")
    raise SystemExit(1)
if not rows:
    print("No instances.")
    raise SystemExit(0)
for row in rows:
    parts = [row.get("name", "?")]
    for key in ("display_name", "database_name", "runtime_backend", "workspace_root"):
        value = row.get(key)
        if value:
            parts.append(f"{key}={value}")
    print("  " + "  ".join(parts))
'
}

format_instance() {
  require_python || { cat; return 0; }
  python3 -c '
import json, sys
try:
    row = json.load(sys.stdin)
except Exception:
    print("Failed to parse instance.")
    raise SystemExit(1)
for key in ("name", "display_name", "database_name", "workspace_root", "runtime_backend"):
    value = row.get(key)
    if value is not None:
        print(f"{key}: {value}")
'
}

extract_instance_names() {
  require_python || return 1
  python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
for row in rows:
    name = row.get("name")
    if name:
        print(name)
'
}

# === Auth & API ===
login_with_credentials() {
  local username="$1"
  local password="$2"
  local body response
  [[ -n "$HAVE_CURL" ]] || return 1
  body="$(json_login_body "$username" "$password")" || return 1
  response="$(curl -fsS --max-time 5 -X POST "${API_BASE}/auth/login" \
    -H "Content-Type: application/json" \
    --data "$body" 2>/dev/null)" || return 1
  printf '%s' "$response" | parse_access_token
}

prompt_login() {
  [[ -t 0 ]] || return 1
  local username password token
  show_cursor
  IFS= read -r -p "Admin username: " username < /dev/tty || return 1
  IFS= read -r -s -p "Admin password: " password < /dev/tty || return 1
  printf '\n' >&2
  token="$(login_with_credentials "$username" "$password" || true)"
  [[ -n "$token" ]] || return 1
  printf '%s' "$token"
}

upsert_root_env_value() {
  local key="$1"
  local value="$2"
  local env_file="$ROOT_DIR/.env"
  local tmp_file
  ensure_tmp_dir || return 1
  tmp_file="$TMP_DIR/root-env"
  if [[ -f "$env_file" ]]; then
    python3 - "$env_file" "$tmp_file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
key = sys.argv[3]
value = sys.argv[4]
lines = src.read_text(encoding="utf-8").splitlines()
prefix = f"{key}="
written = False
out = []
for line in lines:
    if line.startswith(prefix):
        out.append(f"{key}={value}")
        written = True
    else:
        out.append(line)
if not written:
    out.append(f"{key}={value}")
dst.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  else
    {
      printf '%s=%s\n' "$key" "$value"
    } > "$tmp_file"
  fi
  mv "$tmp_file" "$env_file"
}

prompt_auth_recovery() {
  [[ -t 0 ]] || return 1
  show_cursor
  printf '\n%sThe credentials in .env were rejected by the API.%s\n' "$YELLOW" "$RESET" > /dev/tty
  printf '  1) Restart stack to sync DB auth to current .env\n' > /dev/tty
  printf '  2) Enter current app credentials and update .env\n' > /dev/tty
  local choice username password token
  printf 'Choose [1/2]: ' > /dev/tty
  IFS= read -r choice < /dev/tty || return 1
  case "$choice" in
    1)
      stack_restart >/dev/null || return 1
      login_with_credentials "$SENTINEL_AUTH_USERNAME" "$SENTINEL_AUTH_PASSWORD"
      ;;
    2)
      printf 'Current admin username: ' > /dev/tty
      IFS= read -r username < /dev/tty || return 1
      printf 'Current admin password: ' > /dev/tty
      IFS= read -r -s password < /dev/tty || return 1
      printf '\n' > /dev/tty
      token="$(login_with_credentials "$username" "$password" || true)"
      [[ -n "$token" ]] || return 1
      upsert_root_env_value "SENTINEL_AUTH_USERNAME" "$username" || return 1
      upsert_root_env_value "SENTINEL_AUTH_PASSWORD" "$password" || return 1
      SENTINEL_AUTH_USERNAME="$username"
      SENTINEL_AUTH_PASSWORD="$password"
      export SENTINEL_AUTH_USERNAME SENTINEL_AUTH_PASSWORD
      printf '%s' "$token"
      ;;
    *)
      return 1
      ;;
  esac
}

auth_token() {
  local token
  if [[ -n "${SENTINEL_TOKEN:-}" ]]; then
    printf '%s' "$SENTINEL_TOKEN"
    return 0
  fi
  ensure_cli_env_ready || return 1
  if [[ -n "$SENTINEL_AUTH_USERNAME" && -n "$SENTINEL_AUTH_PASSWORD" ]]; then
    token="$(login_with_credentials "$SENTINEL_AUTH_USERNAME" "$SENTINEL_AUTH_PASSWORD" 2>/dev/null || true)"
    if [[ -n "$token" ]]; then
      printf '%s' "$token"
      return 0
    fi
    token="$(prompt_auth_recovery || true)"
    if [[ -n "$token" ]]; then
      printf '%s' "$token"
      return 0
    fi
  fi
  return 1
}

# api_request_interactive: like api_request but on failure stashes the
# captured stderr into the menu error panel instead of scrolling it past.
api_request_interactive() {
  ensure_tmp_dir || return 1
  local errf="$TMP_DIR/last_api.err"
  : > "$errf"
  local out
  if out="$(api_request "$@" 2>"$errf")"; then
    printf '%s' "$out"
    return 0
  fi
  local code=$?
  ui_set_error_from_file "API $1 $2 failed" "$errf" 8
  return $code
}

# api_request METHOD PATH [BODY]
# On success: writes response body to stdout, returns 0.
# On failure: writes formatted error to stderr, returns 1.
api_request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local token http_code detail
  [[ -n "$HAVE_CURL" ]] || { err "curl is required for API calls"; return 1; }
  ensure_tmp_dir || return 1

  token="$(auth_token)" || { err "Could not authenticate with Sentinel."; return 1; }

  local body_file="$TMP_DIR/api_body.$$"
  local args=( -sS --max-time 15 -o "$body_file" -w '%{http_code}'
    -X "$method" "${API_BASE}${path}"
    -H "Authorization: Bearer ${token}" )
  if [[ -n "$body" ]]; then
    args+=( -H "Content-Type: application/json" --data "$body" )
  fi

  if ! http_code="$(curl "${args[@]}" 2>/dev/null)"; then
    err "Network error calling ${method} ${path}"
    rm -f "$body_file" 2>/dev/null || true
    return 1
  fi

  if [[ -z "$http_code" ]] || (( http_code < 200 )) || (( http_code >= 300 )); then
    detail="$(parse_error_detail < "$body_file" 2>/dev/null || true)"
    err "API ${method} ${path} → ${http_code:-no-status}${detail:+: ${detail}}"
    rm -f "$body_file" 2>/dev/null || true
    return 1
  fi

  cat "$body_file"
  rm -f "$body_file" 2>/dev/null || true
}

# === Instance picker ===
pick_instance_name() {
  ensure_tmp_dir || return 1
  local response
  response="$(api_request GET "/instances" 2>/dev/null)" || {
    ui_note_error "Could not load instances. Is the stack running?"
    return 1
  }

  local list_file="$TMP_DIR/instances"
  if ! printf '%s' "$response" | extract_instance_names >"$list_file" 2>/dev/null; then
    ui_note_error "Could not parse instance list."
    return 1
  fi

  local instances=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && instances+=("$line")
  done < "$list_file"

  if [[ ${#instances[@]} -eq 0 ]]; then
    ui_note_warn "No instances exist yet. Create one first."
    return 1
  fi

  if [[ ${#instances[@]} -eq 1 ]]; then
    printf '%s' "${instances[0]}" > "$TMP_DIR/pick"
    return 0
  fi

  local options=("${instances[@]}" "Back")
  select_option "Choose Instance" "${options[@]}"
  local idx=$?
  if (( idx == 255 )) || (( idx == ${#instances[@]} )); then
    return 1
  fi
  printf '%s' "${instances[$idx]}" > "$TMP_DIR/pick"
}

# === Stack commands (one-shot safe) ===
require_stack_api() {
  if backend_ready; then
    return 0
  fi
  ui_note_error "Sentinel API not reachable at ${HEALTH_READY_URL}. Start the stack first."
  return 1
}

ensure_stack_api() {
  backend_ready || die "Sentinel API is not reachable at ${API_BASE}. Start the stack first."
}

# run_compose_captured <title> -- <compose-args...>
# Streams compose output live AND tees it to a log file. On failure, stashes
# the tail of the log into LAST_ERROR so the next menu frame can display it.
# Returns the underlying compose exit code.
run_compose_captured() {
  local title="$1"; shift
  [[ "${1:-}" == "--" ]] && shift
  if [[ -z "$HAVE_DOCKER" ]]; then
    ui_set_error_text "$title" "docker is not installed or not in PATH."
    err "docker is not installed."
    return 127
  fi
  ensure_tmp_dir || { err "Cannot create temp dir for capture"; return 1; }
  local log="$TMP_DIR/last_compose.log"
  : > "$log"
  # 2>&1 merges stderr; tee streams to terminal and saves to log.
  # pipefail (set at top) makes the pipeline reflect compose's exit status.
  compose "$@" 2>&1 | tee "$log"
  local code=${PIPESTATUS[0]}
  if (( code != 0 )); then
    ui_set_error_from_file "$title (exit ${code})" "$log"
  fi
  return $code
}

stack_up() {
  ui_clear_error
  if [[ -z "$HAVE_DOCKER" ]]; then
    ui_note_error "docker is not installed."
    err "docker is not installed."
    return 1
  fi
  if ! prepare_stack_config; then
    ui_note_error "stack configuration is not ready."
    return 1
  fi
  info "Starting Sentinel shared stack (${COMPOSE_PROJECT_NAME})..."
  if ! run_compose_captured "docker compose up failed" -- up --build -d; then
    invalidate_status_cache
    ui_note_error "docker compose up failed — details below."
    return 1
  fi
  invalidate_status_cache
  if wait_backend_spinner; then
    ok "Sentinel is ready at ${STACK_URL}"
    ui_note_ok "Sentinel ready at ${BOLD}${STACK_URL}${RESET}"
    return 0
  fi
  warn "Stack started, but the API did not become ready within ${READY_TIMEOUT}s."
  ui_note_warn "Stack started, but API readiness timed out after ${READY_TIMEOUT}s."
  return 1
}

stack_down() {
  ui_clear_error
  if [[ -z "$HAVE_DOCKER" ]]; then
    ui_note_error "docker is not installed."
    err "docker is not installed."
    return 1
  fi
  if ! run_compose_captured "docker compose down failed" -- down; then
    invalidate_status_cache
    ui_note_error "docker compose down failed — details below."
    return 1
  fi
  invalidate_status_cache
  ui_note_ok "Sentinel stack stopped."
}

stack_restart() {
  stack_down || true
  stack_up
}

remove_runtime_containers() {
  local ids=() id
  while IFS= read -r id; do
    [[ -n "$id" ]] && ids+=("$id")
  done < <(
    docker ps -a -q \
      --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
      --filter "label=com.docker.compose.service=sentinel-runtime" 2>/dev/null
  )
  if [[ ${#ids[@]} -eq 0 ]]; then
    return 0
  fi
  docker rm -f "${ids[@]}"
}

confirm_stack_reset() {
  local assume_yes="$1"
  local prod_confirm="$2"
  local phrase expected
  if [[ "$SENTINEL_MODE" == "prod" ]]; then
    if [[ "$assume_yes" == "true" ]]; then
      [[ "$prod_confirm" == "true" ]] || {
        err "Prod reset requires --yes --prod-confirm."
        return 1
      }
      return 0
    fi
    warn "This will delete the prod stack database and compose volumes for '${COMPOSE_PROJECT_NAME}'."
    warn "It keeps .env and runtime workspaces at ${SENTINEL_RUNTIME_WORKSPACES_DIR:-<unset>}."
    expected="RESET SENTINEL PROD"
  else
    [[ "$assume_yes" == "true" ]] && return 0
    warn "This will delete the dev stack database and compose volumes for '${COMPOSE_PROJECT_NAME}'."
    warn "It keeps .env and runtime workspaces at ${SENTINEL_RUNTIME_WORKSPACES_DIR:-<unset>}."
    expected="RESET"
  fi
  show_cursor
  IFS= read -r -p "Type ${expected} to confirm: " phrase < /dev/tty || return 1
  [[ "$phrase" == "$expected" ]]
}

stack_reset() {
  ui_clear_error
  local assume_yes=false
  local prod_confirm=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes|-y)
        assume_yes=true
        shift
        ;;
      --prod-confirm)
        prod_confirm=true
        shift
        ;;
      *)
        err "Unknown reset option: $1"
        return 1
        ;;
    esac
  done
  if [[ -z "$HAVE_DOCKER" ]]; then
    ui_note_error "docker is not installed."
    err "docker is not installed."
    return 1
  fi
  ensure_runtime_workspace_config || return 1
  if ! confirm_stack_reset "$assume_yes" "$prod_confirm"; then
    ui_note_warn "Reset aborted."
    return 1
  fi
  info "Resetting Sentinel stack (${COMPOSE_PROJECT_NAME})..."
  if ! run_compose_captured "docker compose reset failed" -- down -v --remove-orphans; then
    invalidate_status_cache
    ui_note_error "docker compose reset failed — details below."
    return 1
  fi
  if ! remove_runtime_containers; then
    ui_note_error "Failed to remove runtime containers."
    return 1
  fi
  invalidate_status_cache
  ok "Sentinel stack reset. .env and runtime workspaces were preserved."
  ui_note_ok "Stack reset complete. .env and runtime workspaces were preserved."
}

stack_logs() {
  if [[ -z "$HAVE_DOCKER" ]]; then
    err "docker is not installed."
    return 1
  fi
  if [[ $# -gt 0 ]]; then
    compose logs -f "$@"
  else
    compose logs -f
  fi
}

stack_status() {
  if [[ -z "$HAVE_DOCKER" ]]; then
    err "docker is not installed."
    return 1
  fi
  compose ps || true
  if backend_ready; then
    printf '\n%sBackend ready at %s%s\n' "$GREEN" "$HEALTH_READY_URL" "$RESET"
    printf '%sAPI base: %s%s\n\n' "$DIM" "$API_BASE" "$RESET"
    printf '%sInstances%s\n' "$BOLD" "$RESET"
    api_request GET "/instances" 2>/dev/null | format_instances || true
  else
    printf '\n%sBackend not ready at %s%s\n' "$YELLOW" "$HEALTH_READY_URL" "$RESET"
  fi
}

# === Instance commands (one-shot) ===
instances_list() {
  ensure_stack_api
  api_request GET "/instances" | format_instances
}

instances_create() {
  local name="${1:-}"
  local display="${2:-}"
  [[ -n "$name" ]] || die "Usage: ./sentinel-cli.sh instances create <name> [display-name]"
  ensure_stack_api
  api_request POST "/instances" "$(json_instance_create_body "$name" "$display")" | format_instance
}

instances_rename() {
  local old_name="${1:-}"
  local new_name="${2:-}"
  [[ -n "$old_name" && -n "$new_name" ]] \
    || die "Usage: ./sentinel-cli.sh instances rename <old-name> <new-name>"
  ensure_stack_api
  api_request POST "/instances/${old_name}/rename" "$(json_instance_rename_body "$new_name")" \
    | format_instance
}

instances_delete() {
  local name="${1:-}"
  [[ -n "$name" ]] || die "Usage: ./sentinel-cli.sh instances delete <name>"
  ensure_stack_api
  api_request DELETE "/instances/${name}" >/dev/null
  ok "Deleted instance: ${name}"
}

# === Interactive instance flows ===
validate_instance_name() {
  local name="$1"
  [[ -n "$name" ]] || { ui_note_error "Instance name cannot be empty."; return 1; }
  if ! printf '%s' "$name" | grep -Eq '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$'; then
    ui_note_error "Invalid name '${name}'. Use letters, digits, _ or - (max 63 chars)."
    return 1
  fi
}

interactive_instances_list() {
  ui_clear_error
  require_stack_api || return 0
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sInstances%s\n\n' "$CYAN" "$ICON_INSTANCES" "$RESET" "$BOLD" "$RESET"
  local resp
  if resp="$(api_request_interactive GET "/instances")"; then
    printf '%s\n' "$resp" | format_instances
  else
    ui_note_error "Failed to list instances — details below."
  fi
  printf '\n'
  press_enter_to_return
}

interactive_instances_create() {
  ui_clear_error
  require_stack_api || return 0
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sCreate Instance%s\n\n' "$GREEN" "$ICON_CREATE" "$RESET" "$BOLD" "$RESET"
  local name display body resp
  name="$(prompt_default "Instance name" "main")"
  validate_instance_name "$name" || return 0
  display="$(prompt_default "Display name (optional)" "")"
  printf '\n'
  body="$(json_instance_create_body "$name" "$display")" || {
    ui_note_error "Failed to build request body (python3 missing?)."
    press_enter_to_return
    return 0
  }
  if resp="$(api_request_interactive POST "/instances" "$body")"; then
    printf '%s\n' "$resp" | format_instance
    invalidate_status_cache
    ui_note_ok "Created instance ${BOLD}${name}${RESET}"
  else
    ui_note_error "Failed to create instance ${name} — details below."
  fi
  press_enter_to_return
}

interactive_instances_rename() {
  ui_clear_error
  require_stack_api || return 0
  pick_instance_name || return 0
  local old_name new_name body resp
  old_name="$(cat "$TMP_DIR/pick" 2>/dev/null)"
  [[ -n "$old_name" ]] || return 0
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sRename Instance%s\n\n' "$CYAN" "$ICON_RENAME" "$RESET" "$BOLD" "$RESET"
  new_name="$(prompt_default "New name for ${old_name}" "$old_name")"
  if [[ "$new_name" == "$old_name" ]]; then
    ui_note_info "Rename skipped (unchanged)."
    return 0
  fi
  validate_instance_name "$new_name" || return 0
  printf '\n'
  body="$(json_instance_rename_body "$new_name")" || {
    ui_note_error "Failed to build request body (python3 missing?)."
    press_enter_to_return
    return 0
  }
  if resp="$(api_request_interactive POST "/instances/${old_name}/rename" "$body")"; then
    printf '%s\n' "$resp" | format_instance
    invalidate_status_cache
    ui_note_ok "Renamed ${BOLD}${old_name}${RESET} → ${BOLD}${new_name}${RESET}"
  else
    ui_note_error "Failed to rename ${old_name} — details below."
  fi
  press_enter_to_return
}

interactive_instances_delete() {
  ui_clear_error
  require_stack_api || return 0
  pick_instance_name || return 0
  local name
  name="$(cat "$TMP_DIR/pick" 2>/dev/null)"
  [[ -n "$name" ]] || return 0
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sDelete Instance%s\n\n' "$RED" "$ICON_DELETE" "$RESET" "$BOLD" "$RESET"
  warn "This deletes the manager registry row and drops the instance database for '${name}'."
  printf '\n'
  if confirm_phrase "Type DELETE to confirm: " "DELETE"; then
    printf '\n'
    if api_request_interactive DELETE "/instances/${name}" >/dev/null; then
      invalidate_status_cache
      ui_note_ok "Deleted instance ${BOLD}${name}${RESET}"
    else
      ui_note_error "Failed to delete ${name} — details below."
    fi
  else
    ui_note_info "Delete aborted for ${name}."
  fi
}

instances_menu() {
  local options=(
    "${ICON_LIST}  List Instances"
    "${ICON_CREATE}  Create Instance"
    "${ICON_RENAME}  Rename Instance"
    "${ICON_DELETE}  Delete Instance"
    "${ICON_BACK}  Back"
  )
  while true; do
    MENU_KEY="instances" SHORTCUT_KEYS="lcrdb" \
      select_option "Instances" "${options[@]}"
    local choice=$?
    case "$choice" in
      0) interactive_instances_list ;;
      1) interactive_instances_create ;;
      2) interactive_instances_rename ;;
      3) interactive_instances_delete ;;
      4|255) return 0 ;;
    esac
    # Instance ops don't change docker state — no need to flush status cache.
  done
}

# === Other interactive screens ===
interactive_status() {
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sStack Status%s\n\n' "$CYAN" "$ICON_STATUS" "$RESET" "$BOLD" "$RESET"
  stack_status
  printf '\n'
  press_enter_to_return
}

interactive_logs() {
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sLive Logs%s\n\n' "$CYAN" "$ICON_LOGS" "$RESET" "$BOLD" "$RESET"
  local service
  service="$(prompt_default "Service filter (blank for all)" "")"
  printf '\n%s(Ctrl+C to stop streaming)%s\n\n' "$DIM" "$RESET"
  # Catch Ctrl+C locally so we return to the menu instead of exiting.
  local saved_int
  saved_int="$(trap -p INT)"
  trap 'true' INT
  if [[ -n "$service" ]]; then
    stack_logs "$service" || true
  else
    stack_logs || true
  fi
  eval "${saved_int:-trap on_int INT}"
  printf '\n'
  ui_note_info "Returned from logs."
}

interactive_start() {
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sStarting Stack%s\n\n' "$GREEN" "$ICON_START" "$RESET" "$BOLD" "$RESET"
  stack_up || true
}

interactive_stop() {
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sStopping Stack%s\n\n' "$YELLOW" "$ICON_STOP" "$RESET" "$BOLD" "$RESET"
  stack_down || true
}

interactive_restart() {
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sRestarting Stack%s\n\n' "$CYAN" "$ICON_RESTART" "$RESET" "$BOLD" "$RESET"
  stack_restart || true
}

interactive_reset() {
  printf '%s' "$CLEAR_SCREEN"
  printf '%s%s%s %sReset Stack%s\n\n' "$RED" "$ICON_RESET" "$RESET" "$BOLD" "$RESET"
  stack_reset || true
}

# === Main menu ===
menu_loop() {
  [[ -t 0 && -t 1 ]] || die "Interactive mode requires a TTY. Use --help for commands."
  detect_tools
  if [[ -z "$HAVE_DOCKER" ]]; then
    ui_note_warn "docker not found in PATH — stack controls will not work."
  fi
  local options=(
    "${ICON_START}  Start Stack"
    "${ICON_STOP}  Stop Stack"
    "${ICON_RESTART}  Restart Stack"
    "${ICON_RESET}  Reset Stack"
    "${ICON_INSTANCES}  Instances"
    "${ICON_STATUS}  Status"
    "${ICON_LOGS}  Logs"
    "${ICON_REFRESH}  Refresh"
    "${ICON_EXIT}  Exit"
  )
  # Shortcuts mirror common compose verbs: u=up, d=down, r=restart, x=reset,
  # i=instances, s=status, l=logs, f=refresh, e=exit.
  while true; do
    MENU_KEY="main" SHORTCUT_KEYS="udrxislfe" \
      select_option "Main Menu" "${options[@]}"
    local choice=$?
    case "$choice" in
      0) interactive_start; invalidate_status_cache ;;
      1) interactive_stop; invalidate_status_cache ;;
      2) interactive_restart; invalidate_status_cache ;;
      3) interactive_reset; invalidate_status_cache ;;
      4) instances_menu ;;
      5) interactive_status; invalidate_status_cache ;;
      6) interactive_logs ;;
      7) invalidate_status_cache; ui_note_info "Status refreshed." ;;
      8|255)
        show_cursor
        printf '%s' "$CLEAR_TO_END"
        exit 0
        ;;
    esac
  done
}

# === Dispatch ===
main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dev)
        SENTINEL_MODE="dev"
        shift
        ;;
      --prod|--production)
        SENTINEL_MODE="prod"
        shift
        ;;
      --compose-file)
        [[ $# -ge 2 ]] || die "--compose-file requires a path"
        SENTINEL_COMPOSE_FILE="$2"
        shift 2
        ;;
      --compose-file=*)
        SENTINEL_COMPOSE_FILE="${1#--compose-file=}"
        shift
        ;;
      *)
        break
        ;;
    esac
  done
  resolve_compose_file
  local command="${1:-menu}"
  shift || true
  case "$command" in
    help|-h|--help)
      usage
      return 0
      ;;
  esac
  ensure_cli_env_ready || exit 1
  detect_tools

  case "$command" in
    menu) menu_loop ;;
    up) stack_up "$@" ;;
    down) stack_down "$@" ;;
    restart) stack_restart "$@" ;;
    reset) stack_reset "$@" ;;
    logs) stack_logs "$@" ;;
    status) stack_status "$@" ;;
    instances)
      local subcommand="${1:-list}"
      shift || true
      case "$subcommand" in
        list) instances_list "$@" ;;
        create) instances_create "$@" ;;
        rename) instances_rename "$@" ;;
        delete|rm) instances_delete "$@" ;;
        *) usage; exit 1 ;;
      esac
      ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
