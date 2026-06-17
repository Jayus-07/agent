'use client'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

interface Props {
  content: string
}

export default function MarkdownContent({ content }: Props) {
  if (!content) {
    return <span className="text-[#8e8e8e] italic">(无内容)</span>
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // 图片使用 base64 内嵌，无需额外处理
          img: ({ src, alt }) => (
            <img
              src={src}
              alt={alt ?? ''}
              className="max-w-full rounded-lg my-3"
              loading="lazy"
            />
          ),
          // 链接在新窗口打开
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:text-blue-300 underline underline-offset-2"
            >
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
