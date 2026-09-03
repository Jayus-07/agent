"""Check which chunks of doc 14aa8a1c7a contain '90000' and/or '玖万'."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from chromadb import PersistentClient

client = PersistentClient(path="data/chroma")
for col in client.list_collections():
    result = col.get(where={"doc_id": "14aa8a1c7a"})
    if not result["ids"]:
        continue
    print(f"Collection: {col.name}, {len(result['ids'])} chunks")
    for i, (doc_id, doc, meta) in enumerate(zip(result["ids"], result["documents"], result["metadatas"])):
        has_90k = "90000" in doc
        has_jw = "玖万" in doc
        marker = ""
        if has_90k:
            marker += " [HAS 90000]"
        if has_jw:
            marker += " [HAS JIWAN]"
        print(f"  [{i}] chunk_idx={meta.get('chunk_index','?')}{marker}")
        print(f"       {doc[:150].replace(chr(10), ' ')}")
