"""
文档版本管理工具
用于检测文件变化，实现增量更新向量数据库
"""
import os
import json
import hashlib
from datetime import datetime
from typing import Dict, Optional
from utils.logger import logger

VERSION_FILE = "data/doc_versions.json"


def load_version_index() -> Dict[str, dict]:
    """加载文档版本索引"""
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"版本索引加载失败: {e}，创建新索引")
            return {}
    return {}


def save_version_index(version_index: Dict[str, dict]):
    """保存文档版本索引"""
    os.makedirs(os.path.dirname(VERSION_FILE), exist_ok=True)
    with open(VERSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(version_index, f, ensure_ascii=False, indent=2)
    logger.debug(f"💾 版本索引已保存: {len(version_index)} 个文档")


def get_file_info(file_path: str) -> dict:
    """
    获取文件信息（用于版本比对）
    
    Returns:
        {
            "file_path": 文件路径,
            "file_size": 文件大小,
            "modified_time": 修改时间戳,
            "hash": 文件内容哈希（可选，更精确但较慢）
        }
    """
    stat = os.stat(file_path)
    return {
        "file_path": file_path,
        "file_size": stat.st_size,
        "modified_time": stat.st_mtime,
        "modified_time_str": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def check_file_changed(file_path: str, version_index: Dict[str, dict]) -> bool:
    """
    检查文件是否发生变化
    
    Args:
        file_path: 文件路径
        version_index: 版本索引
    
    Returns:
        True 表示文件已变化或不存在于索引中
    """
    if file_path not in version_index:
        return True  # 新文件
    
    old_info = version_index[file_path]
    new_info = get_file_info(file_path)
    
    # 比对文件大小和修改时间
    if (old_info.get("file_size") != new_info["file_size"] or
        old_info.get("modified_time") != new_info["modified_time"]):
        logger.info(f"📝 检测到文件变化: {os.path.basename(file_path)}")
        return True
    
    return False


def update_version_index(version_index: Dict[str, dict], file_path: str, doc_id: str):
    """
    更新版本索引
    
    Args:
        version_index: 版本索引字典
        file_path: 文件路径
        doc_id: 文档ID
    """
    file_info = get_file_info(file_path)
    file_info["doc_id"] = doc_id
    file_info["indexed_time"] = datetime.now().isoformat()
    version_index[file_path] = file_info


def remove_from_version_index(version_index: Dict[str, dict], file_path: str):
    """从版本索引中移除文件"""
    if file_path in version_index:
        del version_index[file_path]
        logger.debug(f"🗑️ 从版本索引移除: {os.path.basename(file_path)}")


def scan_directory_for_changes(directory_path: str, version_index: Dict[str, dict]) -> tuple:
    """
    扫描目录，找出新增、修改、删除的文件
    
    Returns:
        (new_files, modified_files, deleted_files)
    """
    new_files = []
    modified_files = []
    deleted_files = []
    
    # 当前目录中的所有文件
    current_files = set()
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            file_path = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()
            if ext in [".txt", ".md", ".pdf"]:
                current_files.add(file_path)
                
                if check_file_changed(file_path, version_index):
                    if file_path in version_index:
                        modified_files.append(file_path)
                    else:
                        new_files.append(file_path)
    
    # 找出已删除的文件
    for file_path in version_index:
        if file_path not in current_files:
            deleted_files.append(file_path)
    
    if new_files:
        logger.info(f"🆕 新增文件: {len(new_files)} 个")
    if modified_files:
        logger.info(f"📝 修改文件: {len(modified_files)} 个")
    if deleted_files:
        logger.info(f"🗑️ 删除文件: {len(deleted_files)} 个")
    
    return new_files, modified_files, deleted_files


if __name__ == "__main__":
    # 测试
    index = load_version_index()
    new, modified, deleted = scan_directory_for_changes("data/docs", index)
    print(f"新增: {new}")
    print(f"修改: {modified}")
    print(f"删除: {deleted}")

