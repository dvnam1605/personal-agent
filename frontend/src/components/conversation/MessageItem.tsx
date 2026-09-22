import React from 'react'
import {
  Sparkles,
  User,
  Calendar,
  Mail,
  FileText,
  Users,
  AlertCircle,
  RotateCcw,
} from 'lucide-react'
import type {
  ApprovalRequestResponse,
  CalendarEvent,
  GmailMessage,
  QueryResult,
  RAGCitation,
} from '../../types/api'
import { CalendarCard } from '../domain/CalendarCard'
import { EmailCard } from '../domain/EmailCard'
import { CitationCard } from '../domain/CitationCard'
import { MeetingDossierCard } from '../domain/MeetingDossierCard'
import { ApprovalCard } from '../safety/ApprovalCard'
import { QuestionPlaneCard } from '../safety/QuestionPlaneCard'
import { MarkdownContent } from '../ui/MarkdownContent'
import { NammLogo } from '../common/NammLogo'
import './MessageItem.css'

export interface ChatTurn {
  id: string
  userQuery: string
  timestamp: string
  result?: QueryResult
  pendingApproval?: ApprovalRequestResponse | null
  error?: string
  ttft?: number
  latency?: number
  tokPerSec?: number
  tokenCount?: number
}

interface MessageItemProps {
  turn: ChatTurn
  onSelectCitation: (citation: RAGCitation) => void
  onSelectEmailMessage?: (msg: GmailMessage) => void
  onApproveAction: (id: string, execute: boolean) => Promise<void>
  onDenyAction: (id: string, reason?: string) => Promise<void>
  onClarifyAnswer: (answer: string) => void
  onRetry?: (query: string) => void
}

export const MessageItem: React.FC<MessageItemProps> = ({
  turn,
  onSelectCitation,
  onSelectEmailMessage,
  onApproveAction,
  onDenyAction,
  onClarifyAnswer,
  onRetry,
}) => {
  const result = turn.result
  const domains = result?.route?.domains || []
  const data = result?.data || {}

  const getDomainLabel = () => {
    if (result?.route?.route_type === 'supervisor_dag' || domains.length > 1) {
      return { icon: <Sparkles size={12} />, label: 'Đa tác vụ (Supervisor)' }
    }
    if (result?.route?.target_workflow_id === 'WF-05') return { icon: <Users size={12} />, label: 'Hồ sơ họp WF-05' }
    if (result?.route?.target_workflow_id === 'WF-01') return { icon: <Mail size={12} />, label: 'Tổng hợp họp WF-01' }
    if (domains.includes('calendar')) return { icon: <Calendar size={12} />, label: 'Lịch Google' }
    if (domains.includes('communication')) return { icon: <Mail size={12} />, label: 'Gmail' }
    if (domains.includes('knowledge_research') || domains.includes('internal_doc')) {
      return { icon: <FileText size={12} />, label: 'Kho tri thức' }
    }
    return { icon: <Sparkles size={12} />, label: 'Trợ lý' }
  }

  const domain = getDomainLabel()

  // Format error gracefully
  const getFriendlyError = (rawError: string) => {
    if (rawError.includes('502') || rawError.includes('Bad Gateway') || rawError.includes('fetch')) {
      return 'Không thể kết nối với máy chủ. Vui lòng kiểm tra dịch vụ hoặc thử lại sau.'
    }
    if (rawError.includes('401') || rawError.includes('403')) {
      return 'Yêu cầu chưa được cấp quyền xác thực.'
    }
    return rawError
  }

  // Determine if clarification belongs to calendar creation or email drafting
  const isCalendarCreation =
    result?.route?.target_agent === 'CalendarAgent' ||
    domains.includes('calendar') ||
    Boolean(result?.message?.includes('Để tạo lịch')) ||
    Boolean(result?.message?.includes('thời gian bắt đầu'))

  const isEmailDraft =
    result?.route?.target_agent === 'CommunicationAgent' ||
    domains.includes('communication') ||
    Boolean(result?.message?.includes('soạn') && result?.message?.includes('email'))

  const suggestedChoices = isCalendarCreation
    ? ['10h sáng mai', '14h chiều nay', '9h sáng thứ Hai', 'Huỷ yêu cầu']
    : isEmailDraft
      ? ['Xin nghỉ phép', 'Báo cáo tiến độ', 'Gửi đối tác', 'Huỷ yêu cầu']
      : ['Hôm nay', 'Ngày mai', 'Tuần này', 'Huỷ yêu cầu']

  const handleClarifySubmit = (answer: string) => {
    const trimmed = answer.trim()
    if (!trimmed) return

    // If user explicitly asks to cancel
    if (trimmed.toLowerCase() === 'huỷ yêu cầu' || trimmed.toLowerCase() === 'hủy yêu cầu') {
      onClarifyAnswer('Huỷ yêu cầu')
      return
    }

    if (isCalendarCreation) {
      // If user typed a complete command like "Tạo cuộc họp ...", send directly
      const hasCreationVerb = /\b(tạo|đặt|book|create|schedule|lên lịch|xếp lịch)\b/i.test(trimmed)
      if (hasCreationVerb) {
        onClarifyAnswer(trimmed)
        return
      }

      // Preserve the user's initial creation intent
      const baseQuery = (turn.userQuery || '').replace(/[.,;:!?]+$/, '').trim()
      const baseHasCreationVerb = /\b(tạo|đặt|book|create|schedule|lên lịch|xếp lịch)\b/i.test(baseQuery)
      const queryPrefix = baseHasCreationVerb ? baseQuery : `Tạo cuộc họp ${baseQuery}`
      const separator = /^(lúc|vao|vào|ngày|ngay)\b/i.test(trimmed) ? ' ' : ' lúc '
      onClarifyAnswer(`${queryPrefix}${separator}${trimmed}`)
      return
    }

    if (isEmailDraft) {
      const hasDraftVerb = /\b(soạn|viết|gửi|email|mail|thư)\b/i.test(trimmed)
      if (hasDraftVerb) {
        onClarifyAnswer(trimmed)
        return
      }
      const baseQuery = (turn.userQuery || '').replace(/[.,;:!?]+$/, '').trim()
      const separator = /^(về|ve|cho|gui|gửi)\b/i.test(trimmed) ? ' ' : ' về '
      onClarifyAnswer(`${baseQuery}${separator}${trimmed}`)
      return
    }

    onClarifyAnswer(trimmed)
  }

  // Fallback effective approval request constructed from streamed proposal if API fetch is delayed
  const effectiveApproval: ApprovalRequestResponse | null =
    turn.pendingApproval ||
    (result?.status === 'needs_approval' && (result?.approval_id || result?.data?.proposal)
      ? {
        id: result.approval_id || 'pending-approval',
        run_id: result.run_id,
        action_type: result.data?.proposal?.action_type || 'create_event',
        description:
          result.data?.proposal?.description ||
          (result.message
            ? result.message.replace(/\.\s*Vui lòng xác nhận bên dưới.*$/i, '.')
            : 'Yêu cầu phê duyệt hành động ghi Google Calendar'),
        important_arguments: result.data?.proposal?.important_arguments || {},
        tool_name: result.data?.proposal?.tool_name || 'calendar.create_event',
        risk_level: result.data?.proposal?.risk_level || 'SENSITIVE',
        status: 'pending',
        created_at: new Date().toISOString(),
      }
      : null)

  return (
    <div className="chat-turn animate-slide-in">
      {/* 1. User Message */}
      <div className="user-message-row">
        <div className="user-message-bubble">
          <p className="user-message-text">{turn.userQuery}</p>
          <span className="user-message-time">{turn.timestamp}</span>
        </div>
        <div className="user-message-avatar">
          <User size={14} />
        </div>
      </div>

      {/* 2. Assistant Response */}
      <div className="assistant-message-row">
        <div className="assistant-avatar">
          <NammLogo size={16} />
        </div>

        <div className="assistant-content-container">
          <div className="assistant-header">
            <span className="assistant-name">Noat</span>
            <div className="assistant-domain-pill">
              {domain.icon}
              <span>{domain.label}</span>
            </div>
            {turn.ttft != null && (
              <span
                className="assistant-ttft-badge"
                title={`Thời gian nhận token đầu (TTFT): ${turn.ttft.toFixed(2)}s${turn.tokPerSec ? ` | Tốc độ: ${turn.tokPerSec.toFixed(1)} tok/s` : ''}${turn.tokenCount ? ` (${turn.tokenCount} tokens)` : ''}`}
              >
                ⚡ TTFT: {turn.ttft.toFixed(2)}s
                {turn.tokPerSec != null && turn.tokPerSec > 0 && (
                  <span className="assistant-tok-badge-stat"> • {turn.tokPerSec.toFixed(1)} tok/s</span>
                )}
              </span>
            )}
          </div>

          {/* Assistant Text Message Body */}
          {result && (
            <MarkdownContent content={result.message} className="assistant-body-markdown" />
          )}

          {/* Error Message with single clean card & Retry button */}
          {turn.error && (
            <div className="assistant-error-card">
              <div className="assistant-error-icon">
                <AlertCircle size={16} />
              </div>
              <div className="assistant-error-details">
                <span className="assistant-error-text">
                  {getFriendlyError(turn.error)}
                </span>
                {onRetry && (
                  <button
                    className="btn-error-retry"
                    onClick={() => onRetry(turn.userQuery)}
                  >
                    <RotateCcw size={12} />
                    <span>Thử lại</span>
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Blocked message with 1-click Google connect */}
          {result?.status === 'blocked' && (
            <div className="assistant-blocked-box">
              <p>Google Workspace cần được cấp quyền bổ sung để thực hiện yêu cầu này.</p>
              <a href="/auth/google/start" className="btn-reconnect-google">
                Kết nối lại Google
              </a>
            </div>
          )}

          {/* Domain Specific Cards */}
          {/* Calendar Events */}
          {data.events && Array.isArray(data.events) && (
            <CalendarCard events={data.events as CalendarEvent[]} window={data.window} />
          )}

          {/* Gmail Messages */}
          {data.messages && Array.isArray(data.messages) && (
            <EmailCard
              messages={data.messages as GmailMessage[]}
              onSelectMessage={onSelectEmailMessage}
            />
          )}

          {/* Meeting Prep Dossier */}
          {data.meeting && (
            <MeetingDossierCard
              meeting={data.meeting}
              discussions={data.discussions}
              documents={data.documents}
              onSelectCitation={onSelectCitation}
            />
          )}

          {/* RAG Citations */}
          {data.citations && Array.isArray(data.citations) && data.citations.length > 0 && (
            <CitationCard
              citations={data.citations as RAGCitation[]}
              sufficiency={data.sufficiency}
              onSelectCitation={onSelectCitation}
            />
          )}

          {/* Question Plane Clarification Card */}
          {result?.status === 'clarification_needed' && (
            <QuestionPlaneCard
              questionText={result.message}
              suggestedChoices={suggestedChoices}
              onAnswer={handleClarifySubmit}
            />
          )}

          {/* HITL Approval Card */}
          {effectiveApproval && (
            <ApprovalCard
              approval={effectiveApproval}
              onApprove={onApproveAction}
              onDeny={onDenyAction}
            />
          )}
        </div>
      </div>
    </div>
  )
}
