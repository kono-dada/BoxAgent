#!/bin/sh
# 所有安装和启动入口共用本机路径；只读取字面值，不执行 .env.local。
: "${BOXAGENT_ROOT:?请由调用脚本设置仓库根目录}"
if [ -f "$BOXAGENT_ROOT/.env.local" ]; then
    while IFS= read -r runtime_line || [ -n "$runtime_line" ]; do
        case "$runtime_line" in
            UV_CACHE_DIR=*|UV_PYTHON_INSTALL_DIR=*|UV_PYTHON_BIN_DIR=*|HF_HOME=*|XDG_CACHE_HOME=*|BOXAGENT_PNPM_STORE=*|BOXAGENT_PERCEPTION_PYTHON=*|BOXAGENT_PERCEPTION_MODEL=*|BOXAGENT_JEV_MEM_PYTHON=*|BOXAGENT_JEV_MEMORY_PYTHON=*|BOXAGENT_JEV_MEM_VENV=*|BOXAGENT_JEV_MEMORY_VENV=*|BOXAGENT_AOQ_INSTALL_DIR=*|BOXAGENT_AOQ_SDK_DIR=*|BOXAGENT_CODEX_BIN=*)
                runtime_name=${runtime_line%%=*}
                if ! printenv "$runtime_name" >/dev/null 2>&1; then
                    runtime_value=$(printf '%s' "${runtime_line#*=}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                    case "$runtime_value" in
                        \"*\") runtime_value=${runtime_value#\"}; runtime_value=${runtime_value%\"} ;;
                        \'*\') runtime_value=${runtime_value#\'}; runtime_value=${runtime_value%\'} ;;
                    esac
                    export "$runtime_name=$runtime_value"
                fi
                ;;
        esac
    done < "$BOXAGENT_ROOT/.env.local"
fi

# 保留正在运行的宿主与记忆环境路径，避免移动后破坏进程和入口脚本。
: "${UV_CACHE_DIR:=$BOXAGENT_ROOT/.runtime/uv-cache}"
: "${UV_PYTHON_INSTALL_DIR:=$BOXAGENT_ROOT/.runtime/python}"
: "${UV_PYTHON_BIN_DIR:=$UV_PYTHON_INSTALL_DIR/bin}"
: "${HF_HOME:=$BOXAGENT_ROOT/.runtime/cache/huggingface}"
: "${XDG_CACHE_HOME:=$BOXAGENT_ROOT/.runtime/cache/xdg}"
: "${BOXAGENT_PNPM_STORE:=$BOXAGENT_ROOT/.runtime/cache/pnpm}"
: "${BOXAGENT_PERCEPTION_PYTHON:=$BOXAGENT_ROOT/.runtime/vision-venv/bin/python}"
: "${BOXAGENT_PERCEPTION_MODEL:=$BOXAGENT_ROOT/.runtime/models/qwen3.5-0.8b-mlx}"
: "${BOXAGENT_JEV_MEM_PYTHON:=${BOXAGENT_JEV_MEMORY_PYTHON:-${BOXAGENT_JEV_MEM_VENV:-${BOXAGENT_JEV_MEMORY_VENV:-$BOXAGENT_ROOT/.runtime/jev-mem-venv}}/bin/python}}"
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR UV_PYTHON_BIN_DIR HF_HOME XDG_CACHE_HOME
export BOXAGENT_PNPM_STORE BOXAGENT_PERCEPTION_PYTHON BOXAGENT_PERCEPTION_MODEL BOXAGENT_JEV_MEM_PYTHON
export PATH="$UV_PYTHON_BIN_DIR:$PATH"
unset runtime_line runtime_name runtime_value
