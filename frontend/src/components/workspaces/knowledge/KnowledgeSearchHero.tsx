import React from 'react'
import { Search, Sparkles } from 'lucide-react'

const SAMPLE_QUERIES = [
  'Quy chế làm việc của Đài TNVN',
  'Chương trình tiết kiệm, chống lãng phí 2026',
  'Quy chế văn thư, lưu trữ và ký số',
  'Tuyển dụng viên chức',
  'Danh hiệu Chiến sĩ thi đua cơ sở',
]

interface KnowledgeSearchHeroProps {
  searchQuery: string
  isLoading: boolean
  onQueryChange: (query: string) => void
  onSearch: (queryToRun?: string) => void
}

export const KnowledgeSearchHero: React.FC<KnowledgeSearchHeroProps> = ({
  searchQuery,
  isLoading,
  onQueryChange,
  onSearch,
}) => {
  return (
    <div className="knowledge-search-hero">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSearch()
        }}
        className="knowledge-search-form"
      >
        <div className="knowledge-input-wrap">
          <Search size={18} className="knowledge-search-icon" />
          <input
            type="text"
            className="knowledge-input"
            placeholder="Tra cứu quy chế, văn bản, quyết định hoặc tài liệu nội bộ..."
            value={searchQuery}
            onChange={(e) => onQueryChange(e.target.value)}
          />
          {searchQuery && (
            <button
              type="button"
              className="knowledge-clear-btn"
              onClick={() => onQueryChange('')}
              title="Xóa tìm kiếm"
            >
              ✕
            </button>
          )}
        </div>
        <button
          type="submit"
          className="knowledge-submit-btn"
          disabled={!searchQuery.trim() || isLoading}
        >
          <Sparkles size={14} />
          <span>Tra cứu với AI</span>
        </button>
      </form>

      {/* Quick Sample Queries */}
      <div className="knowledge-sample-chips">
        <span className="sample-label">Gợi ý tra cứu:</span>
        {SAMPLE_QUERIES.map((query, i) => (
          <button
            key={i}
            className="sample-chip"
            onClick={() => {
              onQueryChange(query)
              onSearch(query)
            }}
          >
            {query}
          </button>
        ))}
      </div>
    </div>
  )
}
