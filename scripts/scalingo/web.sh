#!/usr/bin/env bash
# L'agent New Relic doit envelopper le process avant le fork gunicorn.
# Sans NEW_RELIC_LICENSE_KEY, on démarre comme avant : un secret absent
# ne doit pas empêcher le boot.
set -euo pipefail

args=("$@")
if [ ${#args[@]} -eq 0 ]; then
  args=(--workers 1)
fi

if [ -n "${NEW_RELIC_LICENSE_KEY:-}" ]; then
  export NEW_RELIC_APP_NAME="${NEW_RELIC_APP_NAME:-safebrain-lightrag}"
  export NEW_RELIC_LOG="${NEW_RELIC_LOG:-stdout}"
  exec newrelic-admin run-program lightrag-gunicorn "${args[@]}"
fi

exec lightrag-gunicorn "${args[@]}"
