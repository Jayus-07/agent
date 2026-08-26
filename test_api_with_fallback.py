"""Test DashScope Reranker with Fallback to Local Model"""
import sys
sys.path.insert(0, 'backend')

from rag.reranker import get_reranker_backend, LocalModelLoader, rerank
from langchain_core.documents import Document

print('========================================')
print('DashScope Reranker Test with Fallback')
print('========================================')

# Check backend
print('\n[TEST 1] Backend Selection')
backend = get_reranker_backend()
backend_type = type(backend).__name__
print(f'Backend: {backend_type}')

from rag.reranker import DashScopeReranker, LocalCrossEncoderBackend

if isinstance(backend, DashScopeReranker):
    print('Status: Using DashScope API')
else:
    print('Status: Using Local Model')

# Try reranking
print('\n[TEST 2] Rerank Query')
docs = [
    Document(page_content='Python is a programming language', metadata={'source': 'doc1'}),
    Document(page_content='Machine learning uses Python extensively', metadata={'source': 'doc2'})
]
query = 'What is Python?'

try:
    result = rerank(query, docs, top_k=2)
    print(f'SUCCESS: Got {len(result)} results')
    for i, (doc, score) in enumerate(result, 1):
        print(f'{i}. score={score:.4f}')
except Exception as e:
    print(f'ERROR: {e}')
    print('\nAPI Call failed. System should fallback to local model.')

print('\n========================================')
