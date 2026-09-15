/**
 * SSE v2 流式事件公共字段归约 — chat / csChat 两个 store 共享。
 *
 * 此前两套 store 各自实现 delta 累积 / status 切换 / 终态清理，已经漂移过
 * （CS 页缺流式渲染、死订阅）。公共字段统一走这里，私有字段
 * （CS 的 intent/csTimeline、chat 的 streamEvents 环形队列）由各 store
 * 的 addStreamEvent 在调用本函数前后自行处理。
 */
import type { SSEStreamEvent } from '@/lib/types'

export interface StreamCoreFields {
  /** 流式 delta 累积文本（StreamingContent 消费） */
  deltaText: string
  /** 当前宏观状态节点名（StatusBar 消费） */
  currentStatus: string
  /** node → emoji 映射表（meta 事件下发） */
  nodeLabels: Record<string, string>
}

/** 终态判定：done/error 之后流式阶段结束 */
export function isTerminalEvent(evt: SSEStreamEvent): boolean {
  return evt.event === 'done' || evt.event === 'error'
}

/** 归约公共字段，返回需要合并的增量更新（无变化的字段不出现在结果里） */
export function reduceStreamCore(
  state: StreamCoreFields,
  evt: SSEStreamEvent,
): Partial<StreamCoreFields> {
  switch (evt.event) {
    case 'meta':
      return { nodeLabels: evt.data.node_labels }
    case 'status':
      return { currentStatus: evt.data.node }
    case 'delta':
      return { deltaText: state.deltaText + evt.data.content }
    case 'done':
    case 'error':
      // 终态清空状态节点：否则 error/中断路径（无 done 事件）下
      // StatusBar 会残留最后一个阶段标签（如"✍️ 生成回复"）
      return { currentStatus: '' }
    default:
      return {}
  }
}
