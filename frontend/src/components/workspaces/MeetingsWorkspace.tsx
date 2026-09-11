import React, { useState, useEffect } from 'react'
import {
  Users,
  RefreshCw,
  Clock,
  MapPin,
  Mail,
  FileText,
  CheckSquare,
  AlertCircle,
  ExternalLink,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { RAGCitation } from '../../types/api'
import './Workspaces.css'

interface MeetingsWorkspaceProps {
  onSelectCitation: (citation: RAGCitation) => void
  onAskPrepQuestion: (prompt: string) => void
}

export const MeetingsWorkspace: React.FC<MeetingsWorkspaceProps> = ({
  onSelectCitation,
  onAskPrepQuestion,
}) => {
  const [dossier, setDossier] = useState<{
    meeting?: Record<string, any>
    discussions?: any[]
    documents?: any[]
  } | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'brief' | 'emails' | 'docs' | 'notes'>('brief')

  const fetchMeetingPrep = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const res = await apiClient.submitQuery('Chuẩn bị cho cuộc họp tiếp theo')
      if (res.data?.meeting) {
        setDossier({
          meeting: res.data.meeting,
          discussions: res.data.discussions || [],
          documents: res.data.documents || [],
        })
      } else {
        setDossier(null)
      }
    } catch (err: any) {
      setError(err?.message || 'Không thể tổng hợp hồ sơ chuẩn bị cuộc họp.')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchMeetingPrep()
  }, [])

  const meeting = dossier?.meeting

  return (
    <div className="workspace-container meetings-workspace">
      {/* Workspace Toolbar */}
      <div className="workspace-toolbar">
        <div className="toolbar-left">
          <div className="toolbar-filter-group">
            <button
              className={`toolbar-filter-btn ${activeTab === 'brief' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setActiveTab('brief')}
            >
              Hồ sơ tổng quan
            </button>
            <button
              className={`toolbar-filter-btn ${activeTab === 'emails' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setActiveTab('emails')}
            >
              Trao đổi gần đây ({dossier?.discussions?.length || 0})
            </button>
            <button
              className={`toolbar-filter-btn ${activeTab === 'docs' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setActiveTab('docs')}
            >
              Tài liệu liên quan ({dossier?.documents?.length || 0})
            </button>
            <button
              className={`toolbar-filter-btn ${activeTab === 'notes' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setActiveTab('notes')}
            >
              Checklist & Ghi chú
            </button>
          </div>
        </div>

        <div className="toolbar-right">
          <button
            className="toolbar-btn"
            onClick={fetchMeetingPrep}
            disabled={isLoading}
            title="Làm mới hồ sơ họp"
          >
            <RefreshCw size={14} className={isLoading ? 'spin-anim' : ''} />
            <span>Tổng hợp lại</span>
          </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="workspace-content-scroll">
        {isLoading ? (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim text-primary" />
            <span>Đang tra cứu lịch, trao đổi email và văn bản nội bộ cho cuộc họp...</span>
          </div>
        ) : error ? (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Không thể lập hồ sơ họp</h4>
              <p>{error}</p>
            </div>
            <button className="btn-retry" onClick={fetchMeetingPrep}>
              Thử lại
            </button>
          </div>
        ) : !meeting ? (
          <div className="workspace-empty-state">
            <div className="empty-icon-wrap">
              <Users size={32} />
            </div>
            <h3>Không tìm thấy cuộc họp sắp tới</h3>
            <p>
              Bạn chưa có cuộc họp nào trong lịch trình sắp tới hoặc trợ lý chưa thể xác định được cuộc họp tiếp theo.
            </p>
            <button
              className="btn-action-primary"
              onClick={() => onAskPrepQuestion('Chuẩn bị cuộc họp tiếp theo')}
            >
              Yêu cầu trợ lý kiểm tra lại
            </button>
          </div>
        ) : (
          <div className="meeting-dossier-workspace-view">
            {/* Top Meeting Header Banner */}
            <div className="meeting-hero-card">
              <div className="meeting-hero-header">
                <span className="meeting-hero-badge">Cuộc họp tiếp theo</span>
                {meeting.hangout_link && (
                  <a
                    href={meeting.hangout_link}
                    target="_blank"
                    rel="noreferrer"
                    className="btn-meeting-link"
                  >
                    <span>Tham gia Meet</span>
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>

              <h2 className="meeting-hero-title">{meeting.title || 'Cuộc họp thảo luận'}</h2>

              <div className="meeting-hero-meta-grid">
                <div className="meta-grid-item">
                  <Clock size={14} />
                  <span>
                    {meeting.start_time
                      ? new Date(meeting.start_time).toLocaleString('vi-VN', {
                          weekday: 'short',
                          hour: '2-digit',
                          minute: '2-digit',
                          day: 'numeric',
                          month: 'numeric',
                        })
                      : 'Chưa rõ thời gian'}
                  </span>
                </div>

                {meeting.location && (
                  <div className="meta-grid-item">
                    <MapPin size={14} />
                    <span>{meeting.location}</span>
                  </div>
                )}

                <div className="meta-grid-item">
                  <Users size={14} />
                  <span>{meeting.participants?.length || 0} người tham gia</span>
                </div>
              </div>
            </div>

            {/* Tab: Brief */}
            {activeTab === 'brief' && (
              <div className="dossier-section-grid">
                <div className="dossier-card">
                  <h4 className="dossier-card-title">Danh sách người tham dự</h4>
                  <div className="attendee-chips-list">
                    {meeting.participants?.map((p: any, i: number) => (
                      <div key={i} className="attendee-chip">
                        <div className="attendee-avatar">{p.name ? p.name[0] : 'U'}</div>
                        <div className="attendee-text">
                          <span className="attendee-name">{p.name || p.email}</span>
                          {p.name && <span className="attendee-email">{p.email}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="dossier-card">
                  <h4 className="dossier-card-title">Mục tiêu & Tóm tắt nhanh</h4>
                  <p className="dossier-text-summary">
                    {meeting.summary ||
                      'Cuộc họp được sắp xếp để thảo luận các đầu mục công việc và thống nhất tiến độ triển khai.'}
                  </p>
                </div>
              </div>
            )}

            {/* Tab: Discussions */}
            {activeTab === 'emails' && (
              <div className="dossier-card">
                <h4 className="dossier-card-title">Trao đổi qua email với các thành viên</h4>
                {(!dossier.discussions || dossier.discussions.length === 0) ? (
                  <p className="dossier-empty-hint">Chưa ghi nhận chuỗi email trao đổi nào gần đây.</p>
                ) : (
                  <div className="discussions-list">
                    {dossier.discussions.map((d, i) => (
                      <div key={i} className="discussion-thread-item">
                        <div className="discussion-icon">
                          <Mail size={14} />
                        </div>
                        <div className="discussion-body">
                          <div className="discussion-header">
                            <span className="discussion-sender">{d.sender || d.from}</span>
                            <span className="discussion-date">{d.date || d.when}</span>
                          </div>
                          <span className="discussion-subject">{d.subject}</span>
                          <p className="discussion-snippet">{d.snippet || d.summary}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Tab: Docs */}
            {activeTab === 'docs' && (
              <div className="dossier-card">
                <h4 className="dossier-card-title">Tài liệu & Dẫn chứng liên quan</h4>
                {(!dossier.documents || dossier.documents.length === 0) ? (
                  <p className="dossier-empty-hint">Không có tài liệu nội bộ nào được liên kết với cuộc họp này.</p>
                ) : (
                  <div className="dossier-docs-list">
                    {dossier.documents.map((doc, i) => (
                      <div
                        key={i}
                        className="dossier-doc-card"
                        onClick={() => onSelectCitation(doc)}
                      >
                        <FileText size={16} className="dossier-doc-icon" />
                        <div className="dossier-doc-meta">
                          <span className="dossier-doc-title">{doc.title || doc.source_title}</span>
                          <span className="dossier-doc-page">
                            {doc.page_number ? `Trang ${doc.page_number}` : 'Tài liệu nội bộ'}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Tab: Notes & Checklist */}
            {activeTab === 'notes' && (
              <div className="dossier-card">
                <h4 className="dossier-card-title">Checklist chuẩn bị</h4>
                <div className="prep-checklist">
                  <div className="checklist-item">
                    <CheckSquare size={16} className="text-primary" />
                    <span>Đọc lại trao đổi qua email gần nhất với các bên</span>
                  </div>
                  <div className="checklist-item">
                    <CheckSquare size={16} className="text-primary" />
                    <span>Kiểm tra tài liệu nội bộ và các số liệu liên quan</span>
                  </div>
                  <div className="checklist-item">
                    <CheckSquare size={16} className="text-primary" />
                    <span>Chuẩn bị danh sách các câu hỏi và rủi ro cần làm rõ</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
