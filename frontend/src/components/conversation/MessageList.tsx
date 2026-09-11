import React, { useRef, useEffect } from 'react'
import {
  Sparkles,
  Calendar,
  Mail,
  Users,
  FileText,
  Loader2,
} from 'lucide-react'
import type { ChatTurn } from './MessageItem'
import { MessageItem } from './MessageItem'
import type { GmailMessage, RAGCitation } from '../../types/api'
import './MessageList.css'

interface MessageListProps {
  turns: ChatTurn[]
  isLoading: boolean
  onQuickPrompt: (prompt: string) => void
  onSelectCitation: (citation: RAGCitation) => void
  onSelectEmailMessage?: (msg: GmailMessage) => void
  onApproveAction: (id: string, execute: boolean) => Promise<void>
  onDenyAction: (id: string, reason?: string) => Promise<void>
  onClarifyAnswer: (answer: string) => void
  onRetryQuery?: (query: string) => void
}

export const MessageList: React.FC<MessageListProps> = ({
  turns,
  isLoading,
  onQuickPrompt,
  onSelectCitation,
  onSelectEmailMessage,
  onApproveAction,
  onDenyAction,
  onClarifyAnswer,
  onRetryQuery,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, isLoading])

  // Get current time greeting
  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Chào buổi sáng'
    if (hour < 18) return 'Chào buổi chiều'
    return 'Chào buổi tối'
  }

  const suggestions = [
    {
      icon: <Calendar size={15} />,
      label: 'Lịch hôm nay',
      prompt: 'Lịch của tôi hôm nay?',
    },
    {
      icon: <Mail size={15} />,
      label: 'Email mới',
      prompt: 'Đọc các email mới nhất trong hộp thư đến',
    },
    {
      icon: <Users size={15} />,
      label: 'Chuẩn bị cuộc họp tiếp theo',
      prompt: 'Chuẩn bị cho cuộc họp tiếp theo',
    },
    {
      icon: <FileText size={15} />,
      label: 'Tìm trong tài liệu nội bộ',
      prompt: 'Tìm trong tài liệu nội bộ về quy chế làm việc',
    },
  ]

  if (turns.length === 0) {
    return (
      <div className="empty-assistant-stage">
        <div className="empty-hero">
          <div className="empty-hero__badge">
            <Sparkles size={13} />
            <span>Trợ lý Điều hành Cá nhân</span>
          </div>
          <h2 className="empty-hero__title">{getGreeting()}, tôi có thể giúp gì cho bạn?</h2>
          <p className="empty-hero__subtitle">
            Hỗ trợ kết nối lịch trình, xử lý email, chuẩn bị cuộc họp và tra cứu tài liệu nội bộ với cơ chế phê duyệt an toàn.
          </p>
        </div>

        <div className="suggestions-grid">
          {suggestions.map((sugg, i) => (
            <button
              key={i}
              className="suggestion-chip-btn"
              onClick={() => onQuickPrompt(sugg.prompt)}
            >
              <span className="suggestion-icon">{sugg.icon}</span>
              <span className="suggestion-label">{sugg.label}</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="message-list-feed">
      {turns.map((turn) => (
        <MessageItem
          key={turn.id}
          turn={turn}
          onSelectCitation={onSelectCitation}
          onSelectEmailMessage={onSelectEmailMessage}
          onApproveAction={onApproveAction}
          onDenyAction={onDenyAction}
          onClarifyAnswer={onClarifyAnswer}
          onRetry={onRetryQuery}
        />
      ))}

      {isLoading && (
        <div className="assistant-loading-indicator animate-fade-in">
          <div className="loading-spinner-wrap">
            <Loader2 size={15} className="spin-anim" />
          </div>
          <span className="loading-text">Đang tổng hợp thông tin & xử lý yêu cầu...</span>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
