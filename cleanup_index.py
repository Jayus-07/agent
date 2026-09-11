"""
一键清理所有旧向量索引并重新建立的工具脚本

此脚本用于在 embedding model/backend 变更后清理旧索引，确保新模型正确加载。
"""

import os
import shutil
from pathlib import Path

# 配置路径
BASE_DIR = Path(__file__).parent
CHROMA_PATH = BASE_DIR / 'backend' / 'data' / 'chroma'
DOC_DB_PATH = BASE_DIR / 'backend' / 'data' / 'chroma_market'

def cleanup_chroma_index():
    """清理 Chroma 索引目录"""
    print("=" * 60)
    print("RAG Index Cleanup Tool")
    print("=" * 60)
    
    # 检查目录是否存在
    if not CHROMA_PATH.exists():
        print(f"✅ Chroma 目录不存在：{CHROMA_PATH}")
    else:
        # 备份（可选）
        backup_path = CHROMA_PATH.parent / 'chroma_backup_before_switch_model'
        if backup_path.exists():
            print(f"\n⚠️  发现旧备份：{backup_path}")
            choice = input("是否删除旧备份？(y/n): ").strip().lower()
            if choice == 'y':
                shutil.rmtree(backup_path)
                print("✅ 已删除旧备份")
        
        # 删除现有索引
        if input("\n🗑️  确认删除 Chroma 索引吗？(y/n): ").strip().lower() == 'y':
            shutil.rmtree(CHROMA_PATH)
            print(f"✅ 已删除 Chroma 索引：{CHROMA_PATH}")
        else:
            print("❌ 已取消删除")
            return False
    
    if not DOC_DB_PATH.exists():
        print(f"✅ Doc DB 目录不存在：{DOC_DB_PATH}")
    else:
        if input("\n🗑️  确认删除 Doc DB 索引吗？(y/n): ").strip().lower() == 'y':
            shutil.rmtree(DOC_DB_PATH)
            print(f"✅ 已删除 Doc DB 索引：{DOC_DB_PATH}")
        else:
            print("❌ 已取消删除")
            return False
    
    # 清理后提示
    print("\n" + "=" * 60)
    print("✅ 清理完成！请按以下步骤操作：")
    print("=" * 60)
    print("1. 重启后端服务 (python -m backend.app.main)")
    print("2. 系统会自动重新构建向量索引")
    print("3. 所有 23 个活跃文档将使用新的 embedding model 重新索引")
    print("\n📝 注意:")
    print("- 重建索引可能需要几分钟，取决于文档数量")
    print("- 期间查询功能将降级为返回空结果")
    print("- 启动日志会显示 '全量重建向量库' 表示正在重建")
    print("=" * 60)
    
    return True

if __name__ == '__main__':
    try:
        cleanup_chroma_index()
    except KeyboardInterrupt:
        print("\n\n❌ 用户取消操作")
    except Exception as e:
        print(f"\n💥 错误：{e}")
        import traceback
        traceback.print_exc()
