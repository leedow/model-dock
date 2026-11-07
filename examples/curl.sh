#!/usr/bin/env bash
set -euo pipefail
: "${MODELDock_API_KEY:?Set MODELDock_API_KEY to your API key}"
service_url="${MODELDock_BASE_URL:-http://localhost:8000}"
curl --fail-with-body --no-buffer "$service_url/v1/chat/completions" \
  -H "Authorization: Bearer $MODELDock_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-chat","messages":[{"role":"user","content":"Hello, ModelDock"}],"stream":true}'
