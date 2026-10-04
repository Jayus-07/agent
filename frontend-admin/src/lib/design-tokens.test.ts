/**
 * 设计 token 守护 —— 防止「类名写了、样式压根没生成」再次静默发生。
 *
 * 事故背景（2026-10-01）：tailwind.config.ts 的 colors 里整体漏配了
 * text / border / bg 三组，导致全仓 text-text-primary / text-text-secondary /
 * text-text-muted / border-border-subtle 共 1000+ 处「写了等于没写」：
 * 文字靠继承父级颜色、边框靠 currentColor。代码读起来完全正常，
 * 只有去翻编译产物才会发现规则为空 —— 属于最难看出来的那一类 bug。
 *
 * 本测试锁五件事：
 *   1) 语义 token 在 config 里确实存在（防误删）
 *   2) config 的色值 === globals.css 的 CSS 变量（防两处漂移）
 *   3) 源码里引用 colors 命名空间的类都能解析到 token（防新增幽灵类）
 *   4) 透明度修饰必须是 5 的倍数或 [任意值]（防 `bg-accent/8` 这类档位外写法）
 *   5) currentColor 不带透明度修饰（Tailwind 对 currentColor 算不出 alpha）
 *
 * 3)~5) 的覆盖面是 2026-10-01 二次补洞扩的：最初只盯 text-text-* / bg-bg-* /
 * border-border-* / bg-sidebar-* 四个族，于是 bg-surface-raised（token 未定义）、
 * bg-accent/8（档位外）、border-current/20（currentColor）三种写法全部漏网。
 *
 * 姊妹文件：frontend/src/lib/design-tokens.test.ts（同一套 token 定义）。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import tailwindConfig from '../../tailwind.config'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC_DIR = path.resolve(HERE, '..')
const GLOBALS_CSS = path.join(SRC_DIR, 'app', 'globals.css')

/** 归一化色值：去空白 + 统一小写，让 `rgba(255, 255, 255, 0.72)` 与 `rgba(255,255,255,0.72)` 可比 */
function norm(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, '')
}

/** 解析 globals.css 里 :root 块的 `--name: value;` */
function readRootVars(): Map<string, string> {
  const css = readFileSync(GLOBALS_CSS, 'utf8')
  const root = css.match(/:root\s*\{([\s\S]*?)\}/)?.[1] ?? ''
  const out = new Map<string, string>()
  for (const m of root.matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    out.set(m[1], norm(m[2]))
  }
  return out
}

type ColorTree = Record<string, unknown>

const colors = (tailwindConfig.theme?.extend?.colors ?? {}) as unknown as ColorTree

/**
 * 扁平化颜色树，供类名反查。
 * text.primary → 'text-primary'；sidebar.DEFAULT → 'sidebar'；accent.hover → 'accent-hover'
 */
function flattenColors(tree: ColorTree, prefix = ''): Set<string> {
  const out = new Set<string>()
  for (const [key, value] of Object.entries(tree)) {
    const flatKey = prefix ? `${prefix}-${key}` : key
    if (value && typeof value === 'object') {
      const nested = value as ColorTree
      if ('DEFAULT' in nested) out.add(flatKey)
      for (const item of flattenColors(nested, flatKey)) out.add(item)
    } else {
      out.add(flatKey)
    }
  }
  return out
}

const FLAT_COLORS = flattenColors(colors)

/** config 语义 token ↔ globals.css 变量名（两处必须同值，否则报错指向这里） */
const TOKEN_TO_VAR: Array<[label: string, value: unknown, cssVar: string]> = [
  ['colors.text.primary', colors.text && (colors.text as ColorTree).primary, 'text-primary'],
  ['colors.text.secondary', colors.text && (colors.text as ColorTree).secondary, 'text-secondary'],
  ['colors.text.muted', colors.text && (colors.text as ColorTree).muted, 'text-muted'],
  ['colors.border.subtle', colors.border && (colors.border as ColorTree).subtle, 'border-subtle'],
  ['colors.border.default', colors.border && (colors.border as ColorTree).default, 'border-default'],
  ['colors.bg.root', colors.bg && (colors.bg as ColorTree).root, 'bg-root'],
  ['colors.bg.surface', colors.bg && (colors.bg as ColorTree).surface, 'bg-surface'],
  ['colors.bg.elevated', colors.bg && (colors.bg as ColorTree).elevated, 'bg-elevated'],
  ['colors.bg.hover', colors.bg && (colors.bg as ColorTree).hover, 'bg-hover'],
  ['colors.sidebar.DEFAULT', colors.sidebar && (colors.sidebar as ColorTree).DEFAULT, 'sidebar-bg'],
]

describe('语义 token 与 globals.css 变量同值', () => {
  const vars = readRootVars()

  it('globals.css 的 :root 能被解析出变量（测试自身的前置条件）', () => {
    expect(vars.size).toBeGreaterThan(10)
    expect(vars.get('text-muted')).toBeTruthy()
  })

  for (const [label, value, cssVar] of TOKEN_TO_VAR) {
    it(`${label} === --${cssVar}`, () => {
      expect(value, `${label} 在 tailwind.config.ts 里缺失`).toBeTypeOf('string')
      expect(
        norm(String(value)),
        `${label} 与 globals.css 的 --${cssVar} 不一致：两处必须同值，否则改了一处另一处不会跟着变`,
      ).toBe(vars.get(cssVar))
    })
  }
})

describe('语义 token 完整性（防误删）', () => {
  it('text / border / bg 三组都在（缺失即全仓对应类变幽灵类）', () => {
    for (const group of ['text', 'border', 'bg'] as const) {
      expect(colors[group], `colors.${group} 整组缺失 —— 该类名下所有类都会「写了等于没写」`).toBeTruthy()
    }
  })

  it('扁平化后能反查到实际在用的类（回归样本）', () => {
    for (const key of ['text-primary', 'text-secondary', 'text-muted', 'border-subtle', 'bg-root', 'sidebar']) {
      expect(FLAT_COLORS.has(key), `colors 里找不到 ${key}`).toBe(true)
    }
  })
})

/** 递归收集源码文件（跳过测试自身，避免自我指涉） */
function collectSources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name === 'node_modules') continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      collectSources(full, out)
      continue
    }
    if (!/\.tsx?$/.test(entry.name)) continue
    if (/\.test\.tsx?$/.test(entry.name)) continue
    out.push(full)
  }
  return out
}

/**
 * 引用 colors 命名空间的类名（允许 `hover:` 这类变体前缀与 `/50` 透明度）。
 * `bg-surface-base/50`、`hover:bg-accent-soft`、`divide-border-subtle` 都要能扫到。
 */
const TOKEN_CLS_RE =
  /\b((?:[a-z][a-z0-9-]*:)*(?:bg|text|border|ring|divide|fill|stroke|outline|placeholder|from|to|via)-(?:text|border|bg|surface|accent|sidebar)(?:-[a-z0-9]+)*)(\/[A-Za-z0-9.[\]]+)?/g

/** 任意「颜色工具类 + 透明度修饰」，用于校验透明度档位。刻意不含 w/h 等分数类（`top-1/2` 合法）。 */
const ANY_COLOR_CLS_RE =
  /\b((?:[a-z][a-z0-9-]*:)*(?:bg|text|border|ring|divide|fill|stroke|outline|placeholder|from|to|via)-[A-Za-z0-9#_.\]\[]+)(\/(?:\d+|\[[^\]\s]+\]))?/g

/** `currentColor` 带透明度：Tailwind 无法对 currentColor 计算 alpha，整条规则不生成 */
const CURRENT_OPACITY_RE =
  /\b((?:[a-z][a-z0-9-]*:)*(?:bg|text|border|ring|divide|fill|stroke|outline|placeholder|from|to|via)-current\/\d[\w.[\]%-]*)/g

/** 把 hover:text-text-muted/80 → 颜色树的扁平 key `text-muted`；bg-sidebar → `sidebar` */
function toFlatColorKey(rawClass: string): string | null {
  // 先剥变体前缀（hover: / focus-visible: / md:），再去透明度修饰
  const noVariant = rawClass.slice(rawClass.lastIndexOf(':') + 1)
  const base = noVariant.split('/')[0]
  const m = /^(?:text|bg|border|ring|divide|fill|stroke|outline|placeholder|from|to|via)-(.+)$/.exec(base)
  return m ? m[1] : null
}

/** 逐行扫描源码，回调拿到 (相对路径, 行号, 匹配)。跳过测试自身，避免自我指涉。 */
function eachMatchInSources(
  re: RegExp,
  onHit: (rel: string, line: number, match: RegExpExecArray) => void,
): number {
  let total = 0
  for (const file of collectSources(SRC_DIR)) {
    const lines = readFileSync(file, 'utf8').split(/\r?\n/)
    lines.forEach((line, idx) => {
      for (const m of line.matchAll(re)) {
        total += 1
        onHit(path.relative(SRC_DIR, file), idx + 1, m)
      }
    })
  }
  return total
}

describe('源码里不存在「配置没定义」的语义类', () => {
  it('扫到的源码文件数 > 0（防止扫描路径写错导致空过）', () => {
    expect(collectSources(SRC_DIR).length).toBeGreaterThan(20)
  })

  it('每个引用 colors 命名空间的类都能解析到 token 定义', () => {
    const offenders: string[] = []
    const checked = eachMatchInSources(TOKEN_CLS_RE, (rel, line, m) => {
      const key = toFlatColorKey(m[1])
      if (!key || !FLAT_COLORS.has(key)) offenders.push(`${rel}:${line} → ${m[0]}`)
    })

    expect(checked, '一个候选类都没扫到，扫描逻辑可能已失效').toBeGreaterThan(0)
    expect(
      offenders,
      '以下类在 tailwind.config.ts 的 colors 里没有定义，编译后是幽灵类（无任何样式）。' +
        '例：bg-surface-raised 曾因 surface.raised 未定义而整条不生成。',
    ).toEqual([])
  })

  it('透明度修饰必须落在 Tailwind 档位上（5 的倍数）或写成任意值 [..]', () => {
    const offenders: string[] = []
    eachMatchInSources(ANY_COLOR_CLS_RE, (rel, line, m) => {
      const mod = m[2]
      if (!mod || mod.startsWith('/[')) return
      const n = Number(mod.slice(1))
      if (Number.isNaN(n) || n % 5 !== 0) {
        offenders.push(`${rel}:${line} → ${m[0]}（/${n} 不在档位上，编译后无样式；请改用 /${Math.round(n / 5) * 5} 或 /[0.0${n}]）`)
      }
    })
    expect(offenders, '分子不是 5 的倍数时 Tailwind 不生成规则，整条样式丢失').toEqual([])
  })

  it('currentColor 不带透明度修饰（Tailwind 算不出 alpha，整条不生成）', () => {
    const offenders: string[] = []
    eachMatchInSources(CURRENT_OPACITY_RE, (rel, line, m) => {
      offenders.push(`${rel}:${line} → ${m[0]}（改用 border-[color:color-mix(in_srgb,currentColor_20%,transparent)]）`)
    })
    expect(offenders, 'currentColor 无法参与透明度计算，`border-current/20` 这类写法编译后是幽灵类').toEqual([])
  })
})
