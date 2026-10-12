#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BOXAGENT_ROOT=$(dirname -- "$script_dir")
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
. "$script_dir/runtime-env.sh"
cd "$BOXAGENT_ROOT"
if [ "${1:-}" = --check-install ]; then
    exec uv run --offline --no-sync --locked --script scripts/pet.py "$@"
fi
exec uv run --locked --script scripts/pet.py "$@"
