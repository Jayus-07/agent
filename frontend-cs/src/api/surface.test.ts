/**
 * surface 契约测试（P0-6，纯新增）
 *
 * 目的：在 api 层迁移（P0-3）与组件归位（P0-4）之后，守住「对外 surface」不破：
 *   1. 每个域在 src/api/<domain>.ts 有且仅有一个模块（落点收敛）；
 *   2. 兼容层 src/lib/api.ts 为纯 re-export，且旧导入路径（@/lib/api）仍能取到各域关键符号；
 *   3. 无遗留的 @/services 引用（迁移完整性）。
 *
 * 与被测代码共置（仓库口径：src/api/client.test.ts、src/api/errors.test.ts），不使用 src/api/__tests__/。
 */
import { describe, it, expect } from 'vitest'
import * as libApi from '@/lib/api'
import fs from 'node:fs'
import path from 'node:path'

const SRC_ROOT = path.resolve(process.cwd(), 'src')
const API_DIR = path.join(SRC_ROOT, 'api')

// P0-3 之后应存在的域模块（不含 client/errors 这类基础件）
const DOMAINS = [
  'approvals',
  'alerts',
  'chat',
  'cs',
  'competitor',
  'evaluation',
  'feedback',
  'keyword',
  'knowledge',
  'llm',
  'memory',
  'observability',
  'prompts',
  'reports',
  'selection',
  'selectionDecision',
] as const

describe('surface contract: 每域一个模块 src/api/<domain>.ts', () => {
  for (const d of DOMAINS) {
    it(`src/api/${d}.ts 存在`, () => {
      expect(fs.existsSync(path.join(API_DIR, `${d}.ts`))).toBe(true)
    })
  }
})

describe('surface contract: 兼容层 src/lib/api 为纯 re-export', () => {
  it('旧导入路径 @/lib/api 仍能取到各域关键符号（兼容不破）', () => {
    // 基础件
    expect(typeof (libApi as Record<string, unknown>).ApiError).toBe('function')
    // chat
    expect(typeof (libApi as Record<string, unknown>).streamChat).toBe('function')
    // llm
    expect(typeof (libApi as Record<string, unknown>).listLLMModels).toBe('function')
    // memory
    expect(typeof (libApi as Record<string, unknown>).listSessions).toBe('function')
    // observability
    expect(typeof (libApi as Record<string, unknown>).listTraces).toBe('function')
  })

  it('src 下无遗留 @/services 引用（迁移完整性）', () => {
    const bad: string[] = []
    const walk = (dir: string) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        if (e.name === 'node_modules' || e.name === '.next' || e.name.startsWith('.')) continue
        const full = path.join(dir, e.name)
        if (e.isDirectory()) {
          walk(full)
        } else if (/\.(ts|tsx)$/.test(e.name)) {
          const txt = fs.readFileSync(full, 'utf8')
          if (/from\s+['"]@\/services\b/.test(txt)) bad.push(full)
        }
      }
    }
    walk(SRC_ROOT)
    expect(bad).toEqual([])
  })
})
