"""DashScope Reranker API Quick Test"""
import sys
sys.path.insert(0, 'backend')

from rag.reranker import get_reranker_backend, LocalModelLoader
from langchain_core.documents import Document

print('========================================')
print('DashScope Reranker API Quick Test')
print('========================================')

# Test backend selection
print('\n[Test 1] Backend Selection')
backend = get_reranker_backend()
backend_type = type(backend).__name__
print(f'Backend type: {backend_type}')

# Check if using API or local
from rag.reranker import DashScopeReranker, LocalCrossEncoderBackend

if isinstance(backend, DashScopeReranker):
    print('Status: Using DashScope API (GOOD)')
elif isinstance(backend, LocalCrossEncoderBackend):
    print('Status: Using Local Model (check API Key config)')

# Test rerank
print('\n[Test 2] Reorder Query')
docs = [
    Document(page_content='Python is a programming language', metadata={'source': 'doc1'}),
    Document(page_content='Machine learning uses Python extensively', metadata={'source': 'doc2'})
]
query = 'What is Python?'
print(f'Query: {query}')
print(f'Docs: {len(docs)}')

result = __import__('backend.rag.reranker', fromlist=['rerank']).rerank(query, docs, top_k=2)

print(f'\nResult: {len(result)} docs returned')
for i, (doc, score) in enumerate(result, 1):
    print(f'{i}. score={score:.4f} source={doc.metadata.get("source")}')

print('\nTest complete!')
print('Check logs above for API usage details.')
print('========================================')
