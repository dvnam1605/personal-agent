import React, { useState, useEffect, useMemo } from 'react'
import {
  Mail,
  RefreshCw,
  ExternalLink,
  Search,
  PenSquare,
  Sparkles,
  AlertCircle,
  Clock,
  User,
  X,
  Reply,
  Eye,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { GmailMessage } from '../../types/api'
import './Workspaces.css'

interface EmailWorkspaceProps {
  onSelectEmail: (msg: GmailMessage) => void
  onDraftEmail: (prompt: string) => void
}

type OmnibarMode = 'search' | 'compose' | 'summarize'

export const EmailWorkspace: React.FC<EmailWorkspaceProps> = ({
  onSelectEmail,
  onDraftEmail,
}) => {
  const [messages, setMessages] = useState<GmailMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeMode, setActiveMode] = useState<OmnibarMode>('search')
  const [inputValue, setInputValue] = useState('')

  const fetchEmails = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const res = await apiClient.submitQuery('Đọc các email mới nhất trong hộp thư đến')
      if (res.data?.messages && Array.isArray(res.data.messages)) {
        setMessages(res.data.messages as GmailMessage[])
      } else {
        setMessages([])
      }
    } catch (err: any) {
      setError(err?.message || 'Không thể đồng bộ hộp thư Gmail.')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchEmails()
  }, [])

  // Filter messages locally in real-time when in search mode
  const filteredMessages = useMemo(() => {
    if (!inputValue.trim() || activeMode !== 'search') return messages
    const q = inputValue.toLowerCase()
    return messages.filter(
      (msg) =>
        (msg.subject && msg.subject.toLowerCase().includes(q)) ||
        (msg.from && msg.from.toLowerCase().includes(q)) ||
        (msg.snippet && msg.snippet.toLowerCase().includes(q))
    )
  }, [messages, inputValue, activeMode])

  const handleOmnibarSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputValue.trim()) return

    if (activeMode === 'search') {
      onDraftEmail(`Tìm kiếm email trong Gmail: ${inputValue}`)
    } else if (activeMode === 'compose') {
      onDraftEmail(`Soạn email: ${inputValue}`)
      setInputValue('')
    } else if (activeMode === 'summarize') {
      onDraftEmail(`Đọc và tóm tắt email: ${inputValue}`)
      setInputValue('')
    }
  }

  const handleQuickPrompt = (prompt: string) => {
    onDraftEmail(prompt)
  }

  const quickChips = [
    { label: '⚡ Tóm tắt hộp thư hôm nay', prompt: 'Đọc và tóm tắt các email quan trọng nhận được hôm nay' },
    { label: '🔍 Tìm email cảnh báo bảo mật', prompt: 'Tìm các email liên quan đến cảnh báo bảo mật tài khoản' },
    { label: '🔍 Tìm thư từ LinkedIn & Tuyển dụng', prompt: 'Tìm các email từ LinkedIn và cơ hội việc làm' },
    { label: '✍ Soạn thư xin nghỉ phép', prompt: 'Soạn email gửi quản lý xin nghỉ phép một ngày vì việc gia đình' },
    { label: '✍ Soạn thư xác nhận cuộc họp', prompt: 'Soạn email xác nhận tham dự cuộc họp dự án' },
  ]

  return (
    <div className="workspace-container email-workspace">
      {/* Workspace Toolbar */}
      <div className="workspace-toolbar">
        <div className="toolbar-left">
          <div className="email-count-chip">
            <Mail size={14} className="text-primary" />
            <span>Hộp thư đến ({messages.length} thư)</span>
          </div>
        </div>

        <div className="toolbar-right">
          <button
            className="toolbar-btn"
            onClick={fetchEmails}
            disabled={isLoading}
            title="Làm mới hộp thư"
          >
            <RefreshCw size={14} className={isLoading ? 'spin-anim' : ''} />
            <span>Làm mới</span>
          </button>
          <a
            href="https://mail.google.com"
            target="_blank"
            rel="noreferrer"
            className="toolbar-btn toolbar-btn--link"
          >
            <span>Mở Gmail</span>
            <ExternalLink size={13} />
          </a>
        </div>
      </div>

      {/* Intelligent Unified Omnibar (Search, Compose, Summarize, Actions) */}
      <div className="email-omnibar-card">
        {/* Mode Selector Tabs */}
        <div className="omnibar-mode-selector">
          <button
            className={`omnibar-mode-tab ${activeMode === 'search' ? 'omnibar-mode-tab--active' : ''}`}
            onClick={() => setActiveMode('search')}
          >
            <Search size={13} />
            <span>Tìm kiếm & Lọc thư</span>
          </button>
          <button
            className={`omnibar-mode-tab ${activeMode === 'compose' ? 'omnibar-mode-tab--active' : ''}`}
            onClick={() => setActiveMode('compose')}
          >
            <PenSquare size={13} />
            <span>Soạn thư mới</span>
          </button>
          <button
            className={`omnibar-mode-tab ${activeMode === 'summarize' ? 'omnibar-mode-tab--active' : ''}`}
            onClick={() => setActiveMode('summarize')}
          >
            <Sparkles size={13} />
            <span>Tóm tắt hộp thư</span>
          </button>
        </div>

        {/* Omnibar Input Form */}
        <form onSubmit={handleOmnibarSubmit} className="omnibar-form">
          <div className="omnibar-input-container">
            {activeMode === 'search' && <Search size={16} className="omnibar-input-icon text-muted" />}
            {activeMode === 'compose' && <PenSquare size={16} className="omnibar-input-icon text-primary" />}
            {activeMode === 'summarize' && <Sparkles size={16} className="omnibar-input-icon text-accent" />}

            <input
              type="text"
              className="omnibar-input"
              placeholder={
                activeMode === 'search'
                  ? 'Tìm kiếm thư theo người gửi, chủ đề hoặc nội dung (lọc trực tiếp)...'
                  : activeMode === 'compose'
                  ? 'Nhập yêu cầu soạn thư (ví dụ: Soạn email gửi anh Nam xác nhận lịch họp 14:00)...'
                  : 'Yêu cầu tóm tắt (ví dụ: Tóm tắt 5 email quan trọng nhất trong ngày hôm nay)...'
              }
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
            />

            {inputValue.trim() && (
              <button
                type="button"
                className="omnibar-clear-btn"
                onClick={() => setInputValue('')}
                title="Xóa nội dung"
              >
                <X size={14} />
              </button>
            )}
          </div>

          <button
            type="submit"
            className="omnibar-submit-btn"
            disabled={!inputValue.trim() && activeMode !== 'summarize'}
          >
            {activeMode === 'search' && (
              <>
                <Search size={13} />
                <span>Tìm với AI</span>
              </>
            )}
            {activeMode === 'compose' && (
              <>
                <PenSquare size={13} />
                <span>Tạo bản nháp</span>
              </>
            )}
            {activeMode === 'summarize' && (
              <>
                <Sparkles size={13} />
                <span>Tóm tắt ngay</span>
              </>
            )}
          </button>
        </form>

        {/* Quick Action Suggestion Chips */}
        <div className="omnibar-chips-row">
          <span className="omnibar-chips-label">Gợi ý tác vụ:</span>
          {quickChips.map((chip, idx) => (
            <button
              key={idx}
              type="button"
              className="omnibar-suggestion-chip"
              onClick={() => handleQuickPrompt(chip.prompt)}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content Area */}
      <div className="workspace-content-scroll">
        {isLoading ? (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim text-primary" />
            <span>Đang đồng bộ các email mới nhất từ Gmail...</span>
          </div>
        ) : error ? (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Lỗi đồng bộ hộp thư</h4>
              <p>{error}</p>
            </div>
            <button className="btn-retry" onClick={fetchEmails}>
              Thử lại
            </button>
          </div>
        ) : filteredMessages.length === 0 ? (
          <div className="workspace-empty-state">
            <div className="empty-icon-wrap">
              <Mail size={32} />
            </div>
            <h3>Không tìm thấy email phù hợp</h3>
            <p>
              {inputValue
                ? `Không có email nào khớp với từ khóa "${inputValue}". Bạn có thể nhờ Trợ lý AI tìm kiếm sâu trên toàn bộ Gmail.`
                : 'Hộp thư hiện đang trống hoặc chưa có thư mới.'}
            </p>
            {inputValue && (
              <button
                className="btn-ask-ai-search"
                onClick={() => onDraftEmail(`Tìm kiếm email trong toàn bộ Gmail: ${inputValue}`)}
              >
                <Sparkles size={14} />
                <span>Nhờ Trợ lý tìm kiếm: "{inputValue}"</span>
              </button>
            )}
          </div>
        ) : (
          <div className="email-feed-list">
            {filteredMessages.map((msg, idx) => (
              <div
                key={msg.id || idx}
                className="email-row-item"
                onClick={() => onSelectEmail(msg)}
              >
                <div className="email-row-avatar">
                  <User size={14} />
                </div>

                <div className="email-row-content">
                  <div className="email-row-header">
                    <span className="email-row-sender">{msg.from || 'Không rõ người gửi'}</span>
                    {msg.when && (
                      <span className="email-row-time">
                        <Clock size={11} />
                        {new Date(msg.when).toLocaleDateString('vi-VN', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    )}
                  </div>

                  <div className="email-row-subject">{msg.subject || '(Không có tiêu đề)'}</div>

                  <p className="email-row-snippet">{msg.snippet}</p>

                  {/* Multi-action Bar on each Email Card */}
                  <div className="email-card-actions" onClick={(e) => e.stopPropagation()}>
                    <button
                      className="email-card-btn email-card-btn--summarize"
                      onClick={() =>
                        handleQuickPrompt(
                          `Tóm tắt nội dung email từ ${msg.from} với tiêu đề "${msg.subject}": ${msg.snippet}`
                        )
                      }
                      title="Tóm tắt email này với Trợ lý AI"
                    >
                      <Sparkles size={12} />
                      <span>Tóm tắt</span>
                    </button>

                    <button
                      className="email-card-btn email-card-btn--reply"
                      onClick={() =>
                        handleQuickPrompt(
                          `Soạn email trả lời thư của ${msg.from} về chủ đề "${msg.subject}"`
                        )
                      }
                      title="Soạn thư trả lời nhanh với AI"
                    >
                      <Reply size={12} />
                      <span>Trả lời với AI</span>
                    </button>

                    <button
                      className="email-card-btn email-card-btn--view"
                      onClick={() => onSelectEmail(msg)}
                      title="Mở ngăn kéo xem toàn bộ nội dung"
                    >
                      <Eye size={12} />
                      <span>Xem chi tiết</span>
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
