import React from 'react'
import { RefreshCw, ExternalLink, Sparkles, ArrowLeft } from 'lucide-react'
import type { RAGCitation } from '../../../types/api'
import { MarkdownContent } from '../../ui/MarkdownContent'
import { INTERNAL_DOCUMENTS } from './internalDocuments'

interface KnowledgeResultsProps {
  answer: string | null
  citations: RAGCitation[]
  isLoading: boolean
  onBack: () => void
  onSelectCitation: (citation: RAGCitation) => void
}

/** Search results view: AI answer plus the citations extracted from source documents. */
export const KnowledgeResults: React.FC<KnowledgeResultsProps> = ({
  answer,
  citations,
  isLoading,
  onBack,
  onSelectCitation,
}) => {
  return (
    <div className="knowledge-results-layout">
      <div className="knowledge-back-bar">
        <button className="btn-back-to-catalog" onClick={onBack}>
          <ArrowLeft size={14} />
          <span>Quay lại danh mục văn bản ({INTERNAL_DOCUMENTS.length} tài liệu)</span>
        </button>
      </div>

      {/* AI Summarized Answer */}
      {answer !== null && (
        <div className="knowledge-answer-card">
          <div className="answer-header">
            <Sparkles size={16} className="text-primary" />
            <h4>Tổng hợp nội dung từ quy chế & tài liệu nội bộ</h4>
            {isLoading && (
              <span className="knowledge-streaming-indicator">
                <RefreshCw size={12} className="spin-anim" /> Đang phản hồi...
              </span>
            )}
          </div>
          <div className="answer-body">
            <MarkdownContent content={answer} />
          </div>
        </div>
      )}

      {/* Citations Grid */}
      <div className="knowledge-citations-section">
        <h4 className="citations-section-title">
          Dẫn chứng trích xuất từ văn bản gốc ({citations.length})
        </h4>

        {citations.length === 0 ? (
          <p className="no-citations-hint">
            {isLoading ? 'Đang trích xuất dẫn chứng kèm theo...' : 'Không có đoạn trích dẫn cụ thể nào.'}
          </p>
        ) : (
          <div className="citations-grid">
            {citations.map((cite, idx) => (
              <div
                key={idx}
                className="knowledge-citation-card"
                onClick={() => onSelectCitation(cite)}
              >
                <div className="citation-top">
                  <span className="citation-index">[{idx + 1}]</span>
                  <span className="citation-title">{cite.title}</span>
                  {cite.page_number && (
                    <span className="citation-page">Trang {cite.page_number}</span>
                  )}
                </div>

                {cite.section_title && (
                  <h5 className="citation-section">{cite.section_title}</h5>
                )}

                <p className="citation-snippet">{cite.text}</p>

                <div className="citation-bottom">
                  <span className="citation-view-link">
                    <span>Xem chi tiết dẫn chứng</span>
                    <ExternalLink size={11} />
                  </span>
                  {cite.score && (
                    <span className="citation-score">
                      Khớp {Math.round(cite.score * 100)}%
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
