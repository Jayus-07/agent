# Progress

## Current state

In progress; branch `codex/rag-enterprise-controls`, baseline HEAD `561c5ff`.
The active worktree already contains uncommitted RAG parser/quality changes from
the preceding task; preserve them and review them together without resetting.

## Baseline simulation: complete

- Simulated all 30 filesystem entries: 28 real PDF, DOCX, Markdown, TXT, CSV and
  XLSX documents, plus two Office `~$` lock files. The parser loop completed
  without runtime errors; output is in `D:\tmp\rag_eval_kb_fixture_simulation.json`.
- FAQ DOCX/MD, mixed FAQ Markdown, preamble DOCX/MD, long SOP DOCX, interleaved
  DOCX, contract table DOCX, three CSV encodings, XLSX and ordinary TXT produced
  chunks with complete coverage and valid Parent links. CSV kept SKU identifiers
  such as `00532931` as strings. `no_title.txt` produced 69 chunks; three filter
  exclusions were explicitly recorded and did not create a coverage gap.
- `report.pdf` (143 pages) produced 622 duplicate chunk IDs, 390 invalid Parent
  references and 232 Leaf chunks without source locations. Its repeated PDF text
  also caused some Leaf page mappings to span pages 1–143.
- `doc_cn.pdf` (102 pages) produced 1,107 duplicate IDs, 324 invalid Parent
  references, 475 Leaf chunks without source locations, and a `value_loss`
  warning. Coverage was sampled, so the observed 0 gaps is not a full-pass claim.
- `progit_en_501p.pdf` found 366 blank pages, an OCR failure on page 13, and
  content pages without parse coverage. The quality gate blocked indexing; it
  also had 98 duplicate IDs before the fix.
- `progit_zh.pdf` hit the configured OCR page cap (54 remaining required pages),
  had one coverage gap and eight duplicate IDs. The quality gate correctly
  blocked partial indexing. `contract_gf2017_0201_cn.pdf` had one uncovered table
  leaf and 11 Leaf chunks without source locations. `scanned_partial_fail.pdf`
  mapped 9 content pages plus one blank page; OCR reported no failed pages, with
  one visual-content warning.
- The two `~$` Word lock files emitted zero chunks and hard quality anomalies;
  they are Office temporary artifacts, not valid documents. They should be
  excluded from upload testing.

## Changes and validation

- Fixed local content-unit identifier collisions by namespacing chunk, parent,
  table, and reference IDs. Ambiguous repeated PDF text no longer contributes
  page mappings. Deletion API now reports HTTP and operation-state failures;
  frontend single/batch deletion distinguishes success, degraded cleanup, and
  unknown remote outcomes.
- PDF table summaries/rows and strategy-generated leaves now inherit
  parser-confirmed page and bbox metadata before text is rewritten. Financial
  hard-split chunks map their exact source-text spans back to AST locations.
  `report.pdf` now has 0/3,366 kept Leaf chunks without a page location in the
  final real-RapidOCR run; its maximum Leaf page span remains 4 rather than 143.
  The corresponding structural diagnostic, with OCR mocked and production
  filtered-text exclusions supplied, also reports 0 coverage gaps. That run is
  explicitly not an OCR-accuracy result; large-document coverage remains
  sampled, and the native-text audit is partial (99.29%, 92 units missing on 39
  pages, plus 14 OCR pages not independently verified).
- One real-OCR manual quality script that omitted the production
  `filtered_details.content` exclusions reported 45 coverage gaps. The
  production preflight includes those explicit filter exclusions; its exact
  structural check with OCR mocked reports 0 gaps. The final real-OCR
  production-preflight result was not rerun, so this distinction remains an
  explicit verification limitation.
- Local RapidOCR now retries a page once at higher DPI after an exception or
  empty result; cloud OCR is not automatically retried to avoid duplicate
  charges. The English ProGit page 13 still fails both attempts. Visual
  inspection and renderer logs show a nearly blank/corrupt page with invalid
  XRef/XObject data, so the document remains blocked pending source-PDF repair
  or a clean re-export. Chinese ProGit still exceeds the 100-page OCR cap by 54
  pages and remains blocked; the cap was not raised globally.
- Fixed a real long-contract loss: a symbol-heavy final piece of a split clause
  was rejected by the `all_symbols` filter. Split-group source pieces now remain
  intact. `contract_gf2017_0201_cn.pdf` recheck is 261/261 AST Leaves covered,
  45/45 table rows and 90/90 cells covered, no hard anomalies; 9 OCR pages
  succeeded. OCR pages still remain unverified by the independent native-text
  source audit, which is reported as partial rather than full-source proof.
- Targeted backend checks previously passed: 59 tests across content integrity,
  table location, chunk identity, PDF page mapping, and RAG route regression.
  The latest focused parser/location set passes 55 tests, including local OCR
  retry, cloud no-retry, and hard-split source-location regressions.
  `py_compile` and `git diff --check` pass. Frontend Vitest passes (13 tests)
  and `npx tsc --noEmit` passes using the identical dependency cache from the
  primary checkout; no dependency files changed.
- The original all-fixture pass remains the baseline for other formats. Current
  unresolved PDF cases are explicit: `progit_en_501p.pdf` has OCR/source audit
  failures and is blocked; `progit_zh.pdf` exceeds the configured OCR page cap
  by 54 pages and has a coverage gap, so it is blocked. `doc_cn.pdf` coverage
  was sampled, so it must not be labeled 100% complete. Two `~$` Word lock files
  are invalid upload inputs.
- The user authorized rebuilding the local services from this worktree. The
  backend rebuild completed and reports healthy with health HTTP 200; the
  remote RAG service readiness returns HTTP 200. The pre-existing admin on
  port 3200 belongs to another worktree and was left untouched. No
  upload/index/database mutation has been executed. All fixture simulations
  and PDF checks remain offline parser/quality-path runs.

## Follow-up: per-upload OCR budget

- Added an optional administrator-only PDF upload field `ocr_page_budget`.
  The admin dialog offers 200/300/500 pages; omission keeps the configured
  global default (100 pages). The request and Celery retries carry the same
  explicit budget, while parser-side validation enforces a 500-page hard cap.
- The effective budget, its source, required page count, and pages left over the
  budget are recorded in the AST quality record and OCR lineage Trace. The
  existing quality gate still blocks any remaining OCR or content-coverage
  gap; a higher budget does not mark partial parsing as complete.
- The local fixture has 154 OCR-required pages in `progit_zh.pdf`. An offline
  parse with budget 200 recognized 154/154 pages, left 0 pages over budget,
  and produced 5,662 chunks. Typed quality validation found no hard anomalies;
  24/24 table rows and 66/66 cells were covered. However, the Leaf audit sampled
  1,653/4,959 leaves (0 sampled gaps) and the native-text audit is `partial`
  despite 100% of checked windows matching. Do not claim full content
  completeness; warnings include sampled coverage, OCR degradation, and visual
  content that text extraction cannot verify. No upload, embedding, index
  publication, or database write was run.
- Focused checks pass: 103 backend tests across OCR, upload validation, and
  Celery behavior; 5 frontend upload API tests; `npx tsc --noEmit`; and
  `git diff --check`. The worktree rebuild completed successfully: backend,
  RAG service, index workers and dependent workers are healthy; backend `/health`
  and RAG `/readyz` return HTTP 200. Started the frontend from this worktree on
  port 3201 because port 3200 belongs to another worktree. The worktree admin
  page returns HTTP 200 and its upload dialog was opened; no file was selected
  and no upload was submitted. The OCR selector appears after a PDF is selected,
  so its in-dialog state was not exercised in this verification.

## Follow-up: knowledge-base management and department selector

- Added a PostgreSQL-backed, versioned KB catalog with create, edit, deactivate,
  and re-activate operations. Every catalog write records before/after state in
  `auth.knowledge_base_audits` in the same transaction. Deactivation preserves
  indexed documents; read-only compatibility KBs cannot be changed. Only
  `super_admin` can create or change `customer`/`test` audiences.
- Existing `owner_depts` values are migrated as the one-time baseline and now
  drive upload authorization, employee visibility, customer audience filtering,
  and cross-KB test exclusion from the same live catalog. A KB can have multiple
  departments; a document upload still records one department. Upload choices
  use active departments from the caller's verified tenant, and the backend
  checks both KB association and active department again before accepting upload.
- The workbench management panel now supports multi-select, `all` as an exclusive
  option, editing, soft deactivation and restore. Switching KBs updates the
  upload department selector and clears a selection that is no longer allowed.
  Inactive department associations remain visible in management so admins can
  remove them, but they are not offered for upload.
- The isolated PostgreSQL validation originally applied the draft migration as `104_auth_knowledge_bases.sql`; before integration with `main`, the source migration and registry were renumbered to `107_auth_knowledge_bases.sql` to follow the existing RBAC and tenant-isolation migrations. This renumbering does not change the migration body.
  successfully to an isolated PostgreSQL 16 container (18 initial KB rows).
  No shared database was touched. Backend focused checks pass: 59 tests across
  KB authorization, upload-selection filtering, route contracts, lifecycle,
  preview, active uploads and pending review. Frontend `UploadDialog` behavior
  tests pass (2); `npx tsc --noEmit`, Python compilation and `git diff --check`
  pass. The migration test container was stopped and removed.
- The current local backend has not been rebuilt with migration 104. Project
  instructions require the app rebuild path for backend code, while also
  prohibiting shared-database operations; applying the new schema to the shared
  local database therefore remains a deployment gate. Until that step, the
  current 3201 UI cannot exercise the new management API against the backend.
- New KBs are available through explicit upload/search selection. Automatic
  question routing remains keyword-rule based and needs a corresponding rule for
  newly created KBs.

## Next steps

1. 迁移已顺延为 107；仍需在隔离数据库和服务栈中重建，才能运行时验证知识库管理界面。
2. 上传、删除的端到端 UI/API 体验仍待本地服务按隔离环境启动后验证。

## Final authorization review

- The catalog is platform-global while department master data is tenant-scoped.
  To prevent tenant admins from changing a shared catalog, create/edit/deactivate/
  reactivate routes now require a trusted `super_admin`; tenant admins can view
  the directory. UI controls follow that role boundary and denied writes are
  recorded as an authorization event.
- After this boundary change, the focused KB route/authz tests pass (3 passed),
  the upload department-selector behavior tests pass (2 passed), TypeScript and
  Python compilation pass, and `git diff --check` is clean. The 104 migration
  has only been applied to the disposable isolated PostgreSQL test container.
- The worktree backend was not rebuilt against the shared database because the
  project instructions prohibit shared database operations. End-to-end UI/API
  validation therefore remains `UNVERIFIED` until an isolated runtime/database
  is available or the repository rule changes.

## 合并结果（2026-10-22）

- 任务提交 `36213ea` 已合并到本地 `main`，合并提交为 `10426e7`；未推送远端。
- 合并时保留了 `main` 的租户隔离改动和同期客服业务提交，并将知识库目录迁移登记为 `107_auth_knowledge_bases.sql`。
- 合并后定向后端用例 193 项通过；知识库 API/删除/上传前端 20 项通过；TypeScript 检查通过。pytest 有两条环境告警：当前 Python 未识别 `timeout` 与 `timeout_method` 配置。
- 共享数据库未迁移，未按 `devctl.bat rebuild` 重建本机服务；部署和浏览器端到端仍为 `UNVERIFIED`。用户可在隔离服务部署后继续验证上传及删除体验。
