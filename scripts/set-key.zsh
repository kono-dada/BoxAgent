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

read -rs 'api_key?请粘贴北京地域 DashScope API Key，按回车确认（输入不会显示）：'
print
if [[ -z "$api_key" ]]; then
  print -u2 '未输入 API Key。'
  exit 1
fi

umask 077
printf 'DASHSCOPE_API_KEY=%s\n' "$api_key" > .env.local
unset api_key
print '已保存到仅本机可读的 .env.local；该文件已被 Git 忽略。'
