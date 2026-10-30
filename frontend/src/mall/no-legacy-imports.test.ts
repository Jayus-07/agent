import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// cs-workstation-v2 铁律：商城命名空间（src/mall/**、app/mall、app/my-orders）
// 禁止 import 旧 CS 业务组件/hooks。此守卫从 P0 起持续生效，Phase 8 收编为
// PR 检查。
const FORBIDDEN_PATTERNS: Array<[RegExp, string]> = [
  [/@\/components\/cs[/'"]/, '旧 CS 业务组件 components/cs'],
  [/useCSChat|useCSHandoffSync/, '旧 CS hooks'],
  [/@\/store\/csChat/, '旧 CS 会话 store'],
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

const roots = ['src/mall', 'src/app/mall', 'src/app/my-orders']
const files = roots.flatMap((root) => collectFiles(root))

describe('mall 命名空间零旧组件引用', () => {
  it(`扫描 ${files.length} 个 mall 源文件，禁止 import 旧 CS 业务代码`, () => {
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
})
