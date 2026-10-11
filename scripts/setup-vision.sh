#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BOXAGENT_ROOT=$(dirname -- "$script_dir")
. "$script_dir/runtime-env.sh"
vision_dir=$(dirname -- "$(dirname -- "$BOXAGENT_PERCEPTION_PYTHON")")
if [ ! -x "$BOXAGENT_PERCEPTION_PYTHON" ]; then
    uv venv "$vision_dir" --python 3.12.9
fi
uv pip sync --python "$BOXAGENT_PERCEPTION_PYTHON" "$script_dir/requirements/vision.lock.txt"
uv pip check --python "$BOXAGENT_PERCEPTION_PYTHON"
printf '视觉环境已准备：%s\n' "$vision_dir"
