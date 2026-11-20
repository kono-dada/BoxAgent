#!/bin/sh
# Source-code launch, reusing the data and models already prepared on this Mac.
# No installer is built and no onboarding state is reset.
set -eu
local_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
local_data=${BOXAGENT_DATA_DIR:-"$HOME/Library/Application Support/BoxAgent"}
export BOXAGENT_DATA_DIR="$local_data"
if [ -x "$local_data/runtimes/jev/bin/python" ]; then
    export BOXAGENT_JEV_MEM_PYTHON=${BOXAGENT_JEV_MEM_PYTHON:-"$local_data/runtimes/jev/bin/python"}
fi
if [ -x "$local_data/runtimes/vision/bin/python" ]; then
    export BOXAGENT_PERCEPTION_PYTHON=${BOXAGENT_PERCEPTION_PYTHON:-"$local_data/runtimes/vision/bin/python"}
fi
if [ -d "$local_data/models/qwen3.5-0.8b-mlx" ]; then
    export BOXAGENT_PERCEPTION_MODEL=${BOXAGENT_PERCEPTION_MODEL:-"$local_data/models/qwen3.5-0.8b-mlx"}
fi
if [ -d "$local_data/models/embedding" ]; then
    export BOXAGENT_EMBEDDING_MODEL=${BOXAGENT_EMBEDDING_MODEL:-"$local_data/models/embedding"}
fi
exec "$local_script_dir/run-pet.sh" "$@"
