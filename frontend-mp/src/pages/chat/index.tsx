import { useMemo, useRef, useState } from 'react'
import { View, Text, ScrollView, Input, Button } from '@tarojs/components'
import { request } from '@/lib/request'
import { streamChat, type ChatStreamHandle } from '@/lib/sse'
import type { Message, SSEStreamEvent } from '@/lib/types'
import './index.scss'

/** 生成 id：无 nanoid 依赖，时间戳 + 随机段够用（本地会话/请求标识） */
function genId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

/**
 * 对话页 — agent 流式问答（小程序首期唯一功能页）
 *
 * 流协议与 Web 端一致（SSE v2）：delta 增量正文 / done 终态 / error 报错。
 * 会话历史恢复、thinking/todo 展示等留待后续（见 docs/2026-09-16-前端拆分计划.md Phase 5）。
 */
export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [statusText, setStatusText] = useState('')

  const sessionId = useMemo(() => genId('sess'), [])
  const streamRef = useRef<ChatStreamHandle | null>(null)
  const requestIdRef = useRef<string>('')
  const scrollRef = useRef<string>('')

  const scrollToBottom = () => {
    // ScrollView 的 scrollIntoView 按 id 跳到页面底部锚点
    scrollRef.current = 'bottom-anchor'
  }

  const handleEvent = (evt: SSEStreamEvent, assistantId: string) => {
    if (evt.event === 'delta') {
      const chunk = evt.data.content
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk } : m)),
      )
      scrollToBottom()
    } else if (evt.event === 'status') {
      setStatusText(`正在处理：${evt.data.node}`)
    } else if (evt.event === 'done') {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, done: true, elapsed: evt.data.elapsed } : m,
        ),
      )
      setStatusText('')
    } else if (evt.event === 'error') {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: m.content || `出错了：${evt.data.message}`, done: true }
            : m,
        ),
      )
      setStatusText('')
    }
    // meta/thinking 等事件首期不消费
  }

  const handleSend = () => {
    const question = input.trim()
    if (!question || streaming) return

    const requestId = genId('req')
    requestIdRef.current = requestId
    const assistantId = genId('msg')
    setInput('')
    setStatusText('思考中…')
    setMessages((prev) => [
      ...prev,
      { id: genId('msg'), role: 'user', content: question, timestamp: Date.now() },
      { id: assistantId, role: 'assistant', content: '', timestamp: Date.now() },
    ])
    setStreaming(true)
    scrollToBottom()

    streamRef.current = streamChat(
      { question, session_id: sessionId, request_id: requestId },
      {
        onEvent: (evt) => handleEvent(evt, assistantId),
        onError: (msg) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: m.content || `请求失败：${msg}`, done: true } : m,
            ),
          )
          setStatusText('')
          setStreaming(false)
          streamRef.current = null
        },
      },
    )

    // done/error 事件后收尾（轮询流句柄状态不必要，用微任务标记即可：
    // handleEvent 里 done/error 已把消息置 done，这里监听不到事件流结束，
    // 所以在 onEvent 之外用兜底定时收尾——正常 done 会先到。）
    const finishTimer = setInterval(() => {
      setMessages((prev) => {
        const last = prev[prev.length - 1]
        if (last && last.role === 'assistant' && last.done) {
          clearInterval(finishTimer)
          setStreaming(false)
          streamRef.current = null
        }
        return prev
      })
    }, 500)
  }

  const handleAbort = () => {
    streamRef.current?.abort()
    streamRef.current = null
    setStreaming(false)
    setStatusText('')
    const last = messages[messages.length - 1]
    // 通知后端中止（幂等，失败不影响前端状态）
    if (last?.role === 'assistant' && !last.done) {
      request('/api/chat/abort', {
        method: 'POST',
        data: { session_id: sessionId, request_id: requestIdRef.current },
      }).catch(() => undefined)
    }
    setMessages((prev) =>
      prev.map((m) => (m.role === 'assistant' && !m.done ? { ...m, done: true } : m)),
    )
  }

  return (
    <View className='chat-page'>
      <ScrollView
        className='chat-list'
        scrollY
        scrollIntoView={scrollRef.current}
        scrollWithAnimation
      >
        {messages.map((m) => (
          <View key={m.id} className={`chat-msg chat-msg-${m.role}`}>
            <View className='bubble'>
              <Text selectable>{m.content || (streaming && !m.done ? '…' : '')}</Text>
              {m.elapsed != null && m.role === 'assistant' ? (
                <Text className='elapsed'>耗时 {m.elapsed}s</Text>
              ) : null}
            </View>
          </View>
        ))}
        {statusText ? (
          <View className='chat-status'>
            <Text>{statusText}</Text>
          </View>
        ) : null}
        <View id='bottom-anchor' />
      </ScrollView>
      <View className='chat-input-bar'>
        <Input
          className='chat-input'
          placeholder='输入你的问题…'
          value={input}
          disabled={streaming}
          confirmType='send'
          onInput={(e) => setInput(e.detail.value)}
          onConfirm={handleSend}
        />
        {streaming ? (
          <Button className='chat-btn abort' size='mini' onClick={handleAbort}>
            停止
          </Button>
        ) : (
          <Button className='chat-btn send' size='mini' onClick={handleSend}>
            发送
          </Button>
        )}
      </View>
    </View>
  )
}
