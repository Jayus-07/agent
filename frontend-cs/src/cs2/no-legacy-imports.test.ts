import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// cs-workstation-v2 铁律：工作台命名空间（src/cs2/**、src/app/cs/**）禁止 import
// 旧 CS 业务组件/hook。P8 切换后旧资产应已物理删除（下方 not-exists 断言）。
const FORBIDDEN_PATTERNS: Array<[RegExp, string]> = [
  [/@\/app\/cs2[/'"]/, '过渡期路由 app/cs2（P8 已切换为 app/cs）'],
  [/@\/components\/agent/, '旧坐席业务组件 components/agent'],
  [/@\/components\/chat/, '旧聊天组件 components/chat'],
  [/csAgentWs/, '旧 CS WebSocket 封装 lib/csAgentWs'],
  [/useCSChat|useCSHandoffSync/, '旧 CS hooks'],
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

const roots = ['src/cs2', 'src/app/cs']
const files = roots.flatMap((root) => collectFiles(root))

describe('工作台命名空间零旧组件引用（P8 切换守卫）', () => {
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
      ['src/app/cs/handoff', '旧人工接入页 app/cs/handoff'],
      ['src/app/cs/stats', '旧满意度统计页 app/cs/stats'],
      ['src/app/cs2', '过渡期路由目录 app/cs2'],
      ['src/api/cs.ts', '旧坐席 API 封装 api/cs.ts'],
      ['src/lib/csAgentWs.ts', '旧 CS WebSocket 封装 lib/csAgentWs'],
      ['src/components/agent/AgentAssistPanel.tsx', '旧坐席助手面板 AgentAssistPanel'],
    ]
    const leftover = removed
      .filter(([rel]) => existsSync(join(process.cwd(), rel)))
      .map(([, reason]) => reason)
    expect(leftover).toEqual([])
  })
})
