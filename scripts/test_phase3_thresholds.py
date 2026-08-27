"""Test: 分级 Faithfulness 拒答阈值配置验证

验证目标:
1. .env 中的三个分级阈值正确加载
2. backend/config/rag.py 导出正确的值
3. evidence_gate.operations.is_groundedness_acceptable 使用正确的阈值
"""

import sys
from pathlib import Path

# Setup Python path
sys.path.insert(0, str(Path(__file__).parent.parent))


def print_section(title: str):
    print("\n" + "="*80)
    print(f"[TEST] {title}")
    print("="*80)


def test_env_config():
    """测试 #1: 检查 .env 文件配置"""
    print_section("TEST 1: ENV CONFIGURATION")
    
    env_file = Path(".env")
    if not env_file.exists():
        print("❌ .env file not found")
        return False
    
    content = env_file.read_text(encoding="utf-8")
    
    required_vars = [
        "FAITHFULNESS_REJECT_SCORE_HIGH_RISK=0.7",
        "FAITHFULNESS_REJECT_SCORE_MED_RISK=0.5", 
        "FAITHFULNESS_REJECT_SCORE_LOW_RISK=0.3",
    ]
    
    all_present = True
    for var in required_vars:
        if var in content:
            print(f"[OK] Found: {var}")
        else:
            print(f"[MISSING] {var}")
            all_present = False
    
    return all_present


def test_python_import():
    """测试 #2: 导入 Python 模块并验证配置值"""
    print_section("TEST 2: PYTHON MODULE IMPORT")
    
    try:
        from backend.config.rag import (
            FAITHFULNESS_REJECT_SCORE_HIGH_RISK,
            FAITHFULNESS_REJECT_SCORE_MED_RISK,
            FAITHFULNESS_REJECT_SCORE_LOW_RISK,
            FAITHFULNESS_REJECT_SCORE,
        )
        
        print(f"FAITHFULNESS_REJECT_SCORE_HIGH_RISK: {FAITHFULNESS_REJECT_SCORE_HIGH_RISK}")
        print(f"FAITHFULNESS_REJECT_SCORE_MED_RISK: {FAITHFULNESS_REJECT_SCORE_MED_RISK}")
        print(f"FAITHFULNESS_REJECT_SCORE_LOW_RISK: {FAITHFULNESS_REJECT_SCORE_LOW_RISK}")
        print(f"FAITHFULNESS_REJECT_SCORE (default): {FAITHFULNESS_REJECT_SCORE}")
        
        # Verify values
        checks = [
            ("High Risk >= 0.7", FAITHFULNESS_REJECT_SCORE_HIGH_RISK >= 0.7),
            ("Medium Risk = 0.5", FAITHFULNESS_REJECT_SCORE_MED_RISK == 0.5),
            ("Low Risk = 0.3", FAITHFULNESS_REJECT_SCORE_LOW_RISK == 0.3),
            ("Default = Medium", FAITHFULNESS_REJECT_SCORE == FAITHFULNESS_REJECT_SCORE_MED_RISK),
        ]
        
        all_passed = True
        for check_name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"[{status}] {check_name}")
            if not passed:
                all_passed = False
        
        return all_passed
        
    except ImportError as e:
        print(f"ERROR - Import error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_evidence_gate_logic():
    """测试 #3: is_groundedness_acceptable 函数逻辑"""
    print_section("TEST 3: EVIDENCE GATE LOGIC")
    
    try:
        from backend.rag.evidence_gate.operations import is_groundedness_acceptable
        
        # Test cases:
        test_cases = [
            # (score, risk_level, expected_passed, description)
            (0.6, "high", False, "0.6 < 0.7 high-risk → reject"),
            (0.7, "high", True, "0.7 >= 0.7 high-risk → accept"),
            (0.4, "medium", False, "0.4 < 0.5 medium-risk → reject"),
            (0.5, "medium", True, "0.5 >= 0.5 medium-risk → accept"),
            (0.2, "low", False, "0.2 < 0.3 low-risk → reject"),
            (0.3, "low", True, "0.3 >= 0.3 low-risk → accept"),
        ]
        
        all_passed = True
        for score, risk_level, expected, desc in test_cases:
            passed, reason = is_groundedness_acceptable(score, risk_level=risk_level)
            
            actual_result = "accept" if passed else f"reject ({reason})"
            expected_result = "accept" if expected else "reject"
            
            match = passed == expected
            status = "PASS" if match else "FAIL"
            
            print(f"[{status}] | Score:{score:.1f} Risk:{risk_level:5s} | Expected:{expected_result:7s} | Actual:{actual_result:15s}")
            
            if not match:
                all_passed = False
        
        return all_passed
        
    except Exception as e:
        print(f"ERROR - Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*80)
    print("RAG CONFIDENCE OPTIMIZATION - PHASE 3 IMPROVEMENT #1 VALIDATION")
    print("="*80)
    
    results = {}
    
    # Test 1: Env config
    results["env_config"] = test_env_config()
    
    # Test 2: Python import
    results["python_import"] = test_python_import()
    
    # Test 3: Evidence gate logic
    results["gate_logic"] = test_evidence_gate_logic()
    
    # Summary
    print("\n" + "="*80)
    print("FINAL RESULTS SUMMARY")
    print("="*80)
    
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"{test_name:20s}: [{status}]")
    
    all_passed = all(results.values())
    
    print("\n" + "="*80)
    if all_passed:
        print("SUCCESS! All Phase 3 Improvement #1 tests passed!")
        print("")
        print("Key improvements implemented:")
        print("- High-risk questions (FAQ/财务): threshold=0.7")
        print("- Medium-risk questions (制度): threshold=0.5")  
        print("- Low-risk questions (闲聊): threshold=0.3")
        print("")
        print("Next step: Deploy to production and monitor rejection rates")
    else:
        print("WARNING - Some tests failed. Please review the output above")
    print("="*80 + "\n")
    
    return all_passed


if __name__ == "__main__":
    try:
        success = run_all_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
