"""scan_secrets.py — 仓库敏感信息扫描（P0 护栏，无第三方依赖）。

为什么存在（2026-10-12 P0）：一次性能取证的日志导出把带明文密码的 Redis URL
提交进了公开 PR。事后靠人工发现，没有任何自动门禁。本脚本复用既有 CI
（.github/workflows），不引入新服务/新依赖。

用法：
  python scripts/scan_secrets.py                     # 默认扫工作树 + 历史
  python scripts/scan_secrets.py --tree
  python scripts/scan_secrets.py --history [--range origin/main..HEAD]
  python scripts/scan_secrets.py --rev origin/main
  python scripts/scan_secrets.py --with-runtime-secrets   # 叠加真实密钥比对

命中只打印 文件:行号 + 命中类型，**绝不打印命中内容**。
退出码：0 干净 / 1 命中 / 2 环境错误。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# ── 通用模式（不依赖边界符，靠最小长度压误报）──
# 用户名为空也要命中（事故形态正是 redis://:password@host:6379/0 —— 上一版
# 要求用户名至少 1 个字符，会漏掉这一类）。占位符（xxx:yyy@）由调用方过滤。
CREDENTIAL_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]*:[^\s/@]+@")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
AWS_KEY = re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}")
GITHUB_TOKEN = re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")
SLACK_TOKEN = re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")
OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9]{20,}")
# 只在右侧是"看起来像字面量密钥"时命中：允许无引号、单引号、双引号三种写法，
# 并排除函数调用 / 变量引用（foo(...)、obj.attr、foo[bar]）与哨兵编号占位
# （如 noqa: S105），把误报压到可人工逐条审计的数量。
ASSIGNED_SECRET = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token)\b[\s\"\']*[:=][\s\"\']*"
    r"(?!noqa)(?!\w+\s*\()(?!\w+[.\[])([A-Za-z0-9_\-]{16,})"
)

GENERIC_RULES = [
    ("url_with_credentials", CREDENTIAL_URL),
    ("private_key_block", PRIVATE_KEY),
    ("aws_access_key_id", AWS_KEY),
    ("github_token", GITHUB_TOKEN),
    ("slack_token", SLACK_TOKEN),
    ("openai_style_key", OPENAI_KEY),
    ("assigned_secret", ASSIGNED_SECRET),
]

# 注意：**不要整目录豁免 artifacts/**。上一次真实泄漏就发生在
# artifacts/rag-perf-r0r1/ 的文本日志里；整目录跳过等于给事故路径开后门。
# 只跳过体积/二进制类目录，文本产物一律扫描。
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
             "dist", "build", ".next", ".cache"}

# 允许清单：仓库既有测试/示例里的占位符（如 api_key="sk-test"、示例 DSN）。
# 逐条列出、可审计、可回退；新增条目必须随 PR 说明理由，禁止用通配符大范围豁免。
ALLOWLIST: list[tuple[str, str]] = [
    ("backend/tests/", "assigned_secret"),            # 测试夹具占位符
    ("backend/tests/", "url_with_credentials"),       # 测试用假 DSN
    ("backend/.env.example", "url_with_credentials"),  # 示例 DSN 模板
    ("backend/.env.example", "assigned_secret"),       # 示例密钥占位
    ("backend/evaluation/runners/travel_booking.py", "assigned_secret"),  # 运行时配置名
    ("backend/app/api/routes/observability.py", "url_with_credentials"),  # 文档示例
    ("backend/data_collection/writers/sqlalchemy_writer.py", "url_with_credentials"),
    ("backend/tools/crawler_runtime.py", "url_with_credentials"),
    ("backend/rag/indexing/reconcile.py", "assigned_secret"),     # 运行时 header
    ("backend/providers/travel/live/qweather.py", "assigned_secret"),  # 运行时 header
    ("frontend/src/app/login/page.tsx", "assigned_secret"),        # 变量引用
    ("frontend-admin/src/api/modelConfig.test.ts", "assigned_secret"),  # 测试夹具
    ("scripts/gateway_baseline_check.py", "assigned_secret"),      # 测试脚本占位
    ("scripts/gateway_rate_limit_check.py", "assigned_secret"),    # 测试脚本占位
    ("scripts/smoke_gateway_auth.sh", "assigned_secret"),          # 测试脚本占位
    # 注意：scripts/travel_* 三个脚本含**真实 API_KEY**，故意不豁免 —— 扫描
    # 会持续把它们报出来，直到有人轮换凭据并改成读环境变量（见 PR 说明）。
]


# 命中行里的"明显占位符"才算允许：整段或 userinfo 是示例词。
PLACEHOLDER_USERINFO = re.compile(
    r"(?i)://(?:user|username|userpass|user_name)?:(?:pass|password|passwd|pwd|secret|test|xxx+|"
    r"\*+|change[-_]?me|<[^>]+>|\$\{[^}]+\})@"
)


def _is_allowed(path: str, rule: str, line: str = "") -> bool:
    if rule == "url_with_credentials":
        if PLACEHOLDER_USERINFO.search(line):
            return True
        # compose/脚本里的变量插值仍属占位：redis://${PASS}@host 之类，
        # 真实口令不入库；真正硬编码的密码不含 ${...}。
        if "${" in line and "://" in line:
            return True
    return any(path.startswith(prefix) and rule == r for prefix, r in ALLOWLIST)
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip",
                 ".gz", ".7z", ".woff", ".woff2", ".ttf", ".ico", ".mp4", ".bin"}
MAX_FILE_BYTES = 5 * 1024 * 1024


def _run_git(args, cwd=None):
    proc = subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + " failed: " + proc.stderr.strip()[:200])
    return proc.stdout


def _is_skipped(path):
    parts = Path(path).parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    return Path(path).suffix.lower() in SKIP_SUFFIXES


def scan_text(text, rules):
    """返回 [(rule_name, line_no)]，不回显命中内容。"""
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in rules:
            if pattern.search(line):
                hits.append((name, lineno))
    return hits


def _runtime_secret_rules():
    """从运行环境读取真实密钥构造精确匹配（仅 --with-runtime-secrets）。"""
    values = []
    for key, value in os.environ.items():
        if not value or len(value) < 12:
            continue
        if any(tag in key.upper() for tag in
               ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "JWT")):
            values.append(value)
    return [(f"runtime_secret_{i}", re.compile(re.escape(v)))
            for i, v in enumerate(values)]


def scan_tree(rev, rules, root=None):
    findings = []
    files = _run_git(["ls-tree", "-r", "--name-only", rev], cwd=root).splitlines() if rev \
        else _run_git(["ls-files"], cwd=root).splitlines()
    for path in files:
        if not path or _is_skipped(path):
            continue
        if rev:
            try:
                blob = _run_git(["show", rev + ":" + path], cwd=root)
            except RuntimeError:
                continue
        else:
            try:
                local = Path(path) if root is None else Path(root) / path
                if not local.is_file() or local.stat().st_size > MAX_FILE_BYTES:
                    continue
                blob = local.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        for name, lineno in scan_text(blob, rules):
            line = blob.splitlines()[lineno - 1] if lineno - 1 < len(blob.splitlines()) else ""
            if _is_allowed(path, name, line):
                continue
            findings.append((path, name, lineno))
    return findings


def scan_history(rev_range, rules, root=None):
    args = ["rev-list", "--objects"]
    args += [rev_range] if rev_range else ["--all"]
    listing = _run_git(args, cwd=root)
    seen = set()
    findings = []
    for line in listing.splitlines():
        cols = line.split(maxsplit=1)
        if len(cols) != 2:
            continue
        sha, path = cols[0], cols[1]
        if sha in seen or _is_skipped(path):
            continue
        seen.add(sha)
        try:
            blob = _run_git(["cat-file", "-p", sha], cwd=root)
        except RuntimeError:
            continue
        for name, lineno in scan_text(blob, rules):
            lines = blob.splitlines()
            line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
            if _is_allowed(path, name, line):
                continue
            findings.append((sha[:12], path, name))
            break
    return findings


def main():
    parser = argparse.ArgumentParser(description="敏感信息扫描（无第三方依赖）")
    parser.add_argument("--tree", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--rev", default=None)
    parser.add_argument("--root", default=None, help="git 仓库目录（默认当前目录，测试/多仓用）")
    parser.add_argument("--range", dest="rev_range", default=None)
    parser.add_argument("--with-runtime-secrets", action="store_true")
    parser.add_argument("--allow", action="append", default=[],
                        help="额外允许的路径前缀（可重复；用于把已知测试占位移出阻断面）")
    parser.add_argument("--fail-on", default="",
                        help="逗号分隔的规则名白名单：只有这些规则命中才返回 1；"
                             "留空 = 任何命中都返回 1。用于 CI 区分高置信阻断与存量安全债")
    args = parser.parse_args()

    rules = list(GENERIC_RULES)
    if args.with_runtime_secrets:
        rules += _runtime_secret_rules()

    global ALLOWLIST
    for prefix in args.allow:
        ALLOWLIST.append((prefix, "assigned_secret"))
        ALLOWLIST.append((prefix, "url_with_credentials"))
    fail_on = {r.strip() for r in args.fail_on.split(",") if r.strip()}

    do_tree = bool(args.tree or args.rev) or not (args.tree or args.history)
    do_history = bool(args.history) or not (args.tree or args.history)

    tree_findings = []
    history_findings = []
    try:
        if do_tree:
            tree_findings = scan_tree(args.rev, rules, root=args.root)
        if do_history:
            history_findings = scan_history(args.rev_range, rules, root=args.root)
    except RuntimeError as exc:
        print("[scan_secrets] 环境错误: " + str(exc), file=sys.stderr)
        return 2

    for path, name, lineno in tree_findings:
        print("tree", path + ":" + str(lineno), "type=" + name)
    for rev, path, name in history_findings:
        print("history", rev, path, "type=" + name)

    total = len(tree_findings) + len(history_findings)
    blocking = total
    if fail_on:
        blocking = sum(1 for _p, name, _n in tree_findings if name in fail_on) + sum(
            1 for _r, _p, name in history_findings if name in fail_on)
    print("[scan_secrets] 命中 " + str(total) + " 处 (tree=" + str(len(tree_findings))
          + ", history=" + str(len(history_findings)) + "), 其中阻断类 " + str(blocking))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
