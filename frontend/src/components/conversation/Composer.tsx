import React, { useState, useRef, useEffect } from 'react'
import { Send, Sparkles, Command } from 'lucide-react'
import { Button } from '../ui/Button'
import './Composer.css'

interface ComposerProps {
  onSendMessage: (text: string) => void
  isLoading: boolean
  placeholder?: string
}

export const Composer: React.FC<ComposerProps> = ({
  onSendMessage,
  isLoading,
  placeholder = 'Nhập câu hỏi hoặc yêu cầu (ví dụ: Lịch ngày mai, tóm tắt email...)...',
}) => {
  const [text, setText] = useState('')
  const [showSlashMenu, setShowSlashMenu] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const slashCommands = [
    { label: '/lich', desc: 'Xem lịch hôm nay & ngày mai', prompt: 'Lịch của tôi hôm nay và ngày mai?' },
    { label: '/mail', desc: 'Đọc email chưa đọc mới nhất', prompt: 'Đọc các email mới nhất trong hộp thư đến' },
    { label: '/hop', desc: 'Chuẩn bị hồ sơ cho cuộc họp kế tiếp', prompt: 'Chuẩn bị cuộc họp tiếp theo' },
    { label: '/tailieu', desc: 'Tra cứu quy chế và văn bản nội bộ', prompt: 'Tài liệu nội bộ nói gì về: ' },
  ]

  // Auto-resize textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 140)}px`
    }
  }, [text])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSend = () => {
    if (!text.trim() || isLoading) return
    onSendMessage(text.trim())
    setText('')
    setShowSlashMenu(false)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setText(val)
    if (val.startsWith('/') && val.length <= 8) {
      setShowSlashMenu(true)
    } else {
      setShowSlashMenu(false)
    }
  }

  const handleSelectSlash = (prompt: string) => {
    setText(prompt)
    setShowSlashMenu(false)
    textareaRef.current?.focus()
  }

  return (
    <div className="composer-wrapper">
      {/* Slash command popup */}
      {showSlashMenu && (
        <div className="slash-menu animate-slide-in">
          <div className="slash-menu__header">
            <Command size={13} />
            <span>Lệnh thao tác nhanh</span>
          </div>
          {slashCommands.map((cmd) => (
            <button
              key={cmd.label}
              className="slash-menu-item"
              onClick={() => handleSelectSlash(cmd.prompt)}
            >
              <span className="slash-menu-item__label">{cmd.label}</span>
              <span className="slash-menu-item__desc">{cmd.desc}</span>
            </button>
          ))}
        </div>
      )}

      <div className={`composer-box ${text.trim() ? 'composer-box--active' : ''}`}>
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          placeholder={placeholder}
          value={text}
          onChange={handleTextChange}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={1}
        />

        <div className="composer-controls">
          <div className="composer-hints">
            <span className="hint-pill">
              <Sparkles size={11} /> Gõ <strong>/</strong> để chọn lệnh nhanh
            </span>
          </div>

          <Button
            size="sm"
            variant="primary"
            onClick={handleSend}
            disabled={!text.trim() || isLoading}
            isLoading={isLoading}
            rightIcon={<Send size={13} />}
          >
            Gửi
          </Button>
        </div>
      </div>
    </div>
  )
}
