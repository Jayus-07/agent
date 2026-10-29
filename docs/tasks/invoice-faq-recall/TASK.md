# TASK: 发票 FAQ 召回丢失修复（bge-m3 切换后候选池竞争力不足）

> 立项来源：上一专项（enterprise-assistant-roundtable-sse）遗留的发票问拒答。
> 根因来源记录（只读参考）：docs/tasks/enterprise-assistant-roundtable-sse/PROGRESS.md「2026-10-29 续2」。
> 分支：codex/invoice-faq-recall（worktree C:/Users/wh/.codex/worktrees/invoice-faq-recall/agent，base=d9c6100）。

## 目标

修复知识问答拒答：「企业申请发票需要税号和开户银行吗？请给出处。」当前持续返回
「知识库中未找到」。目标块 e07b25dc7b_14（faq_常见问题FAQ.md，「Q：能开发票抬头吗?
A：支持个人/企业抬头，需提供税号及开户银行（企业)」）在库且正确，但从未进入融合
候选池——是召回丢失，不是 rerank 阈值截断。

## 范围

- 改动限定在 RAG 检索链路（backend/rag / backend/config/rag.py）或知识库数据侧；
  复用既有 enhanced_hybrid_retrieval / RRF 融合 / rerank / Evidence Gate，不建平行实现。
- 候选修法按副作用从小到大评估（见 PROGRESS P2 节）：向量修复 > 同文档块挤占治理 >
  融合池加深 > 知识库补口语条目（涉评测数据 DATA-01 三处同步）> 查询侧改写强化。
- 禁止：直接调大 RERANK_SCORE_THRESHOLD 或放宽 Gate；不动 rag.qa v17 prompt；
  不动全局融合权重除非有评测数据支撑。

## 第一优先复核项（可能翻案）

e07b25dc7b_14 块向量新鲜度：向量库（pgvector）中该块向量产出模型/时间是否与同文档
其他块一致。若 _14 向量仍是旧模型（qwen3.7-text-embedding）产出而查询向量是 bge-m3，
dense 分数会系统性偏低，根因即「部分块向量未迁移」而非排序竞争力。

## 验收标准

1. 「企业申请发票需要税号和开户银行吗？请给出处。」返回含「税号」与「开户银行」的
   答案并带 e07b25dc7b（faq_常见问题FAQ.md）出处，留 trace id。
2. 回归不破：发货口语问（「客户下单后多久发货？请给出处。」）与书面问（「请检索企业
   知识库：企业订单的发货时效是怎么规定的？请给出处。」）仍 PASS 带「24 小时」出处；
   支付问 PASS；「用一句话解释幂等」仍走 general_chat 直答。
3. rag 问耗时档不回归（总耗时仍在 5-12s 档，P95 视角）。
4. 证据留痕：全部复测 trace id + D:/tmp 数据文件记入 PROGRESS；修不动的定性如实记录，
   不伪造 PASS。

## 计划

- P0 工作树 + TASK/PROGRESS + 栈健康核验（health + 发票问现状复现一次留 trace）。
- P1 复核：_14 向量新鲜度（第一优先）；融合探针——确认该 query 下 _14 在 dense/sparse
  各自路径的排名与 final_k 截断位置。
- P2 选型与最小实现（带回归测试）。
- P3 验证：rebuild + SSE 复测验收清单；若动检索/数据，跑 rag 评测门禁套件
  （smoke 8 + core 16，24/24 为基线）。
- P4 收尾：PROGRESS 证据 + 展示 Diff 询问确认后提交。

## 纪律

- 马斯克五步、git 提交双重路径限定、不以扩大 mock/删断言/skip 掩盖失败、
  未实际执行的验证不得报告为通过。详见 AGENTS.md 与 .zcode/skills/agent-repo-pitfalls。
