"""Final validation test with actual token tracking."""
import os
import sys

print("\n" + "="*60)
print("Final Integration Test - Token Tracking")
print("="*60 + "\n")

from backend.config import ENV_MODE, TOKEN_USAGE_LOG_PATH
from backend.infra.token_tracker import create_tracker_for_embedding

# Create tracker
tracker = create_tracker_for_embedding(
    log_path=str(TOKEN_USAGE_LOG_PATH),
    model_name="text-embedding-v3",
    backend=ENV_MODE,
)

# Test decorated function
@tracker.track
def mock_embedding_call():
    """Mock embedding API call."""
    return {"embeddings": [[0.1] * 1536]}

# Execute
print(f"Running mock embedding call...")
result = mock_embedding_call()
print(f"[OK] Result: {result}")

# Check JSONL
import os.path
jsonl_path = str(TOKEN_USAGE_LOG_PATH)
if os.path.exists(jsonl_path):
    print(f"\n[OK] JSONL file created: {jsonl_path}")
    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        if lines:
            print(f"[OK] Entries written: {len(lines)}")
            print(f"[OK] First entry:")
            for line in lines[:1]:
                # Pretty print JSON
                import json
                obj = json.loads(line)
                print(f"   component: {obj.get('component')}")
                print(f"   model: {obj.get('model_name')}")
                print(f"   backend: {obj.get('backend')}")
                print(f"   total_tokens: {obj.get('total_tokens')} (available: {obj.get('token_usage_available')})")
                print(f"   duration_ms: {obj.get('duration_ms')}")
                print(f"   status: {obj.get('status')}")
else:
    print(f"\n[FAIL] JSONL not found at {jsonl_path}")
    sys.exit(1)

print("\n" + "="*60)
print("Final Test PASSED!")
print("="*60 + "\n")
