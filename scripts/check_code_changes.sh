#!/bin/bash
# Stop Hook: 检测未提交的代码变更，30 分钟内仅提醒一次
# 解决原 hook 因文件 mtime 持续满足条件而无限触发的问题
#
# 安装方式（写入 .claude/settings.local.json）:
#   {
#     "hooks": {
#       "Stop": [{
#         "matcher": "",
#         "hooks": [{
#           "type": "command",
#           "command": "bash \"<repo>/scripts/check_code_changes.sh\"",
#           "timeout": 10
#         }]
#       }]
#     }
#   }

# 自动定位 git 仓库根（脚本可在任意 cwd 下运行）
repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$repo_root" ]; then
  exit 0
fi
cd "$repo_root" || exit 0

# 状态文件：保存在 .claude/ 下（该目录被 .gitignore 排除，仅本机生效）
STATE=".claude/.last_review_reminder"

# 计算 .py/.ts/.tsx 未提交变更（含未跟踪）
changed=$(git status --porcelain 2>/dev/null | grep -E '\.(py|tsx?)$' | wc -l)

if [ "$changed" -eq 0 ]; then
  exit 0
fi

# 状态文件存在且 mtime < 30 分钟，跳过提醒
if [ -f "$STATE" ]; then
  age_min=$(find "$STATE" -mmin -30 2>/dev/null | wc -l)
  if [ "$age_min" -gt 0 ]; then
    exit 0
  fi
fi

# 触发提醒 + 更新状态
touch "$STATE"

cat <<EOF
{"systemMessage":"💡 检测到 ${changed} 个未提交的代码变更","hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"[Stop Hook] 未提交 .py/.ts/.tsx 变更。下次回复时建议先 git diff 确认范围，再决定是否跑 detect_changes_tool + get_affected_flows_tool。30 分钟内不重复提醒。"}}
EOF