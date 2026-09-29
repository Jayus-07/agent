"""gen_tool_contract_lock.py — Tool 契约快照（lock）生成与漂移检测

为什么存在（M1 技术债，docs/2026-09-30-企业级治理技术债修复台账.md D1）：
34 个 Tool 的契约（args schema / output_type / 归属）此前只有 7 组
运行时守卫测试，且 CI 全 disabled——契约的破坏性变更无法在发布前被
机械拦截，事后也无法回答「哪个构建改了什么契约」。

本脚本把 tool_registry + skills registry 的**运行时派生值**固化为
``backend/tool_contracts.lock.json`` 快照（G2：源头是代码，lock 是派
生物，禁止手编）。契约语义遵守「契约不加版本号」铁律——lock 的版
本归属由 git_sha 派生，不给 Tool 增加独立版本字段。

用法（仓库根）::

    python -m backend.scripts.gen_tool_contract_lock             # 生成/更新 lock
    python -m backend.scripts.gen_tool_contract_lock --check     # 漂移检测：不一致 exit 1
    python -m backend.scripts.gen_tool_contract_lock --check --json  # CI/管理端消费

变更分类（diff 自动判定）：
  BREAKING    删 Tool / 删参数 / 参数类型变 / 可选→必填
  DEGRADED    新增必填参数 / 默认值变化 / output_type 变化
  COMPATIBLE  新增 Tool / 新增可选参数 / 必填→可选 / 描述变化 / capability 归属变化

消费方：release.sh preflight（M8 接入）、CI tool_quality workflow、
管理端 Tool 治理中心（/api/admin/tools/contracts/diff）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "backend" / "tool_contracts.lock.json"

# 分类严重度排序（整体 classification 取最重）
_SEVERITY = {"COMPATIBLE": 0, "DEGRADED": 1, "BREAKING": 2}
_UNSET = "__unset__"


# ---------------------------------------------------------------------------
# 快照派生
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _canonical_args(fn: Any) -> dict[str, dict[str, Any]]:
    """从 LangChain Tool 的 ``args``（pydantic json schema properties）派生
    规范化参数契约。

    - ``title``/``description`` 丢弃（描述变化不构成契约变化）
    - 其余键（type/anyOf/enum/default…）原样保留并按 key 排序，保证序列化稳定
    - ``required`` 从 default 键有无派生（无 default = 必填）
    """
    raw: dict = getattr(fn, "args", None) or {}
    canonical: dict[str, dict[str, Any]] = {}
    for param in sorted(raw):
        spec = {k: v for k, v in raw[param].items() if k not in ("title", "description")}
        has_default = "default" in spec
        default_val = spec.pop("default", _UNSET)
        canonical[param] = {
            "schema": spec,
            "required": not has_default,
            "default": default_val,
        }
    return canonical


def _content_hash(entry: dict[str, Any]) -> str:
    """单 Tool 契约指纹：覆盖 args/capabilities/output_type（不含 description）。"""
    payload = {
        "args_schema": entry["args_schema"],
        "capabilities": entry["capabilities"],
        "output_types": entry["output_types"],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def derive_snapshot() -> dict[str, Any]:
    """从运行时注册表派生契约快照（唯一生成路径，测试与 CLI 共用）。"""
    import backend.skills  # noqa: F401  触发 Skill 包自注册 → 连带加载全部 Tool 模块
    import backend.tools  # noqa: F401
    from backend.skills import registry as skill_registry
    from backend.tools.tool_registry import tool_registry

    # Skill 主链归属：_tool_fn property → 主 Tool；capabilities/output_type 随行。
    # 已知限制（Phase 2 增强）：SQLSkill(_tool_fn=NotImplementedError) 与多 Tool
    # 分发 Skill（CompetitorAnalysis 覆盖 _select_tool）不可静态求值——这些
    # tool 的 capabilities 为空列表。归属变化分类为 COMPATIBLE，不影响
    # 契约核心（args_schema/hash/BREAKING 判定）。
    ownership: dict[str, dict[str, Any]] = {}
    for inst in skill_registry._instances:
        try:
            tool_name = inst._tool_fn.name
        except Exception:
            continue  # _tool_fn 不可静态求值的 Skill 不影响契约本体
        entry = ownership.setdefault(tool_name, {"capabilities": [], "output_types": {}})
        entry["capabilities"].extend(sorted(inst.capabilities))
        for cap in inst.capabilities:
            entry["output_types"][cap] = inst.output_types.get(cap, inst.output_type)

    tools: dict[str, Any] = {}
    for name in sorted(tool_registry.available_tools):
        fn = tool_registry.available_tools[name]
        own = ownership.get(name, {"capabilities": [], "output_types": {}})
        sources = tool_registry._tool_sources.get(name) or ["unknown"]
        description = getattr(fn, "description", "") or ""
        entry = {
            "module": Path(sources[-1]).as_posix(),
            "description_hash": hashlib.sha256(description.encode("utf-8")).hexdigest()[:16],
            "args_schema": _canonical_args(fn),
            "capabilities": sorted(set(own["capabilities"])),
            "output_types": own["output_types"],
        }
        entry["content_hash"] = _content_hash(entry)
        tools[name] = entry

    return {
        "version": 1,
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "tool_count": len(tools),
        "tools": tools,
    }


# ---------------------------------------------------------------------------
# diff 与分类
# ---------------------------------------------------------------------------

def _diff_param(old: dict, new: dict) -> list[dict[str, str]]:
    """单 Tool 的参数级 diff（不改 Tool 集合的变更）。"""
    changes: list[dict[str, str]] = []
    for param in sorted(set(old) - set(new)):
        changes.append({"kind": "param_removed", "param": param, "classification": "BREAKING"})
    for param in sorted(set(new) - set(old)):
        cls = "DEGRADED" if new[param]["required"] else "COMPATIBLE"
        changes.append({"kind": "param_added", "param": param, "classification": cls})
    for param in sorted(set(old) & set(new)):
        o, n = old[param], new[param]
        if o["schema"] != n["schema"]:
            changes.append({"kind": "param_type_changed", "param": param, "classification": "BREAKING"})
        elif o["required"] != n["required"]:
            cls = "BREAKING" if n["required"] else "COMPATIBLE"  # 可选→必填 才是破坏
            changes.append({"kind": "required_changed", "param": param, "classification": cls})
        elif o["default"] != n["default"]:
            changes.append({"kind": "default_changed", "param": param, "classification": "DEGRADED"})
    return changes


def classify_lock_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """比对两份 lock 快照，输出逐 Tool 分类变更（纯函数，零 IO）。

    返回 ``{classification, changed_tools: [{tool, classification, changes[]}], summary}``。
    """
    changed: list[dict[str, Any]] = []
    counts = {"BREAKING": 0, "DEGRADED": 0, "COMPATIBLE": 0}

    for name in sorted(set(old) - set(new)):
        changed.append({"tool": name, "classification": "BREAKING",
                        "changes": [{"kind": "tool_removed"}]})
        counts["BREAKING"] += 1
    for name in sorted(set(new) - set(old)):
        changed.append({"tool": name, "classification": "COMPATIBLE",
                        "changes": [{"kind": "tool_added"}]})
        counts["COMPATIBLE"] += 1

    for name in sorted(set(old) & set(new)):
        o, n = old[name], new[name]
        changes = _diff_param(o["args_schema"], n["args_schema"])
        if o["output_types"] != n["output_types"]:
            changes.append({"kind": "output_type_changed", "classification": "DEGRADED"})
        if o["capabilities"] != n["capabilities"]:
            changes.append({"kind": "capability_binding_changed", "classification": "COMPATIBLE"})
        if o["description_hash"] != n["description_hash"]:
            changes.append({"kind": "description_changed", "classification": "COMPATIBLE"})
        if not changes:
            continue
        cls = max((c.get("classification", "COMPATIBLE") for c in changes), key=lambda c: _SEVERITY[c])
        changed.append({"tool": name, "classification": cls, "changes": changes})
        counts[cls] += 1

    overall = "IN_SYNC" if not changed else max(
        (c["classification"] for c in changed), key=lambda c: _SEVERITY[c])
    return {"classification": overall, "changed_tools": changed, "summary": counts}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_lock_file(path: Path = LOCK_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def run(mode: str, as_json: bool) -> int:
    snapshot = derive_snapshot()

    if mode == "update":
        LOCK_PATH.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"[tool-contract-lock] 已写入 {LOCK_PATH}（{snapshot['tool_count']} 个 Tool，"
              f"git_sha={snapshot['git_sha']}）")
        return 0

    # --check 模式
    existing = _load_lock_file()
    if existing is None:
        print("[tool-contract-lock] FAIL：lock 文件不存在，先运行生成（无 --check）", file=sys.stderr)
        return 1
    if existing.get("version") != snapshot["version"]:
        print(f"[tool-contract-lock] FAIL：lock 格式版本 {existing.get('version')} != "
              f"{snapshot['version']}，需重新生成", file=sys.stderr)
        return 1

    report = classify_lock_diff(existing.get("tools", {}), snapshot["tools"])
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[tool-contract-lock] 分类={report['classification']}  "
              f"BREAKING={report['summary']['BREAKING']} "
              f"DEGRADED={report['summary']['DEGRADED']} "
              f"COMPATIBLE={report['summary']['COMPATIBLE']}")
        for item in report["changed_tools"]:
            print(f"  - {item['tool']} [{item['classification']}]")
            for ch in item["changes"]:
                extra = f" {ch['param']}" if "param" in ch else ""
                print(f"      {ch['kind']}{extra}")

    # 任何漂移（含纯 COMPATIBLE）都 exit 1：lock 必须与代码同步提交，
    # 「描述变化」也应产生一条可见的 lock 变更记录。
    return 0 if report["classification"] == "IN_SYNC" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="漂移检测模式：不一致 exit 1")
    parser.add_argument("--json", action="store_true", help="输出 JSON（CI/管理端消费）")
    args = parser.parse_args(argv)
    return run("check" if args.check else "update", args.json)


if __name__ == "__main__":
    sys.exit(main())
