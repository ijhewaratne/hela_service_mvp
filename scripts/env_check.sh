#!/usr/bin/env bash
# Checks local prerequisites for Hela Service development. Exits non-zero with a clear reason.
set -uo pipefail

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
hint() { printf '       %s\n' "$1"; }

echo "Hela Service environment check"

PY=""
for c in python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  if command -v "$c" >/dev/null 2>&1; then PY=$(command -v "$c"); break; fi
done
if [ -n "$PY" ]; then
  ok "Python 3.12 found: $PY ($("$PY" -c 'import platform;print(platform.python_version())'))"
else
  bad "Python 3.12 not found (system python3 is $(python3 -V 2>&1); 3.12 is required)"
  hint "macOS: brew install python@3.12"
fi

if command -v docker >/dev/null 2>&1; then
  ok "Docker CLI found"
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon reachable ($(docker info --format '{{.ServerVersion}}'))"
  else
    bad "Docker daemon not running"
    hint "Docker Desktop: open -a Docker   |   Colima: colima start --cpu 2 --memory 4"
  fi
  if docker compose version >/dev/null 2>&1; then ok "docker compose v2 available"; else bad "docker compose v2 missing"; fi
else
  bad "Docker CLI not installed"
  hint "Install Docker Desktop (https://docs.docker.com/desktop/) or: brew install colima docker docker-compose"
fi

for port in 55432 56379; do
  holder=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $1}')
  if [ -z "$holder" ]; then ok "Port $port free"
  elif [[ "$holder" == com.docke* || "$holder" == docker* || "$holder" == limactl ]]; then ok "Port $port held by Docker (dev stack running)"
  else bad "Port $port in use by $holder"; fi
done

if [ "$(uname -m)" = "arm64" ]; then
  hint "Apple Silicon: PostGIS runs under amd64 emulation (enable Rosetta in Docker Desktop for speed)."
fi

[ "$fail" -eq 0 ] && echo "All checks passed." || echo "Fix the failures above, then re-run."
exit "$fail"
