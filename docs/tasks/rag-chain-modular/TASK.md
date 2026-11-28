# Task: Tier 14 RAG Chain 模块化解耦重构 (chain.py)

## 1. 目标
对 1924 行的超大单体 `backend/rag/chain.py` 进行物理拆解与解耦重构，消除上帝类与超长方法（如 `ask()`、`_build_chains()` 等），抽离专职微模块。
保持 100% 存量公开/内部契约与 monkeypatch 穿透兼容，门面 `chain.py` 降至 250 行以内（削减 >85%）。

## 2. 架构模式与范围
- 抽离 `backend/rag/chain_modules/`:
  - `models.py`: 数据契约、常量与上下文数据结构；
  - `prompts.py`: Prompt 模板组装、格式化与重载钩子；
  - `retrieval.py`: 检索器构建、多路召回、重排与 Evidence Gate 包装；
  - `verification.py`: 事实校验、Claim 提取与自我纠错；
  - `pipeline.py`: 执行管道阶段编排（prepare -> execute -> verify -> evaluate -> finish）；
  - `streaming.py`: 流式输出生成器与事件投递；
- 门面 `backend/rag/chain.py`:
  - 保留 `RAGChain` 门面类，聚合各个阶段，对外保持完全相同的属性与方法调用契约；
- 架构守卫测试：
  - 编写 `backend/tests/rag/test_rag_chain_modular_architecture.py`，并加入 `.github/workflows/rag_smoke.yml`。
