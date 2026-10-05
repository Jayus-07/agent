'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import rehypeHighlight from 'rehype-highlight'
import { Copy, Check } from 'lucide-react'

interface Props { content: string }

function CodeBlock({ children, ...props }: any) {
  const [copied, setCopied] = useState(false)
  // 复制态定时器：重复点击清旧的，卸载时清理（避免 setState on unmounted）
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
  }, [])

  // 从 children 中提取纯文本
  const extractText = (node: any): string => {
    if (typeof node === 'string') return node
    if (Array.isArray(node)) return node.map(extractText).join('')
    if (node?.props?.children) return extractText(node.props.children)
    return ''
  }

  const codeText = extractText(children)

  const handleCopy = useCallback(async () => {
    if (!codeText) return
    try {
      await navigator.clipboard.writeText(codeText)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = codeText; ta.style.position = 'fixed'; ta.style.opacity = '0'
      document.body.appendChild(ta); ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    setCopied(true)
    if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
    copiedTimerRef.current = setTimeout(() => setCopied(false), 2000)
  }, [codeText])

  return (
    <div className="relative group/code not-prose">
      <button
        onClick={handleCopy}
        className="absolute right-2 top-2 z-10 opacity-0 group-hover/code:opacity-100
          flex items-center gap-1 px-2 py-1 rounded-md
          bg-white/10 hover:bg-white/20 border border-white/10
          text-[11px] text-white/60 hover:text-white/90
          transition-all duration-200"
        aria-label="复制代码">
        {copied ? (
          <><Check size={12} className="text-green-400" /> 已复制</>
        ) : (
          <><Copy size={12} /> 复制</>
        )}
      </button>
      <pre {...props}>
        {children}
      </pre>
    </div>
  )
}

export default function MarkdownContent({ content }: Props) {
  if (!content) {
    return <span className="text-text-muted italic">(无内容)</span>
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          pre: ({ children, ...props }) => (
            <CodeBlock {...props}>{children}</CodeBlock>
          ),
          // 表格排版契约（2026-10-05）：LLM 宽表不得撑破消息气泡——
          // 包横向滚动容器，列多时滚动查看；窄表视觉不变
          table: ({ children, ...props }) => (
            <div className="table-scroll">
              <table {...props}>{children}</table>
            </div>
          ),
          // 标题层级契约：消息流内正文从 H2 起排，LLM 偶发的 `# 大标题`
          // 降级为 H2 渲染，避免破坏消息层级（样式与 h2 一致）
          h1: ({ children, ...props }) => <h2 {...props}>{children}</h2>,
          img: ({ src, alt }) => (
            <img src={src} alt={alt ?? ''} className="max-w-full rounded-lg my-3" loading="lazy" />
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer"
              className="text-accent hover:text-accent-hover underline underline-offset-2">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
