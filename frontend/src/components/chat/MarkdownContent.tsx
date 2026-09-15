'use client'

import { useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { Copy, Check } from 'lucide-react'

interface Props { content: string }

function CodeBlock({ children, ...props }: any) {
  const [copied, setCopied] = useState(false)

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
    setTimeout(() => setCopied(false), 2000)
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
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          pre: ({ children, ...props }) => (
            <CodeBlock {...props}>{children}</CodeBlock>
          ),
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
