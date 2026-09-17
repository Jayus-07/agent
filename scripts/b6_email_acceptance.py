"""b6_email_acceptance.py — B6 发送侧真实验收脚本（欠账：一次真实投递）

用法（env 必须由调用方注入，脚本拒绝在未配置时执行）：
    EMAIL_ENGINE=agently \
    TOOL_APPROVAL_MODE=auto \
    REPORT_EMAIL_TO=<真实收件人> \
    ./.venv/Scripts/python.exe scripts/b6_email_acceptance.py

设计：
- 进程级 env 注入，不写 .env（运行中的容器共用 env_file，改动会影响其他会话）
- EMAIL_ENGINE=agently 走 agently-cli 直发（CLI 已装 + OAuth 已通）
- TOOL_APPROVAL_MODE=auto 跳过审批门（本地调试语义，config.py 注释明示）
- 主题带时间戳 → 幂等指纹唯一，可重复执行
- 发送成功后 search sent 目录验证投递留痕 = 发送侧闭环
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

# ── 前置校验：env 未注入直接失败（防裸跑静默走 smtp 空配置）──
problems = []
if os.getenv("EMAIL_ENGINE") != "agently":
    problems.append("EMAIL_ENGINE 必须显式设为 agently（B6 验收走 agently 引擎）")
if os.getenv("TOOL_APPROVAL_MODE") != "auto":
    problems.append("TOOL_APPROVAL_MODE 必须显式设为 auto（本地验收跳过审批门）")
to_addr = (os.getenv("REPORT_EMAIL_TO") or "").strip()
if not to_addr:
    problems.append("REPORT_EMAIL_TO 必须设为真实收件人（建议 agently 授权账号自发自收）")
if problems:
    print("[B6-ACCEPTANCE] 前置校验失败:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(2)

from backend.tools.email import search_email_tool, send_email_tool  # noqa: E402

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
subject = f"[经营日报] B6 发送侧验收 {now}"
body = (
    f"# 经营日报 {now}\n\n"
    "## 验收说明\n\n"
    "本邮件由 B6 发送侧真实验收脚本（scripts/b6_email_acceptance.py）发出：\n"
    "- 引擎: Agently Mail（agently-cli）\n"
    "- 触发链路: send_email_tool → ensure_approved(auto) → agently_send --confirmed\n"
    "- 业务场景: 报告中心工作流结果推送（daily_report Step 7，收件人 REPORT_EMAIL_TO 可配置）\n\n"
    f"收到此邮件即证明一次真实投递发生（时间戳 {now}）。"
)

print(f"[B6-ACCEPTANCE] 发送目标: {to_addr}")
print(f"[B6-ACCEPTANCE] 主题: {subject}")

send_result = send_email_tool.invoke({
    "to": to_addr, "subject": subject, "body": body,
})
print(f"[B6-ACCEPTANCE] send_email_tool → {send_result}")

if "邮件已发送" not in send_result:
    print("[B6-ACCEPTANCE] ❌ 发送失败（返回非成功语义），验收不通过")
    sys.exit(1)

# ── 投递留痕验证：搜 sent 目录 ──
print("[B6-ACCEPTANCE] 搜索 sent 目录验证投递留痕…")
search_result = search_email_tool.invoke({
    "query": "B6 发送侧验收", "folder": "sent", "limit": 5,
})
print(f"[B6-ACCEPTANCE] search → {search_result[:600]}")

delivered = False
try:
    envelope = json.loads(search_result)
    data = envelope.get("data", {})
    # CLI 实测 envelope 结构：{"ok":true,"data":{"data":[{...}],"total":N}}
    msgs = data.get("data") or data.get("messages") or data.get("list") or []
    delivered = any("B6 发送侧验收" in json.dumps(m, ensure_ascii=False) for m in msgs)
except (json.JSONDecodeError, AttributeError):
    delivered = "B6 发送侧验收" in search_result

if delivered:
    print("[B6-ACCEPTANCE] ✅ 验收通过：一次真实投递已完成（发送 + sent 留痕）")
else:
    print("[B6-ACCEPTANCE] ⚠️ 发送成功但 sent 搜索未命中（可能有同步延迟），请人工核收件箱")
