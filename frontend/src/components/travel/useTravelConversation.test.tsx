import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { useTravelConversation } from './useTravelConversation'

const { getMessages, saveMessages } = vi.hoisted(() => ({
  getMessages: vi.fn(async () => ({ messages: [] })),
  saveMessages: vi.fn(async () => undefined),
}))

vi.mock('@/api/travel', () => ({
  getTravelConversationMessages: getMessages,
  saveTravelConversationMessages: saveMessages,
}))

function Harness(props: { enabled: boolean }) {
  const conversation = useTravelConversation('travel-unverified-url-candidate', {
    enabled: props.enabled,
  })
  return <button type="button" onClick={() => conversation.setMessages([{
    id: 'local-1', conversationId: 'travel-unverified-url-candidate',
    turnId: 'turn-1', role: 'user', text: '测试消息',
  }])}>{conversation.messages.length}</button>
}

let root: Root | undefined
let container: HTMLDivElement
let originalSessionStorage: PropertyDescriptor | undefined
let sessionGetItem: ReturnType<typeof vi.fn>
let sessionSetItem: ReturnType<typeof vi.fn>

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  root = undefined
  if (originalSessionStorage) {
    Object.defineProperty(window, 'sessionStorage', originalSessionStorage)
    originalSessionStorage = undefined
  }
  container?.remove()
  getMessages.mockClear()
  saveMessages.mockClear()
  window.sessionStorage.clear()
})

it('服务端会话关联验证前不读取或写入会话存储，也不请求对话历史', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  originalSessionStorage = Object.getOwnPropertyDescriptor(window, 'sessionStorage')
  sessionGetItem = vi.fn(() => null)
  sessionSetItem = vi.fn()
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: sessionGetItem, setItem: sessionSetItem,
      removeItem: vi.fn(), clear: vi.fn(), key: vi.fn(), length: 0,
    },
  })

  await act(async () => root?.render(<Harness enabled={false} />))
  await act(async () => container.querySelector('button')?.click())
  await act(async () => new Promise((resolve) => setTimeout(resolve, 180)))

  expect(container.textContent).toBe('0')
  expect(getMessages).not.toHaveBeenCalled()
  expect(saveMessages).not.toHaveBeenCalled()
  expect(sessionGetItem.mock.calls.some(([key]) =>
    String(key).startsWith('travel:conversation:v1'))).toBe(false)
  expect(sessionSetItem.mock.calls.some(([key]) =>
    String(key).startsWith('travel:conversation:v1'))).toBe(false)

  await act(async () => root?.render(<Harness enabled />))
  await act(async () => Promise.resolve())
  expect(getMessages).toHaveBeenCalledWith('travel-unverified-url-candidate')

})
