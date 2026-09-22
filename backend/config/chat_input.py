"""config/chat_input.py — 聊天输入限制配置（Chat/RAG Production Closure，2026-09-22）

区分两个概念（勿混）：
- **产品输入限制**（本模块）：聊天入口的防滥用硬上限 —— 防超大请求/恶意
  payload/内存与日志/DB 膨胀/SSE 长期阻塞。
- **LLM Context 预算**（config/memory.py + context_budget/）：模型窗口内的
  active prompt 预算管理。超产品限制的请求根本进不了 Agent。

取值依据（2026-09-22 实测口径）：
- LLM_CONTEXT_LENGTH=8192（.env），input_budget=8192-768-256=7168 tokens。
- Chat 定位是「较长问题 / 配置 / 代码片段 / 业务说明 / 结构化需求」
  （知识文件走 /api/rag/upload，见 docs/chat-rag-closure 报告）：
  20000 chars 对英文/代码 ≈ 5K tokens（窗口内），对纯中文 ≈ 10-12K tokens
  （超窗时由 Context Budget preflight 确定性降级，不崩溃）。
- APISIX client_max_body_size=0（nginx 层不限），产品限制以 app 层为权威。
"""
import os

# 单条聊天输入最大字符数（Pydantic max_length + 前端计数器对齐）
CHAT_INPUT_MAX_CHARS = int(os.getenv("CHAT_INPUT_MAX_CHARS", "20000"))
# 单条聊天输入最大字节数（UTF-8 请求体；20000 纯中文 ≈ 60KB，留量取 64KB）
CHAT_INPUT_MAX_BYTES = int(os.getenv("CHAT_INPUT_MAX_BYTES", "65536"))
# 单条聊天输入最大 token 数（Input Guard 防滥用口径；20000 纯中文 ≈ 10K+
# tokens 会命中此档 —— 超长粘贴应走知识库上传，不再进 Agent）
CHAT_INPUT_MAX_TOKENS = int(os.getenv("CHAT_INPUT_MAX_TOKENS", "8000"))
