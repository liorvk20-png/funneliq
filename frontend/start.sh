#!/bin/sh
# API_BASE_URL points at this environment's backend service — the production
# frontend at the production API, the dev frontend at the dev API. It is written
# at container start rather than baked into the image so the same image can be
# promoted between environments. Empty is valid and means "same origin", which
# is how the page behaves when the backend serves it directly in local
# development.
set -e
printf 'window.API_BASE = "%s";\n' "${API_BASE_URL:-}" > /srv/config.js
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
