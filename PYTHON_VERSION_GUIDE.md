# Python 版本选择指南

## 当前环境：Python 3.10.2 ✅ 已验证可用
- `D:\Python\python.exe` (系统默认 Python)
- `.venv` 虚拟环境基于 Python 3.10.2
- 所有依赖库均正常工作

## ⚠️ 升级到 Python 3.13 ❌ 不推荐
### 问题：
1. **PyTorch 在 Windows 无 3.13 预编译 wheel**
   - 需要源码编译（需配置 MSVC、耗时 >2 小时）
   - 失败率极高
2. **sentence-transformers 依赖 PyTorch**
   - 会连带崩溃
3. **影响 RAG/Embedding 核心功能**
   - chromadb embedding 全部失效

## ✅ 可选：升级到 Python 3.12（折中方案）
### 好处：
- 比 3.10 快 ~5%
- 有成熟的 3.12 wheel 包
- LangChain、PyTorch 都支持良好

### 操作步骤：
```bash
# 删除现有 venv
rmdir /s .venv

# 安装 Python 3.12（官网下载安装程序）

# 重新创建 venv
python3.12 -m venv .venv

# 激活并安装依赖
.venv\Scripts\activate
pip install -r requirements-lock.txt

# 验证
python -c "import torch, sentence_transformers; print('OK')"
```

### 风险：
- 仍有 10-20% 概率遇到兼容性问题
- 某些冷门库可能不支持 3.12
- 需要测试所有工作流

## 🎯 推荐做法
1. **生产环境**：保持 Python 3.10，稳定优先
2. **开发环境**：可以创建一个 Python 3.12 分支试试
3. **不要在生产环境直接升级**

---

## 📝 双 Python 管理技巧

### 启动脚本统一使用 .venv
- `start.bat` - 自动使用虚拟环境
- `start_all.bat` - 传统方式（也用了 .venv）

### IDE 配置
- VSCode: Command Palette → "Python: Select Interpreter" → ".venv"
- PyCharm: Settings → Project → Python Interpreter → ".venv"

### 命令行
```powershell
# PowerShell
& ".venv\Scripts\Activate.ps1"
python your_script.py  # 现在用的是 .venv 的 Python

# CMD
call .venv\Scripts\activate.bat
python your_script.py
```
