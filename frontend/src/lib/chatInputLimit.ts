/**
 * 聊天输入限制 — 须与后端 CHAT_INPUT_MAX_CHARS（backend/config/chat_input.py）保持一致。
 *
 * 定位：Chat 承载较长问题/配置/代码片段/结构化需求；整份知识文档请走知识库上传。
 * 后端在超限时返回业务错误（details.reason=CHAT_INPUT_TOO_LARGE），
 * 前端计数器只是引导层，后端校验仍是权威。
 */
export const CHAT_INPUT_MAX_CHARS = 20000

/** 达到该比例才显示字符计数（普通短消息不展示噪声计数器） */
export const CHAT_INPUT_COUNTER_THRESHOLD = 0.8

export function chatInputOverLimit(text: string): boolean {
  return text.length > CHAT_INPUT_MAX_CHARS
}

export function chatInputShowCounter(text: string): boolean {
  return text.length >= Math.floor(CHAT_INPUT_MAX_CHARS * CHAT_INPUT_COUNTER_THRESHOLD)
}
