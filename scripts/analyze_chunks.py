"""分析文档切分质量 - 对比优化前后效果"""

import sqlite3
import json

conn = sqlite3.connect('data/chunk_store.db')
cursor = conn.cursor()

# 获取最新的两个文档的 doc_id
cursor.execute('''
SELECT file_name, doc_id FROM doc_registry 
WHERE file_name LIKE '%员工%' 
ORDER BY created_at DESC 
LIMIT 2
''')

doc_rows = cursor.fetchall()

print("="*80)
print("📊 DOCUMENT CHUNK QUALITY ANALYSIS")
print("="*80)

for i, (file_name, doc_id) in enumerate(doc_rows, 1):
    print(f"\n{i}. {file_name}")
    print("-"*80)
    
    # 获取该文档的所有 chunk
    try:
        conn_chunk = sqlite3.connect('data/doc_3387d87030_vector.db')
        cursor_chunk = conn_chunk.cursor()
        
        cursor_chunk.execute('''
        SELECT chunk_id, page_content, metadata FROM vector 
        WHERE doc_id = ?
        ORDER BY chunk_index
        ''', (doc_id,))
        
        chunks = cursor_chunk.fetchall()
        
        if not chunks:
            print("   No chunks found!")
            continue
        
        print(f"   Total Chunks: {len(chunks)}")
        
        # 分析每个 chunk
        print("\n   Chunk Details:")
        print("   " + "-"*76)
        
        for j, (chunk_id, content, metadata) in enumerate(chunks, 1):
            # 解析 metadata
            try:
                meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            except:
                meta = {}
            
            # 计算 chunk 特征
            char_count = len(content)
            word_count = len(content.split())
            line_count = content.count('\n') + 1
            
            # 判断质量
            quality_score = "GOOD"
            issues = []
            
            if char_count < 50:
                quality_score = "TOO_SHORT"
                issues.append("very short")
            elif char_count > 2000:
                quality_score = "TOO_LONG"
                issues.append("too long")
            
            if line_count == 1:
                issues.append("no paragraph breaks")
            
            if meta.get('source_file') and 'FAQ' in meta.get('source_file', ''):
                expected_qa = 'Q:' in content or 'A:' in content
                if not expected_qa and len(content) > 100:
                    quality_score = "MAYBE_BAD_SPLIT"
                    issues.append("expected QA format but plain text")
            
            status_symbol = "✅" if quality_score == "GOOD" else "⚠️"
            
            print(f"\n   [{status_symbol}] Chunk {j}:")
            print(f"       ID: {chunk_id[:16]}...")
            print(f"       Length: {char_count} chars | Words: {word_count} | Lines: {line_count}")
            print(f"       Quality: [{quality_score}]")
            
            if issues:
                print(f"       Issues: {', '.join(issues)}")
            
            # 显示内容预览 (最多 200 字符)
            preview = content.replace('\n', ' ').replace('\r', ' ')[:200]
            preview = preview.strip()
            if len(content) > 200:
                preview += "..."
            
            print(f"       Preview: \"{preview}\"")
        
        # 统计信息
        print(f"\n   📈 Chunk Statistics:")
        all_lengths = [len(c[1]) for c in chunks]
        avg_length = sum(all_lengths) / len(all_lengths)
        min_length = min(all_lengths)
        max_length = max(all_lengths)
        
        print(f"       Avg Length: {avg_length:.1f} chars")
        print(f"       Min Length: {min_length} chars")
        print(f"       Max Length: {max_length} chars")
        print(f"       Std Dev: {sum((x - avg_length)**2 for x in all_lengths)**0.5 / len(all_lengths):.1f}")
        
        conn_chunk.close()
        
    except Exception as e:
        print(f"   ERROR: Cannot read chunks - {e}")

print("\n" + "="*80)
print("ANALYSIS COMPLETE")
print("="*80)
