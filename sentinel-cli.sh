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
  local b="$(prompt_default "Bootstrap API Key" "$(generate_secret 16)")"

  cat > "$ef" <<EOF
STACK_PORT=$p
POSTGRES_DB=$db
POSTGRES_USER=$u
POSTGRES_PASSWORD=$pw
JWT_SECRET_KEY=$jwt
JWT_ALGORITHM=HS256
PLATFORM_BOOTSTRAP_API_KEY=$b
EOF
  chmod 600 "$ef"
  success "Config saved for '$inst'."
  action_up "$inst"
  return 0
}

action_up() {
  ensure_docker_ready || return 0
  local inst="${1:-}"
  if [[ -z "$inst" ]]; then
    rm -f "$TMP_PICK"
    if pick_instance_interactive; then
      inst=$(cat "$TMP_PICK")
    fi
  fi
  [[ -z "$inst" ]] && return 0

  info "${ICON_START} Launching '$inst'..."
  if compose_instance "$inst" up --build -d; then
    success "'$inst' is running."
    
    local ef="$(instance_env_file "$inst")"
    local p="$(read_env_value "$ef" "STACK_PORT" || echo "4747")"
    local token="$(read_env_value "$ef" "PLATFORM_BOOTSTRAP_API_KEY" || echo "UNKNOWN")"
    
    printf "\n${CYAN}${BOLD}🚀  S T A C K   O N B O A R D I N G${RESET}\n"
    printf "${DIM}---------------------------------------${RESET}\n"
    printf "1. Open the Gateway: ${MAGENTA}http://localhost:$p/${RESET}\n"
    printf "2. Log in using your ${BOLD}TEMPORARY Bootstrap Token${RESET}:\n"
    printf "   🔑 ${YELLOW}${BOLD}${token}${RESET}\n"
    printf "3. After login, you will receive your ${BOLD}final administrative credentials${RESET}.\n"
    printf "${DIM}---------------------------------------${RESET}\n"
  else
    error "Failed to start '$inst'."
  fi
  return 0
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
    "${ICON_LIST}  Global Status"
    "📜  Tail Logs"
    "🗑️   Delete Instance"
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
      3) action_list ;;
      4) action_logs ;;
      5) action_delete ;;
      6) echo "Goodbye!"; exit 0 ;;
    esac
    
    # BUFFER FLUSH: Prevents skipping the "Press Enter" prompt due to trailing characters from Docker
    while read -r -t 0; do read -r; done < /dev/tty
    
    printf "\n${DIM}Press Enter to return to menu...${RESET}"
    read -r _ < /dev/tty
  done
}

trap "echo -n '$CURSOR_ON'; rm -f '$TMP_PICK'; exit" INT TERM EXIT
menu_loop
