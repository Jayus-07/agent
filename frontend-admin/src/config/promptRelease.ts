/** Prompt 发布默认使用的快速门禁评测集。 */
export const PROMPT_RELEASE_SUITE = 'pr_smoke'

/**
 * 正式管理端发布默认走 GitHub Actions；本地开发可通过 Next 环境变量显式切回 local。
 */
export const PROMPT_RELEASE_EXECUTOR =
  process.env.NEXT_PUBLIC_PROMPT_RELEASE_EXECUTOR === 'local' ? 'local' : 'github'
