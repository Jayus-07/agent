import sqlite3

conn = sqlite3.connect('data/doc_registry.db')
cursor = conn.cursor()

# 查询最新的两个员工相关文档
cursor.execute('''
SELECT doc_id, file_name, doc_type, business_domain, confidence 
FROM doc_registry 
WHERE file_name LIKE '%员工%' 
ORDER BY created_at DESC 
LIMIT 2
''')

rows = cursor.fetchall()

print("\n" + "="*80)
print("Latest Employee Documents Classification Check")
print("="*80)

for i, row in enumerate(rows, 1):
    doc_id, file_name, doc_type, business_domain, confidence = row
    
    print(f"\n{i}. {file_name}")
    print(f"   Doc ID: {doc_id}")
    print(f"   文档类型 (doc_type): {doc_type}")
    print(f"   业务领域 (business_domain): {business_domain}")
    print(f"   置信度：{confidence:.2%}")
    
    # 判断分类是否正确
    if "hr" in file_name.lower() or "手册" in file_name:
        expected_type = ["policy", "sop", "legal"]
        actual_correct = doc_type in expected_type
        status = "CORRECT" if actual_correct else "INCORRECT"
        print(f"   Expected types: {expected_type}")
        print(f"   Actual result: [{status}]")
        
    elif "finance" in file_name.lower() or "报销" in file_name:
        expected_type = ["financial"]
        actual_correct = doc_type == "financial"
        status = "CORRECT" if actual_correct else "INCORRECT"
        print(f"   Expected types: {expected_type}")
        print(f"   Actual result: [{status}]")

print("\n" + "="*80)

# 再查询 trace_store 获取更详细的信息
try:
    import json
    conn_trace = sqlite3.connect('data/trace_store.db')
    cursor_trace = conn_trace.cursor()
    
    print("\nTrace Details:")
    print("-"*80)
    
    for i, row in enumerate(rows[:2], 1):
        doc_id = row[0]
        
        # 查找该文档相关的 trace
        cursor_trace.execute('''
        SELECT data FROM trace_store WHERE data LIKE ? ORDER BY created_at DESC LIMIT 1
        ''', (f'%{doc_id}%',))
        
        trace_result = cursor_trace.fetchone()
        
        if trace_result:
            trace_data = json.loads(trace_result[0])
            spans = trace_data.get('spans', [])
            
            print(f"\n{i}. {rows[i-1][1]} Trace:")
            print(f"   Total Spans: {len(spans)}")
            
            # Look for metadata generation span
            for span in spans:
                if 'metadata' in span.get('name', '').lower() or 'classify' in span.get('name', '').lower():
                    print(f"   - Span: {span.get('name')} ({span.get('duration_ms')}ms)")
                    metrics = span.get('metrics', {})
                    if isinstance(metrics, dict):
                        print(f"     Metrics: doc_type={metrics.get('doc_type')}, confidence={metrics.get('confidence')}")
        else:
            print(f"\n{i}. {rows[i-1][1]} Trace: NOT FOUND")
    
except Exception as e:
    print(f"\nWARNING: Cannot read trace data: {e}")

conn.close()
conn_trace.close()
