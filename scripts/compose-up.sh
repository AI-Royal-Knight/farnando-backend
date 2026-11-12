#!/usr/bin/env bash
# Start the Docker Compose stack, picking free host ports automatically.
#
# For every published port (API, Postgres, Redis, MinIO, Mailpit):
#   1. if this stack is already running, keep the port it already uses;
#   2. otherwise use the preferred port if it is free: the default (e.g. 8000),
#      or a value you set yourself in the environment or .env;
#   3. otherwise use the next free port above it.
# Chosen ports are written to .env with an "# auto" comment so plain
# `docker compose` commands keep using them. Auto lines are never treated as a
# preference, so every run tries the default (8000) first again.
#
# Usage: scripts/compose-up.sh [extra `docker compose up` args]
#        (defaults to: up -d --build)
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env}"
AUTO_MARK="# auto: chosen by scripts/compose-up.sh"
MAX_TRIES="${PORT_SEARCH_LIMIT:-100}"

# variable          default  service  container port
PORTS=(
  "WEB_PORT           8000  web      8000"
  "POSTGRES_PORT      5435  postgres 5432"
  "REDIS_PORT         6381  redis    6379"
  "MINIO_PORT         9000  minio    9000"
  "MINIO_CONSOLE_PORT 9001  minio    9001"
  "MAILPIT_SMTP_PORT  1025  mailpit  1025"
  "MAILPIT_UI_PORT    8025  mailpit  8025"
)

preferred_value() {
  # A port the user chose: from the environment, else a .env line without the
  # auto marker. Empty when none is set.
  local name="$1"
  if [ -n "${!name:-}" ]; then
    printf '%s' "${!name}"
  elif [ -f "$ENV_FILE" ]; then
    { grep -E "^${name}=" "$ENV_FILE" || true; } | { grep -vF "# auto" || true; } |
      tail -n 1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//' | tr -d '"'"'"' \r'
  fi
}

port_is_free() {
  local port="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import socket, sys
port = int(sys.argv[1])
for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        continue  # IPv6 not available
    try:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((address, port))
    except OSError:
        sys.exit(1)
    finally:
        sock.close()
sys.exit(0)
PY
  elif command -v ss >/dev/null 2>&1; then
    ! ss -Hltn "sport = :$port" | grep -q .
  elif command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

current_port() {
  # Host port this stack already publishes for service $1 / container port $2.
  docker compose port "$1" "$2" 2>/dev/null | head -n 1 | sed -E 's/.*:([0-9]+)$/\1/' || true
}

remove_auto() {
  # Drop an auto-chosen line once the default port is used again.
  local name="$1" tmp
  [ -f "$ENV_FILE" ] || return 0
  grep -qE "^${name}=.*# auto" "$ENV_FILE" || return 0
  tmp="$(mktemp)"
  grep -vE "^${name}=.*# auto" "$ENV_FILE" >"$tmp" || true
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
}

save_env() {
  local name="$1" value="$2"
  touch "$ENV_FILE"
  if grep -qE "^${name}=" "$ENV_FILE"; then
    local tmp
    tmp="$(mktemp)"
    awk -v key="$name" -v val="$value" -F= \
      'BEGIN { OFS = "=" } $1 == key { print key, val; next } { print }' "$ENV_FILE" >"$tmp"
    cat "$tmp" >"$ENV_FILE"
    rm -f "$tmp"
  else
    printf '%s=%s\n' "$name" "$value" >>"$ENV_FILE"
  fi
}

USED=" "
for entry in "${PORTS[@]}"; do
  read -r name default service container_port <<<"$entry"
  preferred="$(preferred_value "$name")"
  preferred="${preferred:-$default}"

  running="$(current_port "$service" "$container_port")"
  if [ -n "$running" ]; then
    port="$running"
    note="already used by this stack"
  else
    port="$preferred"
    tries=0
    until port_is_free "$port" && [[ "$USED" != *" $port "* ]]; do
      tries=$((tries + 1))
      if [ "$tries" -ge "$MAX_TRIES" ]; then
        echo "No free port found for $name between $preferred and $port." >&2
        exit 1
      fi
      port=$((port + 1))
    done
    if [ "$port" = "$preferred" ]; then
      note="free"
    else
      note="$preferred is in use"
    fi
  fi

  USED="$USED$port "
  printf -v "CHOSEN_$name" '%s' "$port"
  export "$name=$port"
  if [ "$port" = "$preferred" ] && [ "$preferred" != "$default" ]; then
    : # the user's own setting is in use; leave their .env line alone
  elif [ "$port" = "$default" ]; then
    remove_auto "$name"
  else
    save_env "$name" "$port $AUTO_MARK"
  fi
  printf '  %-19s %-6s (%s)\n' "$name" "$port" "$note"
done

if [ "$#" -eq 0 ]; then
  set -- -d --build
fi
docker compose up "$@"

host="$( (hostname -I 2>/dev/null || true) | awk '{print $1}')"
host="${host:-localhost}"
echo
echo "API:           http://$host:$CHOSEN_WEB_PORT/api/v1/"
echo "API docs:      http://$host:$CHOSEN_WEB_PORT/api/docs/"
echo "Mailpit:       http://$host:$CHOSEN_MAILPIT_UI_PORT/"
echo "MinIO console: http://$host:$CHOSEN_MINIO_CONSOLE_PORT/"
if [ "$CHOSEN_WEB_PORT" != "8000" ]; then
  echo
  echo "Port 8000 was busy, so the API runs on $CHOSEN_WEB_PORT."
  echo "If you call it by IP or domain, make sure DJANGO_ALLOWED_HOSTS and"
  echo "CSRF_TRUSTED_ORIGINS in $ENV_FILE include that host and port."
fi
