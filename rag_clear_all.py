"""
彻底清理 RAG 向量索引的脚本

此脚本会：
1. 删除所有旧向量索引（Chroma + DocDB）
2. 删除 BM25 索引文件  
3. 保留 doc_registry.db 中的注册表记录
4. 提供完整重启指引

执行后系统会自动全量重建索引，然后你可以重新上传文档。
"""

import os
import shutil
from pathlib import Path
import sys

# 配置路径
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'backend' / 'data'

CHROMA_PATHS = [
    DATA_DIR / 'chroma',           # chunk 级向量库
    DATA_DIR / 'chroma_market',     # doc 级向量库
]

BM25_FILES = [
    DATA_DIR / 'bm25' / 'index.pkl',
    DATA_DIR / 'bm25' / 'corpus.pkl',
    DATA_DIR / 'bm25' / 'docs.pkl',
]

CHUNK_STORE_FILES = [
    DATA_DIR / 'chunk_store.db',
]

def confirm_action(prompt: str) -> bool:
    """交互式确认"""
    print(prompt)
    while True:
        choice = input("是否继续？(y/n): ").strip().lower()
        if choice in ['y', 'yes', '是']:
            return True
        elif choice in ['n', 'no', '否']:
            return False
        else:
            print("❌ 请输入 y 或 n")

def delete_directory(path: Path, name: str) -> bool:
    """删除目录"""
    if not path.exists():
        print(f"✅ {name} 不存在，跳过")
        return True
    
    try:
        shutil.rmtree(path)
        print(f"✅ 已删除 {name}: {path}")
        return True
    except Exception as e:
        print(f"❌ 删除 {name} 失败：{e}")
        return False

def delete_file(path: Path, name: str) -> bool:
    """删除文件"""
    if not path.exists():
        print(f"✅ {name} 不存在，跳过")
        return True
    
    try:
        path.unlink()
        print(f"✅ 已删除 {name}: {path}")
        return True
    except Exception as e:
        print(f"❌ 删除 {name} 失败：{e}")
        return False

def check_active_docs():
    """检查 doc_registry 中的活跃文档"""
    import sqlite3
    
    db_path = DATA_DIR / 'doc_registry.db'
    if not db_path.exists():
        print("⚠️  未找到 doc_registry.db")
        return []
    
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT doc_id, file_name, kb_id, status FROM doc_registry WHERE status != 'deleted'")
        rows = cur.fetchall()
        conn.close()
        
        if rows:
            print(f"\n📋 doc_registry 中有 {len(rows)} 个活跃文档:")
            for row in rows[:5]:  # 只显示前 5 个
                print(f"  - {row[1]} (kb_id={row[2]}, status={row[3]})")
            if len(rows) > 5:
                print(f"  ... 还有 {len(rows) - 5} 个文档")
        else:
            print("\n⚠️  doc_registry 中无活跃文档")
        
        return rows
    except Exception as e:
        print(f"❌ 读取 doc_registry 失败：{e}")
        return []

def main():
    print("=" * 70)
    print("🔧 RAG 向量索引彻底清理工具")
    print("=" * 70)
    print()
    
    # 1. 检查当前状态
    active_docs = check_active_docs()
    
    mode = None
    if not active_docs:
        print("\n📝 提示：没有活跃文档时，直接删除索引即可")
        mode = "conservative"
    else:
        print("\n⚠️  警告：检测到有活跃文档!")
        if confirm_action("""
选项 A - 保守模式（推荐）:
   - 只删除向量索引文件
   - 保留 doc_registry 注册表
   - 重启后系统会自动重建索引
   - 你的 23 个文档会恢复显示
"""):
            pass  # 用户选择继续
        else:
            print("\n❌ 操作已取消")
            return
        
        if confirm_action("""
选项 B - 彻底清理模式:
   - 删除所有索引文件和注册表
   - 完全清空知识库
   - 需要你手动重新上传所有文档
"""):
            mode = "complete"
        else:
            mode = "conservative"
    
    print("\n" + "=" * 70)
    print("开始清理...")
    print("=" * 70)
    
    # 2. 删除向量索引
    success_count = 0
    total_count = 0
    
    for path in CHROMA_PATHS:
        total_count += 1
        if delete_directory(path, "Chroma 向量库"):
            success_count += 1
    
    # 3. 删除 BM25 索引
    for file_info in BM25_FILES:
        total_count += 1
        if isinstance(file_info, tuple):
            path, name = file_info
        else:
            path = file_info
            name = "BM25 索引"
        if delete_file(path, name):
            success_count += 1
    
    # 4. 删除 chunk_store
    for file_info in CHUNK_STORE_FILES:
        total_count += 1
        if delete_file(file_info, "Chunk Store"):
            success_count += 1
    
    # 5. 如果是彻底清理模式，也删除注册表
    if mode == "complete":
        if delete_file(DATA_DIR / 'doc_registry.db', "Doc Registry DB"):
            success_count += 1
        print("\n✅ 注册表已删除，知识库完全清空")
    
    print("\n" + "=" * 70)
    print(f"✅ 清理完成！成功删除 {success_count}/{total_count} 个索引文件")
    print("=" * 70)
    
    # 6. 提供后续操作指引
    print("\n📝 下一步操作:")
    print("-" * 70)
    
    if mode == "conservative":
        print("""
方式 1 - 自动重建（推荐）:
   1. 重启后端服务：python -m backend.app.main
   2. 等待启动完成，日志显示"全量重建向量库"
   3. 刷新前端 http://localhost:3000/knowledge/documents
   4. 你的 23 个文档应该会显示

方式 2 - 手动重新上传:
   1. 如果方式 1 后文档不显示，在/documents 页面点击"删除"按钮
   2. 然后重新上传这些文档
   3. 系统会使用新的 API embedding model 构建新索引
        """)
    else:
        print("""
完全清空后的操作步骤:

1. 重启后端服务:
   python -m backend.app/main

2. 访问前端页面:
   http://localhost:3000/knowledge/documents

3. 上传文档:
   - 点击"上传文档"按钮
   - 选择你要的文件
   - 等待上传和索引完成（可能需要几分钟）

4. 验证功能:
   - 在问答框提问测试
   - 查看返回结果是否正确引用了上传的文档
        """)
    
    print("\n🛑 注意事项:")
    print("-" * 70)
    print("• 重建索引期间问答功能会降级（返回空结果）")
    print("• 首次重建可能需要 5-10 分钟（取决于文档数量）")
    print("• 建议查看后端日志确认重建进度")
    print("• 如果遇到问题，可以重启后端服务重试")
    print("=" * 70)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ 用户取消操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n💥 错误：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
