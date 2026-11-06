#!/bin/sh
# Runs the site (http://localhost:8765) and the Worker (reader + companion) on this machine.
# Keys come from .env. Press Ctrl+C to stop both.
cd "$(dirname "$0")" || exit 1
[ -f .env ] || cp .env.example .env
{ grep -v '^#' .env; echo "SITE_URL=http://localhost:8765"; } > worker/.dev.vars
[ -d worker/node_modules ] || (cd worker && npm install)
trap 'kill 0' INT TERM EXIT
python3 -m http.server 8765 --directory site >/dev/null 2>&1 &
(cd worker && npx wrangler dev --port 8787 >/dev/null 2>&1) &
echo "AI Radar: http://localhost:8765 (Ctrl+C to stop)"
wait
