"""快速验证 FAQ 分类优化效果

使用方法:
    1. cd "d:\Program Files\workplace\agent"
    2. .venv\Scripts\activate.ps1  
    3. python scripts\quick_verify_faq.py
"""

import sys
sys.path.insert(0, 'd:/Program Files/workplace/agent')

from backend.rag.preprocessing.metadata import classify_with_confidence


def main():
    print("\n" + "="*70)
    print("🧪 P2 优化快速验证 - FAQ 分类准确性测试")
    print("="*70 + "\n")
    
    test_cases = [
        ("真实售后 FAQ", """
七天无理由退货范围 A：支持，客户在签收后 7 天内可申请无理由退货（贴身衣物/食品等除外）。
Q：退货商品运费 A：质量问题由商家承担运费；无理由退货由买家承担运费。
Q：退款时效 A：商品验收合格后，1-3 个工作日内原路退回支付账户。
"""),
        
        ("混合关键词文档", """
退款政策 FAQ
Q：如何申请退款？A：提交发票即可
发票报销流程：1. 整理所有发票 2. 填写成本价申请表 3. 提交财务审批
"""),
    ]
    
    all_passed = True
    
    for name, text in test_cases:
        doc_type, confidence, detail = classify_with_confidence(
            text, return_detail=True
        )
        
        faq_score = detail.get("scores", {}).get("faq", 0)
        fin_score = detail.get("scores", {}).get("financial", 0)
        
        status = "✅ PASS" if faq_score >= fin_score else "❌ FAIL"
        if status == "❌ FAIL":
            all_passed = False
        
        trigger_llm = detail.get("llm_fallback", False)
        
        print(f"📄 {name}:")
        print(f"   分类结果：{doc_type} [{status}]")
        print(f"   置信度：{confidence:.2%}")
        print(f"   FAQ 得分：{faq_score} | 财务得分：{fin_score}")
        
        if trigger_llm:
            print(f"   ⚠️  触发 LLM 仲裁")
        
        print()
    
    print("="*70)
    if all_passed:
        print("✅ 所有测试通过！FAQ 分类已正确优化。")
        print("\n预期行为:")
        print("  • FAQ 关键词权重已提升（FAQ+常见问题=45 分）")
        print("  • financial 发票权重降低（从 8 降至 6 分）")
        print("  • financial 和 faq 加入仲裁候选集")
    else:
        print("❌ 部分测试未通过，建议检查权重配置")
    print("="*70 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
