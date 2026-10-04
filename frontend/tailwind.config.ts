import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: '#4D6BFE',
          hover: '#3b54d4',
          soft: 'rgba(77, 107, 254, 0.08)',
        },
        surface: {
          root: '#fafbfc',
          base: '#ffffff',
          elevated: '#f8f9fb',
          hover: '#f3f4f6',
        },
        sidebar: {
          /** 裸 bg-sidebar：侧栏容器底色（改动前是幽灵类，三处侧栏实际没背景） */
          DEFAULT: 'rgba(255, 255, 255, 0.72)',
          glass: 'rgba(255, 255, 255, 0.72)',
          /** DeepSeek 风格聊天页侧栏实底浅灰 */
          solid: '#f9f9f9',
        },
        /**
         * 语义 token —— 与 globals.css 的 :root CSS 变量**一一对应**。
         *
         * 为什么写死色值而不是 var(--x)：Tailwind 3 无法对 `var()` 颜色
         * 计算透明度，写成 var() 会让 `text-text-muted/80` 这类带透明度的
         * 候选**整条不生成**（实测编译产物为空）。写死色值则两者都能用。
         *
         * 两处必须同值 —— 由 src/lib/design-tokens.test.ts 机械保证漂移即失败。
         *
         * 缺失史：这三组曾整体漏配，导致全仓 text-text-primary /
         * text-text-secondary / text-text-muted / border-border-subtle /
         * bg-bg-root 共 250+ 处「写了等于没写」（文字靠继承、边框靠
         * currentColor）。
         */
        text: {
          primary: '#1a1a2e',
          secondary: '#6b7280',
          muted: '#9ca3af',
        },
        border: {
          subtle: '#e5e7eb',
          default: '#d1d5db',
        },
        bg: {
          root: '#fafbfc',
          surface: '#ffffff',
          elevated: '#f8f9fb',
          hover: '#f3f4f6',
        },
      },
      boxShadow: {
        'input': '0 2px 8px rgba(77, 107, 254, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04)',
        'card': '0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02)',
      },
      backdropBlur: {
        glass: '20px',
      },
    },
  },
  plugins: [],
}
export default config
