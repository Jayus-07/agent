"""自定义测试脚本 - 用于手动验证特定场景"""

from backend.rag.preprocessing.metadata import classify_with_confidence

def verify_faq_classification():
    """验证售后 FAQ 文档识别"""
    
    print("\n" + "="*70)
    print("🔍 场景测试：cs_售后 FAQ.docx")
    print("="*70 + "\n")
    
    # 模拟真实文档内容
    test_cases = [
        ("完整售后 FAQ", """
七天无理由退货范围 A：支持，客户在签收后 7 天内可申请无理由退货（贴身衣物/食品等除外）。
Q：退货商品运费 A：质量问题由商家承担运费；无理由退货由买家承担运费。
Q：退款时效 A：商品验收合格后，1-3 个工作日内原路退回支付账户。
"""),
        
        ("混合财务关键词", """
退款政策 FAQ
Q：如何申请退款？A：提交发票即可
发票报销流程：
1. 整理所有发票
2. 填写成本价申请表
3. 提交财务审批
"""),
        
        ("物流相关 FAQ", """
常见问题 FAQ - 物流篇
Q：发货时效多久？A：工作日 24 小时内发货
Q：退换货流程是什么？A：申请 → 审核 → 寄回 → 确认收货 → 退款
Q：退款流程如何操作？A：登录后台 → 订单管理 → 申请退款
"""),
    ]
    
    for name, text in test_cases:
        doc_type, confidence, detail = classify_with_confidence(text, return_detail=True)
        
        print(f"📄 {name}:")
        print(f"   分类结果：{doc_type}")
        print(f"   置信度：{confidence:.2%}")
        print(f"   关键得分：{detail.get('scores', {})}")
        
        if detail.get("llm_fallback"):
            print(f"   ⚠️  触发了 LLM 仲裁")
        
        score = detail.get("scores", {})
        if score.get("faq", 0) >= score.get("financial", 0):
            print(f"   ✅ 正确识别为 faa（而非 financial）")
        else:
            print(f"   ❌ 可能被误判为 financial")
        
        print()
    
    print("="*70)


if __name__ == "__main__":
    verify_faq_classification()
