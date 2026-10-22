# 进度

## 当前状态

待审阅；工作树 `codex/rag-enterprise-controls`，基线 HEAD `561c5ff`。代码未提交。

## 已完成

- 识别现状：OCR 已按页统计，但无图片块的矢量/内容页未被 OCR；抽样覆盖率已有显式标记；QA 节点导致结构分数直接为 1.0；Layout 插件未接线。
- 新增页面空白/未解析区分、无图片块页面整页 OCR、双栏 bbox 几何排序和 PDF 原生文字层→AST 审计字段。
- 新增混合根级章节的局部处理器分派，并拆开结构置信度、内容覆盖率、语义质量字段。
- 合成 PDF 运行检查通过：矢量页触发 OCR，空白页不计缺页，双栏页按左列后右列排序；OCR 不可用时可见内容页被质量门禁拦截。
- 员工制度 PDF 只读解析：20 页均映射到内容节点，OCR 第 1 页成功，原生文字层对账 555/555 片段；由于 OCR 页未被原文审计验证，`content_coverage` 保持 null、观察值为 1.0 且状态为 partial。
- 混合 FAQ/条款及 FAQ/SOP AST 运行检查通过：输出 Leaf 覆盖无缺口；FAQ 节点不再将整体结构置信度直接抬为 1.0。
- PDF source-location 补强：策略输出优先继承 AST 位置，财务长正文硬切用原文跨度映射。对 `report.pdf` 的 143 页真实 OCR 回放产出 4,207 个 kept chunks、3,366 个 Leaf，0 个 Leaf 缺页码；覆盖仍为抽样，原生文本审计 99.29% 且 14 个 OCR 页未验证，不作为全量内容通过。
- 本地 RapidOCR 仅对空结果/异常页提高 DPI 重试一次，云 OCR 不重试；ProGit 第 13 页在两次尝试后仍为 0 字符，Poppler 的 XRef 渲染错误和 PyMuPDF XObject 错误表明源页损坏，质量门禁继续拦截。
- 修改涉及 Python 文件 `py_compile` 与 `git diff --check` 通过；针对 PDF page mapping/OCR 的 focused pytest 55 项通过。代码变更后 worktree 后端已重建，`/health` 返回 200。

## 未完成与限制

- 本地只实现常见双栏的 bbox 几何回退，没有接入语义版面模型；跨页复杂表格、图表语义与 OCR 准确率仍需专用 Layout/OCR 评估。
- 未接入语义 Layout 模型；跨页复杂表格、图表语义和 OCR 准确率仍需专用评估。
- ProGit 英文 PDF 第 13 页损坏导致本地 OCR 无法恢复；中文 ProGit 超出默认 100 页 OCR 预算 54 页。建议后续提供单文档显式 OCR 预算重处理（估算页数/资源、可续跑、候选索引、通过后原子发布）；不扩大默认全局上限。
- 后端已在 worktree 重建且健康；未提交、未上传文档、未写入知识库或索引。等待用户用管理端实际测试。
