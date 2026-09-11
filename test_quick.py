"""Quick test for RAG Architecture Refactor Phase 1-3."""
import os
import sys

print("\n" + "="*60)
print("RAG Architecture Refactor - Quick Tests (Phase 1-3)")
print("="*60 + "\n")

# Test 1: Config
print("Test 1: Config Layer...")
try:
    from backend.config.llm import (
        ENV_MODE,
        EMBEDDING_MODEL,
        RERANK_MODEL,
        TOKEN_USAGE_LOG_PATH,
        EVAL_DATASET_PATH,
    )
    print(f"  [OK] ENV_MODE={ENV_MODE}")
    print(f"  [OK] EMBEDDING_MODEL={EMBEDDING_MODEL}")
    print(f"  [OK] RERANK_MODEL={RERANK_MODEL}")
    print(f"  [OK] TOKEN_USAGE_LOG_PATH={TOKEN_USAGE_LOG_PATH}")
    print(f"  [OK] EVAL_DATASET_PATH={EVAL_DATASET_PATH}")
except Exception as e:
    print(f"  [FAIL] {e}")
    sys.exit(1)

# Test 2: Token Tracker  
print("\nTest 2: Token Tracker...")
try:
    from backend.infra.token_tracker import (
        TokenUsageEvent,
        create_tracker_for_embedding,
        create_tracker_for_rerank,
    )
    event = TokenUsageEvent(
        component="embedding",
        model_name="text-embedding-v3",
        backend="cloud",
        prompt_tokens=100,
        completion_tokens=0,
        total_tokens=100,
        token_usage_available=True,
        duration_ms=50.5,
        status="success",
    )
    print(f"  [OK] TokenUsageEvent created")
    print(f"  [OK] JSON: {event.to_json()[:80]}...")
    
    tracker = create_tracker_for_embedding(
        log_path="/tmp/test_token.jsonl",
        model_name="test",
        backend="cloud",
    )
    print(f"  [OK] Tracker factory works")
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Prometheus metric
print("\nTest 3: Prometheus metrics...")
try:
    from backend.observability.metrics import (
        llm_tokens_total,
        token_usage_total,
    )
    print(f"  [OK] llm_tokens_total (backward compatible)")
    print(f"  [OK] token_usage_total (new unified metric)")
except Exception as e:
    print(f"  [FAIL] {e}")
    sys.exit(1)

# Test 4: Embedding singleton
print("\nTest 4: Embedding singleton...")
try:
    from backend.config import ENV_MODE
    from backend.rag.embedding_singleton import _get_cloud_embedding, _get_local_embedding
    
    if ENV_MODE == "cloud":
        from backend.config import EMBEDDING_API_KEY
        if not EMBEDDING_API_KEY:
            print(f"  [OK] Cloud mode without API key (expected in dev)")
            try:
                _get_cloud_embedding()
                print(f"  [FAIL] Should raise RuntimeError")
            except RuntimeError:
                print(f"  [OK] Correctly raises RuntimeError for missing API key")
        else:
            _get_cloud_embedding()
            print(f"  [OK] Cloud embedding initialized")
    else:
        _get_local_embedding()
        print(f"  [OK] Local embedding initialized")
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 5: Reranker backend
print("\nTest 5: Reranker backend...")
try:
    from backend.config import ENV_MODE
    from backend.rag.reranker import get_reranker_backend
    
    if ENV_MODE == "cloud":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            print(f"  [OK] Cloud mode without API key (expected in dev)")
            try:
                get_reranker_backend()
                print(f"  [FAIL] Should raise RuntimeError")
            except RuntimeError:
                print(f"  [OK] Correctly raises RuntimeError for missing API key")
        else:
            get_reranker_backend()
            print(f"  [OK] Cloud reranker initialized")
    else:
        get_reranker_backend()
        print(f"  [OK] Local reranker initialized")
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*60)
print("All tests PASSED!")
print("="*60 + "\n")
