# 文件上传功能测试与审查报告

> 日期:2026-08-20
> 范围:`backend/app/api/routes/rag_upload.py` 及其依赖链(MIME 校验、文件锁、临时文件清理、操作日志)
> 方法:三步走 — 先测试(特征化安全网)→ 再审查 → 最后改造
> 背景:项目由多个智能体编写,上传模块核心逻辑此前几乎无测试覆盖

---

## 1. 执行摘要

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 上传模块测试用例 | 34(仅覆盖 3 个纯辅助函数) | **68**(覆盖核心上传逻辑 + HTTP 端点) |
| `sync_upload_impl` 覆盖率 | 近乎为零 | 7 大路径全覆盖 |
| 确认并修复的 bug | — | **2 个**(F1 临时文件泄漏、G7 首次索引 doc_id 丢失) |
| 代码质量改进 | — | 4 项(F2–F5) |
| 回归引入 | — | **0**(stash 对照验证) |
| 全量套件状态 | 801 通过 / 36 失败(另 9 模块收集失败) | **887 通过 / 0 失败**(含上传→入库 e2e,见第 7/8 章) |

**结论**:上传模块的安全防线(路径穿越、MIME、魔数、并发锁)设计正确且经测试验证有效;发现并修复 1 个资源泄漏 bug;识别出 7 项遗留问题(跨模块/环境类),已记录待后续处理。

---

## 2. 第一步:测试(建立安全网)

### 2.1 已有测试基线(改造前 34 个,全部通过)

| 文件 | 覆盖 | 用例数 |
|---|---|---|
| `test_rag_upload_mime.py` | `_validate_mime` 纯函数 | 20 |
| `test_rag_upload_concurrency.py` | 文件锁 acquire/release + 并发语义 | 6 |
| `test_rag_upload_cleanup_overwrite.py` | `_cleanup_failed_upload` 覆盖场景保护 | 8 |

### 2.2 新增测试(`test_rag_upload_sync_impl.py`,34 个用例)

| 测试组 | 用例数 | 覆盖内容 |
|---|---|---|
| TestPathTraversal | 6 | `../`、反斜杠穿越、点文件、空文件名;断言文件绝不逃逸出 docs 目录 |
| TestSizeLimit | 3 | 流式超限拒绝、**超限后临时文件清理**、边界值 |
| TestEmptyFile | 2 | 空文件拒绝 + 临时文件清理 |
| TestMagicNumber | 5 | 假 PDF/DOCX 拒绝、真魔数放行、md 无魔数校验(特征锁定) |
| TestChineseFilename | 2 | latin-1 mojibake 回编 utf-8、ASCII 文件名不变 |
| TestSuccessFlow | 5 | 返回结构完整性、atomic rename、覆盖检测、进度队列、多块流式完整性 |
| TestSha256OfFile | 4 | 流式哈希正确性(含跨块边界、空文件、缺失文件) |
| TestUploadEndpoint | 6 | HTTP 端点:成功/KB 校验/MIME 拒绝/octet-stream 拒绝/超大预检/503 |
| TestUploadStreamEndpoint | 1 | SSE 流未知 upload_id 错误事件 |

### 2.3 安全防线验证结果

| 防线 | 状态 |
|---|---|
| 路径穿越(basename + realpath + commonpath 三重防御) | ✅ 有效 |
| MIME 白名单 + 显式 octet-stream 拒绝 | ✅ 有效 |
| 魔数校验(PDF `%PDF-` / DOCX `PK\x03\x04`) | ✅ 有效 |
| Content-Length 预检 + 流式双保险大小限制 | ✅ 有效 |
| 同文件并发上传文件锁 | ✅ 有效(已有测试) |
| 覆盖场景不删源文件(P0-X) | ✅ 有效(已有测试) |
| 审计日志参数化 SQL(无注入) | ✅ 有效 |

---

## 3. 第二步:审查发现

### 3.1 已修复(第三步)

| # | 问题 | 等级 | 修复方式 |
|---|---|---|---|
| F1 | **超限拒绝分支不清理临时文件** — `with` 块内直接 `return`,句柄关闭但文件留在磁盘,反复上传大文件持续泄漏 tmp_dir | 🔴 bug | 超限分支补 `f.close()` + `os.unlink(tmp_path)`;红色测试转绿 |
| F2 | 死代码 `except Exception as e: raise`(无效 try/except) | 🟢 | 删除,同时规范缩进结构 |
| F3 | `ALLOWED_MIME_TYPES` 含永不可达的 octet-stream 条目(空 content_type 提前返回、显式 octet-stream 前置拒绝,两处短路导致字典条目死重),误导维护者 | 🟡 | 清理条目 + 重写注释说明真实兜底机制 + 更新结构测试 |
| F4 | 去重检测 `hashlib.sha256(f.read())` 整文件读入内存(上限 50MB),并发上传内存峰值翻倍 | 🟡 | 抽出 `sha256_of_file()` 分块流式哈希(8MB 块),配 4 个单元测试 |
| F5 | 未使用的 `import sys` + 函数内冗余重复 `import time` | 🟢 | 清理 |

### 3.2 遗留问题(跨模块/环境类,本轮不改动,建议后续处理)

| # | 问题 | 等级 | 建议 |
|---|---|---|---|
| F6 | 三处扩展名白名单不一致:上传 {pdf,md,txt,docx} / indexer 含 `.xlsx` / pipeline 含 `.markdown`+`.xlsx`。上传缺 `.markdown` 而解析器支持;`.xlsx` 解析器未实现(indexer 声明了但 ExcelParser 抛 NotImplementedError) | 🟡 | 统一为单一来源常量;`.xlsx` 在 ExcelParser 落地前不建议开放上传 |
| F7 | 路由模块导入链重:`EMBEDDING_MODEL_PATH` 默认 HF 模型名,冷启动测试首跑 50s(疑似 HF 缓存/网络探测),热跑 9s | 🟡 | 路由层改惰性导入;CI 设 `HF_HUB_OFFLINE=1` |
| F8 | **环境依赖未安装**:`openpyxl`/`pymupdf` 声明在 `requirements-dev.txt` 但当前环境未装 → 9 个测试模块收集失败 + 15 个索引链路测试失败;`excel_parser.py` 顶层 `import openpyxl` 会拖垮整个 parser 包导入 | 🔴 环境 | 安装依赖;建议 excel_parser 改惰性导入并加 pytest.importorskip |
| F9 | `was_overwrite` 检测(L247)与 `os.replace`(L321)之间存在 TOCTOU 窗口,并发同名上传可能基于陈旧状态决策清理策略 | 🟢 | 低风险(有索引锁兜底);如需彻底解决,把覆盖检测移到 replace 前原子操作 |
| F10 | 端点 Content-Length 预检含 multipart overhead(~几百字节),恰好贴上限的文件可能被误拒 | 🟢 | 可容忍;如需精确可放宽预检一个 overhead 余量 |
| F11 | `_progress_queues` 的过期清理仅在新上传时触发,无上传流量时过期队列滞留;跨线程共享依赖"变更只发生在事件循环线程"的隐式约定 | 🟢 | 已在代码注释中确认机制;可考虑后台定时清理 |

### 3.3 存量测试失败(与上传模块无关,改造前即存在)— 已在后续工作清零

全量套件(排除 F8 收集失败模块):**801 通过 / 36 失败 / 3 跳过**。
经 `git stash` 对照验证:**36 个失败在原始代码上完全一致地失败**,与本次改动零关联:

- `test_indexer_trace`(15)、`test_progress_e2e`(7)、`test_loader*`(4)、`test_indexer_chunking_empty`(1)、`test_docx 相关`:根因为 openpyxl 缺失(F8)→ **P0 安装依赖后全部转绿**
- `test_snippet_match`(4)、`test_evidence_gate`(2)、`test_retrievers`(2)、`test_adr0001`(1):其他模块自身的存量缺陷 → **P1 逐个根因定位并修复,详见第 7 章**

---

## 4. 第三步:改造清单

改动的文件:

| 文件 | 改动 |
|---|---|
| `backend/app/api/routes/rag_upload.py` | F1 超限清理 / F2 删死代码并规范缩进 / F3 白名单清理+注释 / F4 `sha256_of_file` 流式哈希 / F5 清理 import |
| `backend/tests/test_rag_upload_mime.py` | F3 配套:结构测试改为断言"表内无永不可达条目" |
| `backend/tests/test_rag_upload_sync_impl.py` | 新建,34 个用例(特征化测试 + 修复验证) |

每项改动均有对应测试:F1→`test_oversize_cleans_tmp_file`(红→绿);F4→`TestSha256OfFile` 4 例;F3→`test_octet_stream_not_in_whitelist_dicts`;其余路径由既有行为测试锁定。

---

## 5. 验证记录

```text
# 上传模块全量(改造后)
68 passed in 8.96s

# 全量套件(排除 F8 收集失败模块)
801 passed, 36 failed, 3 skipped — 36 个失败经 stash 对照确认为存量问题,零回归

# 语法校验
python -m py_compile backend/app/api/routes/rag_upload.py → OK
```

## 6. 后续建议(按优先级)

1. ~~**P0**:安装 `openpyxl`/`pymupdf`(F8)~~ ✅ 已完成(见第 7.1 节)
2. ~~**P1**:排查存量失败中的非环境类~~ ✅ 已完成(见第 7.2 节)
3. **P2**:统一三处扩展名白名单为单一常量(F6)
4. **P2**:路由层惰性导入重依赖,设置 CI 离线模式(F7)
5. **P3**:按同样"测试→审查→改造"流程推进下一模块(建议:文档删除/重索引链路,与上传共享 registry 与操作日志)

---

## 7. 后续工作:P0/P1 存量问题清零(2026-08-20)

### 7.1 P0:补齐环境依赖

安装 `openpyxl`/`pymupdf`/`python-docx` 后,9 个被遮蔽的测试模块恢复收集,
套件规模 801 → **884**;原 36 个失败中 21 个环境类直接转绿,剩 15 个真实失败进入 P1。

### 7.2 P1:15 个非环境类失败的根因与修复

全部根因都是**多 agent 协作的典型症状**(签名漂移、契约不同步、全局状态污染),逐个定位并修复:

| # | 失败模块(数) | 根因 | 修复 |
|---|---|---|---|
| G1 | `test_snippet_match`(4) | 实现 V1.1 改用 `page_content` 匹配,测试只传 `snippet` — 两个 agent 未同步 | 实现改为 `page_content` 优先、回退 `snippet`(兼容两种调用方) |
| G2 | `test_pdf_parser`/`test_pipeline_ext`(2) | `caplog` 抓不到日志:`setup_logger` 直接实例化 `ObservableLogger`,未登记到 `logging.Logger.manager` 且 `parent=None` 传播链断裂 | `shared/logger.py` 补登记 manager + 挂 root 父链 — 这是**生产可观测性缺口**(全局 handler/采集器同样抓不到),不只测试问题 |
| G3 | `test_retrievers`(2+3 处隐患) | `hybrid_retrieve` 新增 `expanded_queries` 透传,测试 mock 签名未同步;另有 3 处旧签名 mock 靠降级路径"侥幸通过" | 测试 mock 全部补齐参数 |
| G4 | `test_adr0001`(1) | 硬编码能力数 8,另一 agent 新增了 `business.analyze` | 改为断言已知能力集合完整包含,不硬编码总数 |
| G5 | `test_evidence_gate`(2) | 测试断言"代码默认值",但运行时被 `.env` 覆盖(`RERANK_MIN_TOP1=0.25` 等) | 新增 `_code_default()`:临时移除环境变量 + patch `load_dotenv` + reload 读真实默认值,自动恢复 |
| G6 | `test_circuit_breaker`(2)+ `test_llm_span_fields`(2),仅全量运行时失败 | `llm_circuit_breaker` 进程级单例,前序测试累计 5 次 mock 失败触发熔断(OPEN,30s),污染后续测试 | 熔断器补行业标准 `reset()` API(对标 pybreaker/resilience4j);conftest 加 autouse fixture 每测试前后复位 |

改动文件:`evaluation/runners/builtin.py`、`shared/logger.py`、`infra/circuit_breaker.py`、
`tests/rag/test_retrievers.py`、`tests/test_adr0001_dual_registry_merge.py`、
`tests/test_evidence_gate.py`、`tests/conftest.py`。

### 7.3 最终验证

```text
# 全量套件(连续两次运行确认 flaky 稳定消除)
881 passed, 3 skipped in 52.34s
881 passed, 3 skipped in 53.24s
```

全库测试从"801 通过 / 36 失败 + 9 模块收集失败"到 **881 通过 / 0 失败**,零回归。

---

## 8. 上传→入库全链路端到端测试(2026-08-20)

### 8.1 覆盖范围

此前测试只盖住两段:`sync_upload_impl`(上传落盘)和 `parse_and_chunk`(文件→chunks),
中间的入库链路无端到端验证。新增 [test_rag_upload_to_index_e2e.py](../backend/tests/test_rag_upload_to_index_e2e.py)(6 个用例):

```text
上传落盘 → 后台索引任务 → 文件锁 → duplicate 检测 → reindex_file
  → 解析/清洗/分块 → metadata → embed → 向量库/doc_db/BM25/registry → SSE 终态
```

设计:**真实** registry/chunk_store(tmp SQLite)+ 真实解析流水线 + 真实索引编排;
**fake** embedding/vectordb/doc_db/bm25(内存记录型,签名与真实接口严格对齐,如 `delete(where=...)`)。

| 用例 | 验证点 |
|---|---|
| md 文件入库 | registry active + hash 一致、向量库 chunks 带 doc_id/kb_id、doc_db 全文、BM25 同步 |
| 重复 reindex 同内容 | doc_id 稳定、registry 不重复、旧向量/旧全文按 doc_id 删除 |
| 空文件 | ChunkingEmptyError 拒绝入库,registry 无残留行 |
| 路由层全链路 | SSE 事件序 uploading→done、done 携带 doc 信息、操作日志 success、队列 finalize |
| 同内容二次上传 | duplicate 短路,各 store 零新增写入 |
| 内容变化重传 | 重新索引、registry hash 更新、旧向量删除 |

### 8.2 发现并修复的 bug(G7)

**`reindex_file` 首次索引返回 `doc_id=''`**:旧实现返回 `old_doc_id`(新文件无旧记录 → 空串),
而 `_index_file` 内部明明派生了真实 doc_id。导致返回值/日志丢失文档身份。
修复:`_index_file` 透传派生的 doc_id,`reindex_file` 优先消费。测试断言"首次索引 doc_id 必非空"。

### 8.3 观察到的生产行为(记录,不改)

- 入库过程会真实调用 LLM 关键词提取;本环境 API 返回 402(余额不足)时
  **优雅降级到规则提取**,索引不阻断 — 降级链路经实测有效。测试未屏蔽该调用,
  因此 e2e 用例含外部服务延迟(~25s/6 例);如需提速可后续加配置开关。
- 重索引删除接口为 Chroma 风格 `delete(where={"doc_id": ...})`,fake 已按此对齐。

### 8.4 验证

```text
# 上传→入库 e2e
6 passed in 25.29s

# 全量套件(含新增 e2e)
887 passed, 3 skipped in 60.07s
```
