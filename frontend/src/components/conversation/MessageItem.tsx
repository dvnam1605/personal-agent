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
    if (domains.includes('calendar')) return { icon: <Calendar size={12} />, label: 'Lịch Google' }
    if (domains.includes('communication')) return { icon: <Mail size={12} />, label: 'Gmail' }
    if (domains.includes('internal_doc')) return { icon: <FileText size={12} />, label: 'Văn bản nội bộ' }
    if (result?.route?.target_workflow_id === 'WF-05') return { icon: <Users size={12} />, label: 'Hồ sơ họp' }
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
            <span className="assistant-name">Namm Agent</span>
            <div className="assistant-domain-pill">
              {domain.icon}
              <span>{domain.label}</span>
            </div>
            {turn.ttft != null && (
              <span className="assistant-ttft-badge" title="Thời gian nhận token đầu tiên (TTFT)">
                ⚡ TTFT: {turn.ttft.toFixed(2)}s
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
              onAnswer={onClarifyAnswer}
            />
          )}

          {/* HITL Approval Card */}
          {turn.pendingApproval && (
            <ApprovalCard
              approval={turn.pendingApproval}
              onApprove={onApproveAction}
              onDeny={onDenyAction}
            />
          )}
        </div>
      </div>
    </div>
  )
}
