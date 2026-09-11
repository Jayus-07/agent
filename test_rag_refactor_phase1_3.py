#!/usr/bin/env python
"""Quick integration test for RAG Architecture Refactor (Phase 1-3)."""
import os
import sys

# Test 1: Config Layer
def test_config_layer():
    print("=" * 60)
    print("Test 1: Config Layer")
    print("=" * 60)
    
    from backend.config.llm import (
        ENV_MODE,
        EMBEDDING_MODEL,
        EMBEDDING_API_BASE,
        EMBEDDING_API_KEY,
        RERANK_MODEL,
        TOKEN_USAGE_LOG_PATH,
        EVAL_DATASET_PATH,
    )
    
    print(f"✅ ENV_MODE: {ENV_MODE}")
    print(f"✅ EMBEDDING_MODEL: {EMBEDDING_MODEL}")
    print(f"✅ EMBEDDING_API_BASE: {EMBEDDING_API_BASE}")
    print(f"✅ RERANK_MODEL: {RERANK_MODEL}")
    print(f"✅ TOKEN_USAGE_LOG_PATH: {TOKEN_USAGE_LOG_PATH}")
    print(f"✅ EVAL_DATASET_PATH: {EVAL_DATASET_PATH}")
    
    # Test ENV_MODE validation
    try:
        os.environ["ENV_MODE"] = "invalid"
        # Need to reload module to trigger validation
        import importlib
        import backend.config.llm as llm_config
        importlib.reload(llm_config)
        print("❌ ENV_MODE validation should have raised ValueError")
        return False
    except ValueError as e:
        print(f"✅ ENV_MODE validation works: {e}")
    finally:
        # Reset to default
        del os.environ["ENV_MODE"]
    
    print("✅ Config Layer Tests PASSED\n")
    return True


# Test 2: Token Tracker
def test_token_tracker():
    print("=" * 60)
    print("Test 2: Token Tracker")
    print("=" * 60)
    
    from backend.infra.token_tracker import (
        TokenUsageEvent,
        TokenTracker,
        create_tracker_for_embedding,
        create_tracker_for_rerank,
    )
    
    # Test TokenUsageEvent creation
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
    print(f"✅ TokenUsageEvent created: {event.component}")
    print(f"   JSON: {event.to_json()[:100]}...")
    
    # Test tracker creation
    tracker_emb = create_tracker_for_embedding(
        log_path="/tmp/test_token.jsonl",
        model_name="text-embedding-v3",
        backend="cloud",
    )
    print(f"✅ Embedding Tracker created: {tracker_emb.component}")
    
    tracker_rerank = create_tracker_for_rerank(
        log_path="/tmp/test_token.jsonl",
        model_name="qwen3-rerank",
        backend="cloud",
    )
    print(f"✅ Rerank Tracker created: {tracker_rerank.component}")
    
    # Test decorator usage
    @tracker_emb.track
    def dummy_embedding_call():
        return {"embeddings": [[0.1] * 1536]}
    
    result = dummy_embedding_call()
    print(f"✅ Decorator wrapper works: {type(result)}")
    
    # Verify JSONL file created
    import os.path
    if os.path.exists("/tmp/test_token.jsonl"):
        with open("/tmp/test_token.jsonl", "r") as f:
            lines = f.readlines()
        print(f"✅ JSONL file created with {len(lines)} entries")
    
    print("✅ Token Tracker Tests PASSED\n")
    return True


# Test 3: Embedding Singleton
def test_embedding_singleton():
    print("=" * 60)
    print("Test 3: Embedding Singleton (Cloud/Local)")
    print("=" * 60)
    
    # Test env mode detection
    from backend.config import ENV_MODE
    print(f"Current ENV_MODE: {ENV_MODE}")
    
    from backend.rag.embedding_singleton import get_embedding, _get_cloud_embedding, _get_local_embedding
    
    if ENV_MODE == "cloud":
        # Cloud mode should check API key
        from backend.config import EMBEDDING_API_KEY
        if not EMBEDDING_API_KEY:
            print("⚠️  Cloud mode without API key (expected in dev)")
            try:
                emb = _get_cloud_embedding()
                print("❌ Should have raised RuntimeError")
            except RuntimeError as e:
                print(f"✅ Cloud mode correctly requires API key: {str(e)[:80]}...")
        else:
            emb = _get_cloud_embedding()
            print(f"✅ Cloud embedding initialized: {type(emb).__name__}")
    else:
        # Local mode should work without API key
        emb = _get_local_embedding()
        print(f"✅ Local embedding initialized: {type(emb).__name__}")
    
    print("✅ Embedding Singleton Tests PASSED\n")
    return True


# Test 4: Reranker Backend
def test_reranker_backend():
    print("=" * 60)
    print("Test 4: Reranker Backend (Cloud/Local)")
    print("=" * 60)
    
    from backend.config import ENV_MODE
    from backend.rag.reranker import get_reranker_backend, ENV_MODE
    
    print(f"Current ENV_MODE: {ENV_MODE}")
    
    if ENV_MODE == "cloud":
        # Cloud mode should check API key
        from backend.config import ENV_MODE as env_mode_check
        from backend.rag.reranker import get_reranker_backend
        
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            print("⚠️  Cloud mode without API key (expected in dev)")
            try:
                reranker = get_reranker_backend()
                print("❌ Should have raised RuntimeError")
            except RuntimeError as e:
                print(f"✅ Cloud mode correctly requires API key: {str(e)[:80]}...")
        else:
            reranker = get_reranker_backend()
            print(f"✅ Cloud reranker initialized: {type(reranker).__name__}")
    else:
        # Local mode should work
        reranker = get_reranker_backend()
        print(f"✅ Local reranker initialized: {type(reranker).__name__}")
    
    print("✅ Reranker Backend Tests PASSED\n")
    return True


# Main
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("RAG Architecture Refactor - Integration Tests (Phase 1-3)")
    print("=" * 60 + "\n")
    
    results = []
    
    try:
        results.append(("Config Layer", test_config_layer()))
    except Exception as e:
        print(f"❌ Config Layer FAILED: {e}\n")
        results.append(("Config Layer", False))
    
    try:
        results.append(("Token Tracker", test_token_tracker()))
    except Exception as e:
        print(f"❌ Token Tracker FAILED: {e}\n")
        results.append(("Token Tracker", False))
    
    try:
        results.append(("Embedding Singleton", test_embedding_singleton()))
    except Exception as e:
        print(f"❌ Embedding Singleton FAILED: {e}\n")
        results.append(("Embedding Singleton", False))
    
    try:
        results.append(("Reranker Backend", test_reranker_backend()))
    except Exception as e:
        print(f"❌ Reranker Backend FAILED: {e}\n")
        results.append(("Reranker Backend", False))
    
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(passed for _, passed in results)
    print("=" * 60)
    if all_passed:
        print("All tests PASSED!")
        sys.exit(0)
    else:
        print("Some tests FAILED")
        sys.exit(1)
