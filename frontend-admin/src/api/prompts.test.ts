import { beforeEach, describe, expect, it, vi } from 'vitest'

const requestMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({ request: requestMock }))

import { promptsService } from './prompts'

describe('promptsService authenticated API client', () => {
  beforeEach(() => {
    requestMock.mockReset()
  })

  it('loads prompt list through the shared authenticated request client', async () => {
    const items = [{ key: 'assistant.main', name: '主助手' }]
    requestMock.mockResolvedValue({ items })

    await expect(promptsService.list({ keys: ['assistant.main'] })).resolves.toEqual(items)

    expect(requestMock).toHaveBeenCalledWith('/api/prompts?keys=assistant.main', undefined)
  })
})
