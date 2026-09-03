#!/usr/bin/env bash
#
# Start MediKiosk locally. `./start.sh`
#
# ⛔ PORTS ARE NOT THE DEFAULTS, AND THAT IS DELIBERATE.
#
# Several copies of this project sit side by side under Vibe_Coding/ — SIH_test, "SIH_test
# backend", "SIH_test copy", "SIH_test copy 2" and this one — and at the time of writing
# "SIH_test copy" was already serving vite on 5173 and uvicorn on 8000. Sharing those ports
# means the browser silently shows a DIFFERENT COPY of the product than the one being edited,
# which is a genuinely confusing hour to lose. This project owns its own pair and refuses to
# start rather than attach to someone else's.
#
# The banner below names the directory it is ACTUALLY running from, derived at runtime. It
# used to hardcode "SIH_test copy 2" — a different checkout — so the one line whose job is to
# tell you which copy you are looking at was telling you the wrong one. The same
# copied-rather-than-created origin left every console script in .venv/bin/ with a shebang
# pointing at a sibling's interpreter, which silently ran `make lint` and `make test` under
# another project's packages until it was found and repaired.
#
# Override either at will:
#     WEB_PORT=4000 API_PORT=4001 ./start.sh
#
# The frontend alone, when the API is not needed or the database is unreachable:
#     WEB_ONLY=1 ./start.sh
# The hero and every component being rebuilt render fine without it — the UI only needs the
# API once a screen actually calls one of the 60 endpoints.
#
# On 1010 specifically: ports below 1024 are privileged and the kernel refuses to bind them
# without root. `sudo -E ./start.sh` with WEB_PORT=1010 would work, but it runs vite, node and
# every npm postinstall hook as root. Not the default for that reason.

set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

WEB_PORT="${WEB_PORT:-10100}"
API_PORT="${API_PORT:-10101}"
WEB_ONLY="${WEB_ONLY:-}"

# ------------------------------------------------------------------ prerequisites

if [ ! -d .venv ]; then
  echo "No .venv found. Run:"
  echo "  python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
  exit 1
fi

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies…"
  (cd frontend && npm install --no-audit --no-fund)
fi

# ------------------------------------------------------------------ port guard
#
# Naming the occupant matters more than the refusal does: with four sibling checkouts, "port
# in use" without a directory sends you hunting through Activity Monitor.

port_owner() {
  local port="$1" pid
  pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1)"
  [ -z "$pid" ] && return 1
  local cwd
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | sed 's/^n//' | head -1)"
  echo "pid ${pid}${cwd:+ in ${cwd}}"
}

check_port() {
  local port="$1" label="$2" owner
  if owner="$(port_owner "$port")"; then
    echo "Port ${port} (${label}) is already taken by ${owner}."
    if [ "$owner" != "${owner#*$ROOT}" ]; then
      echo "  That is THIS project — it is already running. Stop it, or just use it."
    else
      echo "  That is a DIFFERENT project. Leaving it alone."
      echo "  Start this one elsewhere:  WEB_PORT=$((port + 10)) API_PORT=$((port + 11)) ./start.sh"
    fi
    return 1
  fi
  return 0
}

fail=0
[ -z "$WEB_ONLY" ] && { check_port "$API_PORT" "API" || fail=1; }
check_port "$WEB_PORT" "frontend" || fail=1
[ "$fail" -eq 1 ] && exit 1

if [ "$WEB_PORT" -lt 1024 ] || [ "$API_PORT" -lt 1024 ]; then
  echo "Note: a port below 1024 is privileged and needs root. If bind fails, that is why."
fi

# ------------------------------------------------------------------ run

cleanup() {
  echo ""
  echo "Stopping…"
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ -n "$WEB_ONLY" ]; then
  echo "WEB_ONLY set — skipping the API. /api calls from the browser will fail."
else
echo "Starting the API on :${API_PORT}…"
PYTHONPATH="$ROOT" .venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$API_PORT" > /tmp/medikiosk-api.log 2>&1 &
API_PID=$!

# The API reaches Supabase on boot, pays a cold connect AND retries with backoff on a
# blip, so this waits far longer than a local-only stack would need. 60s, not 20s: the
# first version timed out at 20 while the retry loop was still working.
for _ in $(seq 1 240); do
  curl -fsS "http://127.0.0.1:${API_PORT}/health" > /dev/null 2>&1 && break
  sleep 0.25
done

if ! curl -fsS "http://127.0.0.1:${API_PORT}/health" > /dev/null 2>&1; then
  echo "The API did not start. Last 30 lines of /tmp/medikiosk-api.log:"
  tail -30 /tmp/medikiosk-api.log
  echo ""
  echo "If that is a TimeoutError reaching Supabase, the UI does not need it:"
  echo "  WEB_ONLY=1 ./start.sh"
  exit 1
fi
fi

echo "Starting the frontend on :${WEB_PORT}…"
(
  cd frontend
  # vite.config.ts reads both. The proxy target must follow API_PORT or every /api call from
  # the browser lands on whatever else is listening on 8000.
  VITE_PORT="$WEB_PORT" \
  VITE_API_TARGET="http://127.0.0.1:${API_PORT}" \
    npm run dev -- --host 127.0.0.1 --port "$WEB_PORT"
) > /tmp/medikiosk-web.log 2>&1 &
WEB_PID=$!

for _ in $(seq 1 80); do
  curl -fsS "http://127.0.0.1:${WEB_PORT}" > /dev/null 2>&1 && break
  sleep 0.25
done

if ! curl -fsS "http://127.0.0.1:${WEB_PORT}" > /dev/null 2>&1; then
  echo "The frontend did not start. Last 30 lines of /tmp/medikiosk-web.log:"
  tail -30 /tmp/medikiosk-web.log
  exit 1
fi

if [ -n "$WEB_ONLY" ]; then
  API_LINES="     API                 not started (WEB_ONLY=1)"
else
  API_LINES="     API docs            http://localhost:${API_PORT}/docs
     What is mocked      http://localhost:${API_PORT}/about"
fi

cat <<BANNER

  ────────────────────────────────────────────────────────────────
   MediKiosk — "$(basename "$ROOT")"

     Frontend            http://localhost:${WEB_PORT}
${API_LINES}

   The hero is the only screen built so far; the rest of the UI is being
   rebuilt from supplied designs. The API is whole — 60 endpoints.

   Demo login
     Patient   any listed ABHA address, OTP 123456
     Staff     any name, role "clinician"

   The frontend hot-reloads. The API does NOT — restart this script after a
   Python change, or run uvicorn with --reload while editing.

   Logs: /tmp/medikiosk-api.log  /tmp/medikiosk-web.log
   Ctrl-C to stop both.
  ────────────────────────────────────────────────────────────────

BANNER

wait
