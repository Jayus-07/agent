"""agently.py — QQ 邮箱 Agently Mail CLI（agently-cli）包装层（批次1）

SkillHub `@tencent-adm/agently-mail` 的本地集成。agently-cli 是 npm 全局
二进制（`npm install -g @tencent-qqmail/agently-cli`），OAuth 授权走
`agently-cli auth login`（浏览器完成，凭据存本机，不入库不入 git）。

职责边界：
  - 本模块只做 subprocess 调用 + JSON envelope 解析 + exit code 语义映射，
    不做审批（发送的 human-in-the-loop 由工具层 ensure_approved 门承担，
    审批通过后以 --confirmed 直发，避免两套确认机制叠加）。
  - 只读能力（list/search/read/watch）无需审批。

exit code 语义（照 CLI 规范，skill 的 SKILL.md §错误处理）：
  0 成功 | 1 服务端/网络抖动(可重试) | 2 参数不合规(不可重试)
  3 授权失效(不可重试) | 4 本地网络(可重试) | 6 业务永久拒绝(不可重试)
  7 限频(看 Retry-After) | 8 缺 confirmation-token

JSON envelope：stdout 为 JSON，错误信息在 error.message；解析失败时
退回原始文本（CLI 版本差异容忍）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Optional

from backend.shared.logger import logger

# 可重试的 exit code（BaseSkill 层 classify_error 之外的第二层语义；
# 这里映射成人读消息，重试决策仍由 BaseSkill 统一做）
_EXIT_MESSAGES: dict[int, str] = {
    0: "成功",
    1: "Agently 服务端错误或网络抖动",
    2: "参数不合规",
    3: "Agently 授权失效，请重新执行 OAuth 登录（agently-cli auth login）",
    4: "本地网络错误",
    6: "业务永久拒绝（已退订/黑名单/不存在/已删除等），请更换参数",
    7: "触发限频，请稍后重试",
    8: "缺少 confirmation-token",
}


def agently_bin() -> Optional[str]:
    """返回 agently-cli 可执行路径；未安装返回 None。"""
    from backend.config import AGENTLY_BIN
    return shutil.which(AGENTLY_BIN)


def agently_available() -> bool:
    return agently_bin() is not None


def _parse_envelope(stdout: str) -> Any:
    """stdout → JSON envelope；解析失败退回原始文本（容忍 CLI 版本差异）。"""
    s = (stdout or "").strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {"raw": s}


def _run(args: list[str], timeout_sec: float = 30.0) -> str:
    """执行 agently-cli 子命令，返回结果字符串。

    成功：JSON envelope 序列化（含 data）；失败：`[AGENTLY ERROR:<code>] 消息`
    格式——错误消息含 "Agently"/"授权失效" 等字样，BaseSkill.classify_error
    会归类；exit 3（授权失效）映射为 permission（不可重试）。
    """
    bin_path = agently_bin()
    if bin_path is None:
        return "[AGENTLY ERROR:4] agently-cli 未安装（npm install -g @tencent-qqmail/agently-cli），且 EMAIL_ENGINE=agently"

    try:
        proc = subprocess.run(
            [bin_path, *args],
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"[Agently] 超时(>{timeout_sec}s): {args[0]} {args[1] if len(args) > 1 else ''}")
        return f"[AGENTLY ERROR:1] agently-cli 执行超时（>{timeout_sec:.0f}s）"
    except OSError as e:
        return f"[AGENTLY ERROR:4] agently-cli 启动失败: {e}"

    envelope = _parse_envelope(proc.stdout)
    if proc.returncode == 0:
        return json.dumps(envelope, ensure_ascii=False)

    msg = _EXIT_MESSAGES.get(proc.returncode, f"未知错误(exit={proc.returncode})")
    # envelope 里的 error.message 优先（照 SKILL.md：错误文案在 error.message，照原文反馈）
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        detail = envelope["error"].get("message") or ""
        if detail:
            msg = f"{msg}: {detail}"
    logger.warning(f"[Agently] exit={proc.returncode} args={args[:2]} msg={msg}")
    return f"[AGENTLY ERROR:{proc.returncode}] {msg}"


# ── 只读能力 ─────────────────────────────────────────────

def agently_me(timeout_sec: float = 15.0) -> str:
    """当前授权用户信息（+me），用于验证 OAuth 状态。"""
    return _run(["+me"], timeout_sec)


def agently_list(folder: str = "inbox", limit: int = 10,
                 cursor: str = "", timeout_sec: float = 30.0) -> str:
    """按文件夹翻页列出邮件。folder: inbox|sent|trash|spam"""
    args = ["message", "+list", "--dir", folder, "--limit", str(limit)]
    if cursor:
        args += ["--cursor", cursor]
    return _run(args, timeout_sec)


def agently_search(query: str, folder: str = "", limit: int = 10,
                   cursor: str = "", timeout_sec: float = 30.0) -> str:
    """关键词+多维度过滤搜索。翻页时调用方必须保留原查询条件再追加 cursor。"""
    args = ["message", "+search", "--q", query, "--limit", str(limit)]
    if folder:
        args += ["--dir", folder]
    if cursor:
        args += ["--cursor", cursor]
    return _run(args, timeout_sec)


def agently_read(message_id: str, timeout_sec: float = 30.0) -> str:
    """读取邮件完整内容（含 body、attachments）。message_id 形如 msg_xxx。"""
    return _run(["message", "+read", "--id", message_id], timeout_sec)


def agently_watch(timeout_sec: float = 300.0) -> str:
    """长轮询等待新邮件，窗口内无新邮件返回空结果。

    timeout_sec 上限受 AGENTLY_WATCH_MAX_SECONDS 约束（Skill 层超时判重试，
    线程不可取消——窗口必须小于 Skill default_timeout，否则重复挂起）。
    """
    from backend.config import AGENTLY_WATCH_MAX_SECONDS
    capped = min(max(timeout_sec, 10.0), float(AGENTLY_WATCH_MAX_SECONDS))
    return _run(["message", "+watch", "--msg-format", "full"], timeout_sec=capped + 10.0)


# ── 写能力（审批门在工具层 ensure_approved，这里一律 --confirmed 直发）──

def agently_send(to: list[str], subject: str, body: str,
                 cc: Optional[list[str]] = None,
                 bcc: Optional[list[str]] = None,
                 attachments: Optional[list[str]] = None,
                 timeout_sec: float = 60.0) -> str:
    """发送邮件。审批已由 ensure_approved 门完成，故传 --confirmed 免 CLI 两阶段。

    attachments 必须是相对路径（CLI 规范），调用方负责路径校验。
    """
    args = ["message", "+send", "--confirmed"]
    for addr in to:
        args += ["--to", addr]
    args += ["--subject", subject]
    if body.lstrip().startswith("<"):
        args += ["--body-file", _body_tmpfile(body, "html")]
    else:
        args += ["--body-file", _body_tmpfile(body, "md")]
    for addr in (cc or []):
        args += ["--cc", addr]
    for addr in (bcc or []):
        args += ["--bcc", addr]
    for path in (attachments or []):
        args += ["--attachment", path]
    return _run(args, timeout_sec)


def _body_tmpfile(body: str, suffix: str) -> str:
    """正文落临时文件（CLI 的 --body/--body-file 二选一，长正文用文件更稳）。"""
    import os
    import tempfile
    fd, path = tempfile.mkstemp(prefix="agently_body_", suffix=f".{suffix}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return path
