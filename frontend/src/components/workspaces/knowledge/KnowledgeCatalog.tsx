import React from 'react'
import { Sparkles, FileText, Eye, CheckCircle2 } from 'lucide-react'
import { INTERNAL_DOCUMENTS, type InternalDoc } from './internalDocuments'
import { Hint } from '../../ui/Hint'

interface KnowledgeCatalogProps {
  docs: InternalDoc[]
  selectedCategory: string
  onSelectCategory: (category: string) => void
  onRunQuery: (query: string) => void
}

/** Default view: the corporate knowledge catalog with category filters. */
export const KnowledgeCatalog: React.FC<KnowledgeCatalogProps> = ({
  docs,
  selectedCategory,
  onSelectCategory,
  onRunQuery,
}) => {
  return (
    <div className="knowledge-catalog-view">
      {/* Catalog Header & Category Tabs */}
      <div className="catalog-header-bar">
        <div className="catalog-title-wrap">
          <h3 className="catalog-heading">Danh mục Quyết định & Văn bản Đài TNVN</h3>
          <p className="catalog-subheading">
            37 quyết định, chỉ thị đã được số hóa và ingest vào kho RAG — mọi thẻ đều tra cứu được.
          </p>
        </div>

        {/* Category Filter Pills */}
        <div className="catalog-category-pills">
          <button
            className={`category-pill ${selectedCategory === 'all' ? 'category-pill--active' : ''}`}
            onClick={() => onSelectCategory('all')}
          >
            Tất cả ({INTERNAL_DOCUMENTS.length})
          </button>
          <button
            className={`category-pill ${selectedCategory === 'policy' ? 'category-pill--active' : ''}`}
            onClick={() => onSelectCategory('policy')}
          >
            Quy chế & Chỉ đạo
          </button>
          <button
            className={`category-pill ${selectedCategory === 'finance' ? 'category-pill--active' : ''}`}
            onClick={() => onSelectCategory('finance')}
          >
            Tài chính & Dự toán
          </button>
          <button
            className={`category-pill ${selectedCategory === 'hr' ? 'category-pill--active' : ''}`}
            onClick={() => onSelectCategory('hr')}
          >
            Nhân sự & Đào tạo
          </button>
          <button
            className={`category-pill ${selectedCategory === 'tech' ? 'category-pill--active' : ''}`}
            onClick={() => onSelectCategory('tech')}
          >
            Khoa học & Phát sóng
          </button>
        </div>
      </div>

      {/* Document Cards Grid */}
      <div className="knowledge-docs-grid">
        {docs.map((doc) => (
          <div key={doc.id} className="knowledge-doc-item-card">
            <div className="doc-card-header">
              <span className="doc-code-badge">{doc.code}</span>
              <span className="doc-category-badge">{doc.categoryLabel}</span>
            </div>

            <h4 className="doc-card-title">{doc.title}</h4>

            <p className="doc-card-summary">{doc.summary}</p>

            <div className="doc-card-meta">
              <span className="meta-item">
                <FileText size={12} />
                <span>{doc.pages} trang</span>
              </span>
              <span className="meta-item">
                <CheckCircle2 size={12} className="text-safe" />
                <span>Hiệu lực: {doc.effectiveDate}</span>
              </span>
              <span className="meta-authority">{doc.authority}</span>
            </div>

            {/* Actions on Document Card */}
            <div className="doc-card-actions">
              <Hint label="Yêu cầu Trợ lý AI tra cứu văn bản này">
                <button
                  className="doc-action-btn doc-action-btn--primary"
                  onClick={() => onRunQuery(doc.samplePrompt)}
                >
                  <Sparkles size={13} />
                  <span>Tra cứu quy định</span>
                </button>
              </Hint>

              <Hint label="Tóm tắt văn bản này">
                <button
                  className="doc-action-btn doc-action-btn--secondary"
                  onClick={() => onRunQuery(`Tóm tắt nội dung văn bản ${doc.title}`)}
                >
                  <Eye size={13} />
                  <span>Tóm tắt</span>
                </button>
              </Hint>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
