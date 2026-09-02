"""scripts/tool_quality_check.py — Tool 质量检查脚本

功能：
1. 检测 Tool 重复定义
2. 验证所有 Tool 已正确注册
3. 输出工具列表和元数据
"""
import sys
import json


def check_tool_duplicates():
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
        else:
            print("   [OK] 未发现重复定义 ✓")
            return True
            
    except Exception as e:
        print(f"   [ERROR] 检查失败：{e}")
        return False


def verify_all_tools_registered():
    """验证所有期望的 Tool 都已注册"""
    print("\n[2/3] 验证 Tool 注册情况...")
    
    try:
        from backend.tools.tool_registry import tool_registry
        
        expected_tools = [
            'execute_sql_tool',
            'sql_query_tool', 
            'search_knowledge_tool',
            'generate_report_tool',
            'export_csv_tool',
            'web_search_tool',
            'web_crawl_tool',
            'send_email_tool',
            'data_collection_tool',
            'competitor_analyze_tool',
        ]
        
        registered = tool_registry.tool_names
        
        missing = set(expected_tools) - registered
        extra = registered - set(expected_tools)
        
        if missing:
            print(f"   [WARN] 缺少 {len(missing)} 个工具:")
            for tool in missing:
                print(f"      - {tool}")
        
        if extra:
            print(f"   [INFO] 额外注册了 {len(extra)} 个工具:")
            for tool in extra:
                print(f"      - {tool}")
        
        # 只要没有缺失就算通过
        if not missing:
            print(f"   [OK] 所有 {len(expected_tools)} 个期望工具已注册 ✓")
            print(f"   📦 总计注册：{len(registered)} 个工具")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"   [ERROR] 验证失败：{e}")
        return False


def print_tool_schema():
    """打印 Tool Schema 概要"""
    print("\n[3/3] Tool Schema 概要...")
    
    try:
        from backend.tools.tool_registry import tool_registry
        
        registry_summary = []
        for name in sorted(tool_registry.tool_names):
            sources = tool_registry._tool_sources.get(name, [])
            registry_summary.append({
                'name': name,
                'sources_count': len(sources),
                'sources': sources
            })
        
        print(json.dumps(registry_summary, indent=2, ensure_ascii=False))
        print("\n   [OK] Schema 导出成功")
        return True
        
    except Exception as e:
        print(f"   [ERROR] Schema 导出失败：{e}")
        return False


def main():
    print("=" * 60)
    print("Tool 质量检查报告")
    print("=" * 60)
    
    results = []
    
    # 检查 1: 重复定义
    r1 = check_tool_duplicates()
    results.append(r1)
    
    # 检查 2: 注册验证
    r2 = verify_all_tools_registered()
    results.append(r2)
    
    # 检查 3: Schema 导出
    r3 = print_tool_schema()
    results.append(r3)
    
    print("\n" + "=" * 60)
    
if all(results):
        print("SUCCESS: All checks passed!")
        print("=" * 60)
        return 0
    else:
        print("FAILURE: Some checks failed")
        print("=" * 60)
        print(f"\n详情:")
        print(f"  - 重复定义检查：{'✓' if results[0] else '✗'}")
        print(f"  - 注册验证：{'✓' if results[1] else '✗'}")
        print(f"  - Schema 导出：{'✓' if results[2] else '✗'}")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
