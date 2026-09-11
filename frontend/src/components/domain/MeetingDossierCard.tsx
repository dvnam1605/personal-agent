import React, { useState } from 'react'
import {
  Users,
  Calendar,
  Clock,
  Mail,
  FileText,
  HelpCircle,
} from 'lucide-react'
import type { RAGCitation } from '../../types/api'
import './DomainCards.css'

interface MeetingDossierCardProps {
  meeting: {
    id: string
    summary: string
    when: string
    attendees: string[]
  }
  discussions?: string[]
  documents?: RAGCitation[]
  onSelectCitation?: (citation: RAGCitation) => void
}

export const MeetingDossierCard: React.FC<MeetingDossierCardProps> = ({
  meeting,
  discussions = [],
  documents = [],
  onSelectCitation,
}) => {
  const [activeTab, setActiveTab] = useState<'overview' | 'discussions' | 'documents'>('overview')

  return (
    <div className="meeting-dossier-card">
      <div className="dossier-header">
        <div className="dossier-header__badge">
          <Users size={15} />
          <span>Hồ sơ Chuẩn bị Họp</span>
        </div>
        <h3 className="dossier-header__title">{meeting.summary || 'Cuộc họp'}</h3>
        {meeting.when && (
          <div className="dossier-header__time">
            <Clock size={13} />
            <span>{meeting.when}</span>
          </div>
        )}
      </div>

      {/* Tabs Bar */}
      <div className="dossier-tabs" role="tablist">
        <button
          className={`dossier-tab-btn ${activeTab === 'overview' ? 'dossier-tab-btn--active' : ''}`}
          onClick={() => setActiveTab('overview')}
          role="tab"
          aria-selected={activeTab === 'overview'}
        >
          <Calendar size={14} />
          <span>Khách mời ({meeting.attendees?.length || 0})</span>
        </button>

        {discussions.length > 0 && (
          <button
            className={`dossier-tab-btn ${activeTab === 'discussions' ? 'dossier-tab-btn--active' : ''}`}
            onClick={() => setActiveTab('discussions')}
            role="tab"
            aria-selected={activeTab === 'discussions'}
          >
            <Mail size={14} />
            <span>Email gần đây ({discussions.length})</span>
          </button>
        )}

        {documents.length > 0 && (
          <button
            className={`dossier-tab-btn ${activeTab === 'documents' ? 'dossier-tab-btn--active' : ''}`}
            onClick={() => setActiveTab('documents')}
            role="tab"
            aria-selected={activeTab === 'documents'}
          >
            <FileText size={14} />
            <span>Tài liệu liên quan ({documents.length})</span>
          </button>
        )}
      </div>

      {/* Tab Content */}
      <div className="dossier-content">
        {activeTab === 'overview' && (
          <div className="dossier-section">
            <h4 className="dossier-section__heading">Thành phần tham gia</h4>
            {meeting.attendees && meeting.attendees.length > 0 ? (
              <div className="attendees-list">
                {meeting.attendees.map((attendee, i) => (
                  <div key={i} className="attendee-chip">
                    <div className="attendee-avatar">{attendee[0]?.toUpperCase() || 'U'}</div>
                    <span className="attendee-email">{attendee}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="dossier-empty-note">Không có khách mời cụ thể được ghi nhận.</p>
            )}

            <div className="meeting-prep-tips">
              <div className="tips-title">
                <HelpCircle size={14} />
                <span>Gợi ý chuẩn bị</span>
              </div>
              <ul className="tips-list">
                <li>Rà soát các điểm còn mở từ các email trao đổi gần nhất.</li>
                <li>Chuẩn bị sẵn tài liệu quy chế nội bộ có liên quan.</li>
              </ul>
            </div>
          </div>
        )}

        {activeTab === 'discussions' && (
          <div className="dossier-section">
            <h4 className="dossier-section__heading">Nội dung trao đổi qua Gmail gần nhất</h4>
            <div className="discussions-list">
              {discussions.map((disc, idx) => (
                <div key={idx} className="discussion-item">
                  <span className="discussion-bullet">•</span>
                  <span className="discussion-text">{disc}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'documents' && (
          <div className="dossier-section">
            <h4 className="dossier-section__heading">Tài liệu nội bộ liên quan</h4>
            <div className="dossier-docs-list">
              {documents.map((doc, idx) => (
                <div
                  key={idx}
                  className="dossier-doc-item"
                  onClick={() => onSelectCitation?.(doc)}
                >
                  <FileText size={15} className="dossier-doc-item__icon" />
                  <div className="dossier-doc-item__details">
                    <span className="dossier-doc-item__title">{doc.title}</span>
                    {doc.page_number && (
                      <span className="dossier-doc-item__page">Trang {doc.page_number}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
