import React, { useState } from 'react'
import { FileText, Mail, ExternalLink } from 'lucide-react'
import { Drawer } from '../ui/Drawer'
import type { GmailMessage, RAGCitation } from '../../types/api'

/** Keeps the last non-null value so a closing drawer still has content to animate out. */
function useRetained<T>(value: T | null): T | null {
  const [kept, setKept] = useState(value)
  if (value !== null && value !== kept) setKept(value)
  return value ?? kept
}

interface CitationDrawerProps {
  citation: RAGCitation | null
  onClose: () => void
}

export const CitationDrawer: React.FC<CitationDrawerProps> = ({ citation, onClose }) => {
  const selectedCitation = useRetained(citation)
  return (
    <Drawer
      isOpen={Boolean(citation)}
      onClose={onClose}
      title={selectedCitation?.title || 'Dẫn chứng tài liệu'}
      subtitle={
        selectedCitation?.page_number
          ? `Văn bản nội bộ · Trang ${selectedCitation.page_number}`
          : 'Kho tri thức nội bộ'
      }
    >
      {selectedCitation && (
        <div className="drawer-evidence-view">
          <div className="evidence-badge-row">
            <span className="evidence-type-badge">
              <FileText size={12} /> {selectedCitation.source_type || 'Tài liệu'}
            </span>
            {selectedCitation.score && (
              <span className="evidence-score-badge">
                Độ khớp: {Math.round(selectedCitation.score * 100)}%
              </span>
            )}
          </div>

          {selectedCitation.section_title && (
            <h4 className="evidence-section-title">{selectedCitation.section_title}</h4>
          )}

          <div className="evidence-quote-box">
            <p>{selectedCitation.text}</p>
          </div>

          {selectedCitation.source_uri && (
            <div className="evidence-source-link">
              <a href={selectedCitation.source_uri} target="_blank" rel="noreferrer">
                <span>Mở tệp gốc</span> <ExternalLink size={12} />
              </a>
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}

interface EmailDrawerProps {
  email: GmailMessage | null
  onClose: () => void
}

export const EmailDrawer: React.FC<EmailDrawerProps> = ({ email, onClose }) => {
  const selectedEmail = useRetained(email)
  return (
    <Drawer
      isOpen={Boolean(email)}
      onClose={onClose}
      title={selectedEmail?.subject || 'Chi tiết email'}
      subtitle={`Từ: ${selectedEmail?.from}`}
    >
      {selectedEmail && (
        <div className="drawer-email-view">
          <div className="email-meta-box">
            <div className="email-meta-row">
              <span className="email-meta-label">Người gửi:</span>
              <span className="email-meta-value">{selectedEmail.from}</span>
            </div>
            {selectedEmail.when && (
              <div className="email-meta-row">
                <span className="email-meta-label">Thời gian:</span>
                <span className="email-meta-value">
                  {new Date(selectedEmail.when).toLocaleString('vi-VN')}
                </span>
              </div>
            )}
          </div>

          <div className="email-body-box">
            <p>{selectedEmail.snippet}</p>
          </div>

          <div className="email-actions-box">
            <a
              href={`https://mail.google.com/mail/u/0/#inbox/${selectedEmail.thread_id || selectedEmail.id}`}
              target="_blank"
              rel="noreferrer"
              className="btn-open-gmail"
            >
              <Mail size={14} /> Mở trong Gmail
            </a>
          </div>
        </div>
      )}
    </Drawer>
  )
}
