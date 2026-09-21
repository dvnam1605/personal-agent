import React from 'react'
import {
  Calendar,
  Mail,
  Users,
  FileText,
  ChevronRight,
} from 'lucide-react'
import type { ChatTurn } from '../conversation/MessageItem'
import type { GmailMessage, RAGCitation } from '../../types/api'
import { MessageList } from '../conversation/MessageList'
import { Composer } from '../conversation/Composer'
import { NammLogo } from '../common/NammLogo'
import './Workspaces.css'

interface AssistantWorkspaceProps {
  turns: ChatTurn[]
  isLoading: boolean
  onSendMessage: (query: string) => void
  onSelectCitation: (citation: RAGCitation) => void
  onSelectEmailMessage?: (msg: GmailMessage) => void
  onApproveAction: (id: string, execute: boolean) => Promise<void>
  onDenyAction: (id: string, reason?: string) => Promise<void>
  onClarifyAnswer: (answer: string) => void
  onNavigateWorkspace?: (ws: 'calendar' | 'email' | 'meetings' | 'knowledge') => void
}

export const AssistantWorkspace: React.FC<AssistantWorkspaceProps> = ({
  turns,
  isLoading,
  onSendMessage,
  onSelectCitation,
  onSelectEmailMessage,
  onApproveAction,
  onDenyAction,
  onClarifyAnswer,
}) => {
  const isConversationActive = turns.length > 0

  const quickCapabilities = [
    {
      id: 'cal',
      icon: <Calendar size={18} />,
      title: 'Lịch & Lịch trình',
      desc: 'Kiểm tra cuộc họp, lịch rảnh và tạo sự kiện Google Calendar.',
      prompt: 'Lịch của tôi hôm nay và ngày mai?',
    },
    {
      id: 'mail',
      icon: <Mail size={18} />,
      title: 'Hộp thư Gmail',
      desc: 'Đọc email mới nhất, tóm tắt chuỗi trao đổi và soạn bản nháp.',
      prompt: 'Đọc các email mới nhất trong hộp thư đến',
    },
    {
      id: 'meetings',
      icon: <Users size={18} />,
      title: 'Chuẩn bị cuộc họp',
      desc: 'Tổng hợp người tham dự, email liên quan và tài liệu cần xem trước.',
      prompt: 'Chuẩn bị cuộc họp tiếp theo',
    },
    {
      id: 'knowledge',
      icon: <FileText size={18} />,
      title: 'Kho tri thức nội bộ',
      desc: 'Tra cứu quy chế công ty, quyết định và chính sách nhân sự.',
      prompt: 'Tài liệu nội bộ nói gì về quy chế làm việc?',
    },
  ]

  return (
    <div className="executive-cockpit-layout">
      {/* Main Conversation Feed / Cockpit Stage */}
      <div className="cockpit-main-stage">
        <div className="assistant-feed-container">
          {!isConversationActive ? (
            <div className="cockpit-welcome-view animate-fade-in">
              {/* Date & Title Banner */}
              <div className="cockpit-hero-banner">
                <div className="hero-status-pill">
                  <NammLogo size={14} />
                  <span>Naot — Sẵn sàng phục vụ</span>
                </div>
                <h2 className="cockpit-hero-title">Xin chào, tôi là Naot</h2>
                <p className="cockpit-hero-desc">
                  Trợ lý điều hành thông minh kết nối an toàn với Google Calendar, Gmail và kho dữ liệu quy chế nội bộ. Mọi thao tác gửi email hay thay đổi lịch trình đều được chuẩn bị kỹ lưỡng và có sự phê duyệt bảo vệ của bạn.
                </p>
              </div>

              {/* 4 Rich Capability Cards */}
              <div className="cockpit-cards-grid">
                {quickCapabilities.map((cap) => (
                  <div
                    key={cap.id}
                    className="cockpit-action-card"
                    onClick={() => onSendMessage(cap.prompt)}
                  >
                    <div className="action-card-header">
                      <div className="action-card-icon-wrap">{cap.icon}</div>
                      <span className="action-card-launch-hint">
                        <span>Thực hiện</span>
                        <ChevronRight size={13} />
                      </span>
                    </div>
                    <h3 className="action-card-title">{cap.title}</h3>
                    <p className="action-card-desc">{cap.desc}</p>
                    <div className="action-card-footer">
                      <span className="action-card-prompt-badge">"{cap.prompt}"</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <MessageList
              turns={turns}
              isLoading={isLoading}
              onQuickPrompt={onSendMessage}
              onSelectCitation={onSelectCitation}
              onSelectEmailMessage={onSelectEmailMessage}
              onApproveAction={onApproveAction}
              onDenyAction={onDenyAction}
              onClarifyAnswer={onClarifyAnswer}
              onRetryQuery={onSendMessage}
            />
          )}
        </div>

        {/* Composer */}
        <div className="assistant-composer-wrapper">
          <Composer onSendMessage={onSendMessage} isLoading={isLoading} />
        </div>
      </div>
    </div>
  )
}
