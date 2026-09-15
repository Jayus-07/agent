"""scripts/tool_quality_check.py — Tool 质量检查（可独立运行）

检查三件事：
1. 重复定义：同一个 Tool 在同一文件里被定义多次（P0 防护）
2. 漏注册：源码里 ``@tool`` 装饰但没登记进 tool_registry
3. 汇总：列出全部已注册 Tool 及其来源文件

用法::

    python scripts/tool_quality_check.py

脚本自带 sys.path 自举，可从任意工作目录运行。

修订记录（2026-09-16）：
  - 修 IndentationError（L131 ``if all(results):`` 缩进错位，导致本脚本
    长期无法运行 —— 这是 ``map_lookup_tool`` 漏注册「无声无息」的真正原因）
  - 期望清单不再硬编码：改为 AST 扫描派生。原清单只写死 10 个工具，
    34 个里漏注册的 24 个全在检查范围之外，本就查不出漏注册。
  - 判据与 ``backend/tests/test_layer_consistency.py`` 同源
    （``scan_repo_declared_tools``），避免两份清单各自漂移。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def check_tool_duplicates() -> bool:
    """检查是否有重复定义的 Tool"""
    print("\n[1/3] 检查 Tool 重复定义...")

    try:
        from backend.tools.tool_registry import tool_registry

        duplicates = tool_registry.check_duplicates()

        if duplicates:
            print(f"   [FAIL] 发现 {len(duplicates)} 个重复定义的 Tool:")
            for name, sources in duplicates.items():
                print(f"      - {name}: {len(sources)} 次定义")
                for source in sources:
                    print(f"        • {source}")
            return False
        print("   [OK] 未发现重复定义 ✓")
        return True

    except Exception as e:
        print(f"   [ERROR] 检查失败：{e}")
        return False


def verify_all_tools_registered() -> bool:
    """验证所有 @tool 都已注册（期望清单由 AST 扫描派生，非硬编码）"""
    print("\n[2/3] 验证 Tool 注册情况（AST 扫描派生期望清单）...")

    try:
        import backend.tools  # noqa: F401  触发 tools/__init__ 导入链
        import backend.skills  # noqa: F401  触发 skills/map → tools/map 导入链
        from backend.tools.tool_registry import (
            scan_repo_declared_tools,
            tool_registry,
        )

        declared = scan_repo_declared_tools(REPO_ROOT)
        registered = set(tool_registry.tool_names)

        missing = sorted(set(declared) - registered)
        extra = sorted(registered - set(declared))

        if missing:
            print(f"   [FAIL] 有 {len(missing)} 个 @tool 已定义但未注册"
                  "（工具会从注册表里静默消失）:")
            for name in missing:
                print(f"      - {name}  @ {', '.join(declared[name])}")
            print("   修法：在定义文件底部加"
                  " tool_registry.register(<fn>, __file__)")

        if extra:
            print(f"   [FAIL] 注册表有 {len(extra)} 个源码里找不到 @tool 定义的名字"
                  "（幽灵条目）:")
            for name in extra:
                print(f"      - {name}")

        if missing or extra:
            return False

        print(f"   [OK] {len(declared)} 个 @tool 全部已注册 ✓")
        print(f"   📦 注册表条目：{len(registered)} 个")
        return True

    except Exception as e:
        print(f"   [ERROR] 验证失败：{e}")
        return False


def print_tool_schema() -> bool:
    """打印 Tool Schema 概要"""
    print("\n[3/3] Tool Schema 概要...")

    try:
        from backend.tools.tool_registry import tool_registry

        summary = []
        for name in sorted(tool_registry.tool_names):
            sources = tool_registry._tool_sources.get(name, [])
            summary.append({
                "name": name,
                "sources_count": len(sources),
                "sources": sources,
            })

        print(f"   共 {len(summary)} 个 Tool。完整清单见"
              " backend.tools.tool_registry.tool_registry.get_schema()")
        print("   [OK] Schema 导出成功")
        return True

    except Exception as e:
        print(f"   [ERROR] Schema 导出失败：{e}")
        return False


def main() -> int:
    print("=" * 60)
    print("Tool 质量检查报告")
    print("=" * 60)

    results = [
        check_tool_duplicates(),
        verify_all_tools_registered(),
        print_tool_schema(),
    ]

    print("\n" + "=" * 60)
    if all(results):
        print("SUCCESS: All checks passed!")
        print("=" * 60)
        return 0

    print("FAILURE: Some checks failed")
    print("=" * 60)
    print("\n详情:")
    print(f"  - 重复定义检查：{'✓' if results[0] else '✗'}")
    print(f"  - 注册完整性：  {'✓' if results[1] else '✗'}")
    print(f"  - Schema 导出： {'✓' if results[2] else '✗'}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
