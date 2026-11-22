/**
 * Prompt 发布默认使用的快速门禁评测集。
 *
 * 2026-11-22 修复：原值 'pr_smoke' 在 datasets/rag/suites/ 中从未创建，
 * RAG Prompt 发起发布评测（无论 local/github）都会 FileNotFoundError——
 * RAG 的发布评测链路此前实际不可跑。改用真实存在的 8 条 smoke 集
 * （desktop_upload_smoke，manifest 治理齐备，tier=smoke @0.95）。
 */
export const PROMPT_RELEASE_SUITE = 'desktop_upload_smoke'

/**
 * 正式管理端发布默认走 GitHub Actions；本地开发可通过 Next 环境变量显式切回 local。
 */
export const PROMPT_RELEASE_EXECUTOR =
  process.env.NEXT_PUBLIC_PROMPT_RELEASE_EXECUTOR === 'local' ? 'local' : 'github'
