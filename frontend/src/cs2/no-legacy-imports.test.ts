import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// cs-workstation-v2 铁律：用户端客服命名空间（src/cs2/**、app/customer-service）
// 禁止 import 旧 CS 业务组件/hooks（components/cs/*、useCSChat、
// useCSHandoffSync、store/csChat）。P8 切换后旧资产应已物理删除（下方
// not-exists 断言）。
const FORBIDDEN_PATTERNS: Array<[RegExp, string]> = [
  [/@\/components\/cs[/'"]/, '旧 CS 业务组件 components/cs'],
  [/useCSChat|useCSHandoffSync/, '旧 CS hooks'],
  [/@\/store\/csChat/, '旧 CS 会话 store'],
  [/@\/app\/customer-service-v2[/'"]/, '旧过渡路由 app/customer-service-v2（P8 已删除）'],
]

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    // 排除守卫自身：本文件内的禁用模式字符串是规则定义，不是 import
    else if (/\.(ts|tsx)$/.test(name) && !name.startsWith('no-legacy-imports.')) {
      out.push(full)
    }
  }
  return out
}

const roots = ['src/cs2', 'src/app/customer-service']
const files = roots.flatMap((root) => collectFiles(root))

describe('客服命名空间零旧组件引用（P8 切换守卫）', () => {
  it(`扫描 ${files.length} 个 cs 源文件，禁止 import 旧 CS 业务代码`, () => {
    expect(files.length).toBeGreaterThan(0)
    const violations: string[] = []
    for (const file of files) {
      const source = readFileSync(file, 'utf8')
      for (const [pattern, reason] of FORBIDDEN_PATTERNS) {
        if (pattern.test(source)) violations.push(`${file}: ${reason}`)
      }
    }
    expect(violations).toEqual([])
  })

  it('P8 删除的旧资产已物理清除', () => {
    const removed: Array<[string, string]> = [
      ['src/components/cs', '旧 CS 业务组件目录 components/cs'],
      ['src/app/customer-service-v2', '旧过渡路由 app/customer-service-v2'],
      ['src/hooks/useCSChat.ts', '旧会话 hook useCSChat'],
      ['src/hooks/useCSHandoffSync.ts', '旧转人工轮询 hook useCSHandoffSync'],
      ['src/store/csChat.ts', '旧会话 store csChat'],
      ['src/api/cs.ts', '旧客服 API 封装 api/cs'],
    ]
    const leftover = removed
      .filter(([rel]) => existsSync(join(process.cwd(), rel)))
      .map(([, reason]) => reason)
    expect(leftover).toEqual([])
  })
})
