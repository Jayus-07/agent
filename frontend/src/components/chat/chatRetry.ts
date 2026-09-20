import type { Message } from '@/lib/types'

export function lastUserQuestion(messages: Pick<Message, 'role' | 'content'>[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role === 'user' && message.content.trim()) return message.content.trim()
  }
  return null
}
