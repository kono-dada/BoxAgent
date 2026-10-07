#!/bin/zsh

set -e

if [[ ! -f scripts/voice-demo.py ]]; then
  print -u2 '请先进入项目根目录再运行此脚本。'
  exit 1
fi

if [[ -e .env.local ]]; then
  print -u2 '.env.local 已存在；为避免覆盖，脚本已退出。'
  exit 1
fi

read -rs 'dashscope_api_key?请粘贴北京地域 DashScope API Key，按回车确认（输入不会显示）：'
print
if [[ -z "$dashscope_api_key" ]]; then
  print -u2 '未输入 DashScope API Key。'
  exit 1
fi

read 'workspace_id?请输入百炼 Workspace ID：'
if [[ -z "$workspace_id" ]]; then
  print -u2 '未输入 Workspace ID。'
  exit 1
fi

read -rs 'deepseek_api_key?请粘贴 DeepSeek API Key，按回车确认（输入不会显示）：'
print
if [[ -z "$deepseek_api_key" ]]; then
  print -u2 '未输入 DeepSeek API Key。'
  exit 1
fi

read -rs 'typesafe_api_key?请粘贴 TypeSafe.ai API Key，按回车确认（输入不会显示）：'
print
if [[ -z "$typesafe_api_key" ]]; then
  print -u2 '未输入 TypeSafe.ai API Key。'
  exit 1
fi

umask 077
{
  printf 'DASHSCOPE_API_KEY=%s\n' "$dashscope_api_key"
  printf 'BOXAGENT_QWEN_TRANSPORT=auto\n'
  printf 'BOXAGENT_DASHSCOPE_WORKSPACE_ID=%s\n' "$workspace_id"
  printf 'BOXAGENT_DASHSCOPE_REGION=cn-beijing\n'
  printf 'BOXAGENT_TASK_PROVIDER=deepseek\n'
  printf 'DEEPSEEK_API_KEY=%s\n' "$deepseek_api_key"
  printf 'TYPESAFE_API_KEY=%s\n' "$typesafe_api_key"
  printf 'BOXAGENT_JEV_MEM_BACKEND=jev\n'
} > .env.local
unset dashscope_api_key workspace_id deepseek_api_key typesafe_api_key
print '已保存完整产品凭据到仅本机可读的 .env.local；该文件已被 Git 忽略。'
