# Q3 决策前证据（历史快照）

> 用户随后已批准本期完整实施；当前PAUSED。下面“未拍板”仅为历史记录，当前范围与下一步见 [HANDOFF.md](HANDOFF.md)。

2026-11-02，工作树agent-cs-prod，分支feat/cs-prod-q3，HEAD 144aea7729b4e573b87e5ba34421c42e329c98d2。

## 只读复核

读取agent-gov/data/eval_runs/cs-live-1102-b1～b4/report.json，共304条，与锁版category按case_id关联。action共62：24 pass、38 fail。失败样本实际expert分布：32 action、4 query、1 knowledge、1 complaint+knowledge；其中最终答案含“系统繁忙”2条。抽查C3-021/030/040/055：target与visited都为action，但must_contain未命中订单号/确认词。因此“38.7%缺口全部由低置信度知识兜底导致”不是足够准确的实施依据。

Q2的8条action选样通过不代表其余54条已修复；不得用60条冒烟替代全量action验收。Q2整体质量门禁仍FAIL。

## 待决定后的执行分支

- action：先以原报告38个失败ID逐例复现/分类；路由错才改词表；进入正确expert的用例检查提案、可信身份、必填槽和应答。只有证实错标时走锁版修订；不为提高通过率改标注。
- full_path：若本期补齐，沿用_run_cs(mode='full_path')与主图入口，补CLI/配置传递、跨run身份隔离、域进入/禁词/澄清断言及真实链路验证；不能只加未实际调用的开关。
- multi_turn：若本期补齐，统一会话逐轮重放turns，跨case/run隔离；末轮断言消费完整上下文，补能识别串话与丢上下文的验证。
- 预算：先定位CS图/远端RAG跨线程上下文缺口；复用现有统一预算与计费，不加调用点独立账本；真实验收需要按run归因与超限阻断证据。未拍板前不安排常规付费批次或自动排程。

三组问题已通过异步输入提出，尚未收到用户选择。此文件只记录实施准备，所有Q3功能项均未开工/未验收。
