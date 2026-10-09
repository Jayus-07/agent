'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { getCachedUser } from '@/lib/auth'
import {
  getTravelConversationMessages,
  saveTravelConversationMessages,
  type TravelStoredConversationMessage,
} from '@/api/travel'
import type { RationaleData } from './RationaleCard'

export interface TravelConversationMessage {
  id: string
  conversationId: string
  turnId: string
  role: 'user' | 'assistant'
  text: string
  tag?: string
  tone?: 'ok' | 'warn'
  rationale?: RationaleData
}

interface ConversationSnapshot {
  storageKey: string
  messages: TravelConversationMessage[]
}

const STORAGE_PREFIX = 'travel:conversation:v1'
const MAX_MESSAGES = 100
const MAX_CONVERSATIONS = 30

function identityScope(): { tenantId: string; userId: string } {
  const user = getCachedUser() as Record<string, unknown> | null
  return {
    tenantId: String(user?.tenantId || user?.tenant_id || 'default'),
    userId: String(user?.userId || user?.user_id || user?.username || 'anonymous'),
  }
}

function storageKeys(conversationId: string): {
  conversationKey: string
  indexKey: string
  conversationPrefix: string
} {
  const { tenantId, userId } = identityScope()
  const scope = `${encodeURIComponent(tenantId)}:${encodeURIComponent(userId)}`
  const conversationPrefix = `${STORAGE_PREFIX}:${scope}:`
  return {
    conversationKey: `${conversationPrefix}${encodeURIComponent(conversationId)}`,
    indexKey: `${STORAGE_PREFIX}:index:${scope}`,
    conversationPrefix,
  }
}

function validMessage(value: unknown, conversationId: string): value is TravelConversationMessage {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<TravelConversationMessage>
  return Boolean(
    item.conversationId === conversationId
    && typeof item.id === 'string'
    && typeof item.turnId === 'string'
    && (item.role === 'user' || item.role === 'assistant')
    && typeof item.text === 'string',
  )
}

function readMessages(storageKey: string, conversationId: string): TravelConversationMessage[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.sessionStorage.getItem(storageKey)
    const values: unknown = raw ? JSON.parse(raw) : []
    if (!Array.isArray(values)) return []
    return values
      .filter((value): value is TravelConversationMessage =>
        validMessage(value, conversationId))
      .slice(-MAX_MESSAGES)
  } catch {
    return []
  }
}

function fromServerMessages(
  values: TravelStoredConversationMessage[], conversationId: string,
): TravelConversationMessage[] {
  let currentTurn = ''
  return values
    .filter((item) => (item.role === 'user' || item.role === 'assistant')
      && typeof item.content === 'string' && item.content.trim())
    .slice(-MAX_MESSAGES)
    .map((item) => {
      const id = `server:${item.id}`
      if (item.role === 'user') currentTurn = `server-turn:${item.id}`
      const turnId = currentTurn || `server-turn:${item.id}`
      return {
        id,
        conversationId,
        turnId,
        role: item.role,
        text: item.content,
        tag: undefined,
        tone: undefined,
        rationale: undefined,
      }
    })
}

function mergeMessages(
  remote: TravelConversationMessage[], local: TravelConversationMessage[],
): TravelConversationMessage[] {
  if (remote.length === 0) return local.slice(-MAX_MESSAGES)
  if (local.length === 0) return remote.slice(-MAX_MESSAGES)
  const sameMessage = (a: TravelConversationMessage, b: TravelConversationMessage) =>
    a.role === b.role && a.text === b.text
  let sharedPrefix = 0
  while (sharedPrefix < remote.length && sharedPrefix < local.length
    && sameMessage(remote[sharedPrefix], local[sharedPrefix])) {
    sharedPrefix += 1
  }
  if (sharedPrefix < Math.min(remote.length, local.length)) {
    return remote.slice(-MAX_MESSAGES)
  }
  const longer = remote.length >= local.length ? remote : local
  const merged = longer.map((message, index) =>
    index < sharedPrefix ? local[index] : message)
  return merged.slice(-MAX_MESSAGES)
}

function writeMessages(
  conversationKey: string,
  indexKey: string,
  conversationPrefix: string,
  messages: TravelConversationMessage[],
): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(
      conversationKey,
      JSON.stringify(messages.slice(-MAX_MESSAGES)),
    )
    const raw = window.sessionStorage.getItem(indexKey)
    const index: unknown = raw ? JSON.parse(raw) : []
    const keys = Array.isArray(index)
      ? index.filter((key): key is string =>
        typeof key === 'string' && key.startsWith(conversationPrefix))
      : []
    const next = [...keys.filter((key) => key !== conversationKey), conversationKey]
    while (next.length > MAX_CONVERSATIONS) {
      const expiredKey = next.shift()
      if (expiredKey) window.sessionStorage.removeItem(expiredKey)
    }
    window.sessionStorage.setItem(indexKey, JSON.stringify(next))
  } catch {
    // sessionStorage 配额或隐私限制只影响刷新恢复，不影响当前对话。
  }
}

/** 按租户、用户和会话隔离消息；桌面/抽屉重挂时从同一快照恢复。 */
export function useTravelConversation(conversationId: string): {
  messages: TravelConversationMessage[]
  setMessages: Dispatch<SetStateAction<TravelConversationMessage[]>>
  syncError: string
} {
  const { conversationKey, indexKey, conversationPrefix } = storageKeys(conversationId)
  const [snapshot, setSnapshot] = useState<ConversationSnapshot>(() => ({
    // 等 hydration 完成后再读取 sessionStorage，避免服务端与客户端首屏不一致。
    storageKey: '',
    messages: [],
  }))
  const [hydratedKey, setHydratedKey] = useState('')
  const [syncError, setSyncError] = useState('')
  const lastSavedRef = useRef('')
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve())

  const messages = snapshot.storageKey === conversationKey
    ? snapshot.messages
    : readMessages(conversationKey, conversationId)

  const setMessages = useCallback<Dispatch<SetStateAction<TravelConversationMessage[]>>>(
    (update) => {
      setSnapshot((current) => {
        const previous = current.storageKey === conversationKey
          ? current.messages
          : readMessages(conversationKey, conversationId)
        const next = typeof update === 'function' ? update(previous) : update
        return { storageKey: conversationKey, messages: next.slice(-MAX_MESSAGES) }
      })
    },
    [conversationId, conversationKey],
  )

  useEffect(() => {
    if (snapshot.storageKey === conversationKey) return
    setSnapshot({
      storageKey: conversationKey,
      messages: readMessages(conversationKey, conversationId),
    })
  }, [conversationId, conversationKey, snapshot.storageKey])

  useEffect(() => {
    let cancelled = false
    const local = readMessages(conversationKey, conversationId)
    setHydratedKey('')
    setSyncError('')
    lastSavedRef.current = ''
    setSnapshot({ storageKey: conversationKey, messages: local })

    void getTravelConversationMessages(conversationId).then(({ messages: stored }) => {
      if (cancelled) return
      const remote = fromServerMessages(stored || [], conversationId)
      const remoteSnapshot = remote.map(({ role, text }) => ({ role, content: text }))
      const localExtendsRemote = remote.length > 0 && local.length > remote.length
        && remote.every((message, index) =>
          message.role === local[index].role && message.text === local[index].text)
      if (remote.length > 0 && !localExtendsRemote) {
        // 服务端是跨设备历史权威源，已恢复的同一份快照不应再删写一次。
        lastSavedRef.current = `${conversationKey}:${JSON.stringify(remoteSnapshot)}`
      }
      setSnapshot((current) => {
        const latestLocal = current.storageKey === conversationKey
          ? current.messages : local
        const localIds = new Set(local.map((message) => message.id))
        const pendingLocal = latestLocal.filter((message) => !localIds.has(message.id))
        const merged = mergeMessages(remote, latestLocal)
        const mergedIds = new Set(merged.map((message) => message.id))
        const pendingTail = pendingLocal.filter((message) => !mergedIds.has(message.id))
        return {
          storageKey: conversationKey,
          messages: [...merged, ...pendingTail].slice(-MAX_MESSAGES),
        }
      })
      setHydratedKey(conversationKey)
    }).catch(() => {
      if (cancelled) return
      // 读取失败时不允许用本地旧快照覆盖服务器历史；下次进入会话时重试。
      setSyncError('旅游对话历史暂未同步，当前设备上的消息仍可查看。')
    })

    return () => { cancelled = true }
  }, [conversationId, conversationKey])

  useEffect(() => {
    if (snapshot.storageKey !== conversationKey) return
    writeMessages(conversationKey, indexKey, conversationPrefix, snapshot.messages)
  }, [conversationKey, conversationPrefix, indexKey, snapshot])

  useEffect(() => {
    if (snapshot.storageKey !== conversationKey || hydratedKey !== conversationKey
      || snapshot.messages.length === 0) return
    const persisted = snapshot.messages.map(({ role, text }) => ({ role, content: text }))
    const signature = `${conversationKey}:${JSON.stringify(persisted)}`
    if (lastSavedRef.current === signature) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      const save = saveQueueRef.current.catch(() => undefined).then(async () => {
        await saveTravelConversationMessages(conversationId, persisted)
      })
      saveQueueRef.current = save.then(() => undefined, () => undefined)
      void save.then(() => {
        if (cancelled) return
        lastSavedRef.current = signature
        setSyncError('')
      }).catch(() => {
        if (!cancelled) {
          setSyncError('旅游对话历史同步失败，消息仍保存在当前设备。')
        }
      })
    }, 150)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [conversationId, conversationKey, hydratedKey, snapshot])

  return { messages, setMessages, syncError }
}
