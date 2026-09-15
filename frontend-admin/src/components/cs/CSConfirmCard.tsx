'use client'

import { CheckCircle, XCircle } from 'lucide-react'

interface Props {
  content: string
  onConfirm: () => void
  onCancel: () => void
}

export default function CSConfirmCard({ content, onConfirm, onCancel }: Props) {
  return (
    <div className="flex gap-3 mb-4">
      <div className="flex-1 max-w-[80%] ml-11">
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <p className="text-xs font-medium text-amber-700 mb-2">请确认以下操作</p>
          <p className="text-sm text-amber-900 mb-3">{content}</p>
          <div className="flex gap-2">
            <button
              onClick={onConfirm}
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg
                bg-green-600 text-white hover:bg-green-700 transition-colors"
            >
              <CheckCircle size={12} />
              确认
            </button>
            <button
              onClick={onCancel}
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg
                bg-white text-text-secondary border border-border-subtle
                hover:bg-gray-50 transition-colors"
            >
              <XCircle size={12} />
              取消
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
