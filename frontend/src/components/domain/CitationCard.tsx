import React from 'react'
import { FileText, ChevronRight } from 'lucide-react'
import type { RAGCitation } from '../../types/api'
import './DomainCards.css'

interface CitationCardProps {
  citations: RAGCitation[]
  sufficiency?: string
  onSelectCitation: (citation: RAGCitation) => void
}

export const CitationCard: React.FC<CitationCardProps> = ({
  citations,
  sufficiency,
  onSelectCitation,
}) => {
  if (!citations || citations.length === 0) return null

  return (
    <div className="citation-container">
      <div className="citation-header">
        <FileText size={14} className="domain-icon--rag" />
        <span className="citation-header__label">Dẫn chứng tài liệu nội bộ ({citations.length} nguồn)</span>
        {sufficiency && (
          <span className="citation-header__sufficiency">
            {sufficiency === 'sufficient' ? 'Đầy đủ dẫn chứng' : 'Tham khảo'}
          </span>
        )}
      </div>

      <div className="citation-pills-list">
        {citations.map((cite, idx) => (
          <button
            key={cite.chunk_id || idx}
            className="citation-pill-btn"
            onClick={() => onSelectCitation(cite)}
            title={`Click để xem đoạn trích: ${cite.title}`}
          >
            <span className="citation-pill-btn__index">{idx + 1}</span>
            <span className="citation-pill-btn__title">{cite.title}</span>
            {cite.page_number && (
              <span className="citation-pill-btn__page">Trang {cite.page_number}</span>
            )}
            <ChevronRight size={13} className="citation-pill-btn__arrow" />
          </button>
        ))}
      </div>
    </div>
  )
}
