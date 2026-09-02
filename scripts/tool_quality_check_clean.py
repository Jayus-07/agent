"""scripts/tool_quality_check.py — Tool 质量检查脚本

功能：
1. 检测 Tool 重复定义
2. 验证所有 Tool 已正确注册  
3. 输出工具列表和元数据
"""
import sys


def main():
    print("=" * 60)
    print("Tool Quality Check Report")
    print("=" * 60)
    
    try:
        from backend.tools.tool_registry import tool_registry
        
        # Check 1: Duplicates
        print("\n[1/3] Checking duplicate definitions...")
        duplicates = tool_registry.check_duplicates()
        
        if duplicates:
            print(f"FAIL: Found {len(duplicates)} duplicate tools:")
            for name, sources in duplicates.items():
                print(f"  - {name}: {len(sources)} times")
                return 1
        else:
            print("OK: No duplicates found [PASS]")
        
        # Check 2: Registration
        print("\n[2/3] Verifying tool registration...")
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
        
        if missing:
            print(f"WARN: Missing {len(missing)} tools:")
            for tool in missing:
                print(f"  - {tool}")
            return 1
        else:
            print(f"OK: All {len(expected_tools)} expected tools registered [PASS]")
            print(f"Total registered: {len(registered)} tools")
        
        # Check 3: Summary
        print("\n[3/3] Tool registry summary...")
        print("Registered tools:")
        for name in sorted(registered):
            sources = tool_registry._tool_sources.get(name, [])
            print(f"  - {name} ({len(sources)} source)")
        
        print("\nSUCCESS: All checks passed!")
        print("=" * 60)
        return 0
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
