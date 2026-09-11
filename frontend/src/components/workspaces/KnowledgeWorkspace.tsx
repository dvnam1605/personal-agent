import React, { useState, useMemo } from 'react'
import {
  Search,
  RefreshCw,
  ExternalLink,
  Sparkles,
  AlertCircle,
  FileText,
  ArrowLeft,
  Eye,
  CheckCircle2,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { RAGCitation } from '../../types/api'
import './Workspaces.css'

interface KnowledgeWorkspaceProps {
  onSelectCitation: (citation: RAGCitation) => void
}

interface InternalDoc {
  id: string
  code: string
  title: string
  category: 'policy' | 'finance' | 'hr' | 'security'
  categoryLabel: string
  effectiveDate: string
  authority: string
  summary: string
  pages: number
  samplePrompt: string
}

const INTERNAL_DOCUMENTS: InternalDoc[] = [
  {
    id: 'doc-01',
    code: 'QC-05/MEET',
    title: 'Quy chế tổ chức cuộc họp và nguyên tắc gửi tài liệu trước 24 giờ',
    category: 'policy',
    categoryLabel: 'Quy chế điều hành',
    effectiveDate: '15/01/2026',
    authority: 'Văn phòng Điều hành',
    summary:
      'Quy định bắt buộc đối với tất cả cuộc họp nội bộ: người chủ trì phải chuẩn bị hồ sơ (dossier), gửi chương trình nghị sự (agenda) trước tối thiểu 24 giờ và ghi nhận biên bản.',
    pages: 12,
    samplePrompt: 'Tài liệu nội bộ nói gì về quy chế họp và yêu cầu gửi agenda trước 24 giờ?',
  },
  {
    id: 'doc-02',
    code: 'QC-01/HR',
    title: 'Quy chế làm việc, quản lý thời gian và chấm công trực tuyến',
    category: 'hr',
    categoryLabel: 'Nhân sự & Phúc lợi',
    effectiveDate: '01/01/2026',
    authority: 'Khối Nhân sự',
    summary:
      'Quy định khung giờ làm việc linh hoạt, chế độ làm việc từ xa (hybrid work), quy trình đăng ký nghỉ phép và thủ tục xin phê duyệt vắng mặt.',
    pages: 24,
    samplePrompt: 'Quy chế làm việc và chấm công quy định như thế nào về làm việc từ xa?',
  },
  {
    id: 'doc-03',
    code: 'QT-08/FIN',
    title: 'Quy trình phê duyệt chi phí, thanh toán và tạm ứng công tác phí',
    category: 'finance',
    categoryLabel: 'Tài chính & Chi phí',
    effectiveDate: '01/02/2026',
    authority: 'Phòng Tài chính - Kế toán',
    summary:
      'Hạn mức chi tiêu công tác theo cấp bậc, quy chuẩn hóa đơn chứng từ điện tử hợp lệ, và quy trình phê duyệt điện tử 2 cấp qua hệ thống.',
    pages: 18,
    samplePrompt: 'Quy trình phê duyệt chi phí và hạn mức tạm ứng công tác phí là bao nhiêu?',
  },
  {
    id: 'doc-04',
    code: 'CS-03/SEC',
    title: 'Chính sách bảo mật dữ liệu, an toàn thông tin và quyền riêng tư',
    category: 'security',
    categoryLabel: 'Bảo mật & Kỹ thuật',
    effectiveDate: '10/01/2026',
    authority: 'Bộ phận Bảo mật & Pháp chế',
    summary:
      'Nguyên tắc phân loại tài liệu mật, quy định sử dụng tài khoản Google Workspace công ty, chính sách xác thực hai lớp (2FA) và bảo vệ dữ liệu khách hàng.',
    pages: 30,
    samplePrompt: 'Chính sách bảo mật dữ liệu quy định thế nào về thông tin mật và thiết bị cá nhân?',
  },
  {
    id: 'doc-05',
    code: 'QĐ-587/TNVN',
    title: 'Quyết định 587 ban hành Quy chế chấm điểm và tiêu chuẩn chuyên môn',
    category: 'policy',
    categoryLabel: 'Quy chế điều hành',
    effectiveDate: '16/03/2026',
    authority: 'Hội đồng Điều hành',
    summary:
      'Quy định cơ cấu thang điểm đánh giá, điều kiện tham dự, phân loại thể loại chuyên môn và trách nhiệm của Hội đồng Giám khảo.',
    pages: 16,
    samplePrompt: 'Nội dung chính và điều kiện tham dự trong Quyết định 587 là gì?',
  },
  {
    id: 'doc-06',
    code: 'QC-11/HR',
    title: 'Quy chế đánh giá hiệu suất (KPI), thi đua khen thưởng và phúc lợi',
    category: 'hr',
    categoryLabel: 'Nhân sự & Phúc lợi',
    effectiveDate: '01/03/2026',
    authority: 'Ban Nhân sự & Công đoàn',
    summary:
      'Chu kỳ đánh giá hiệu suất định kỳ 6 tháng, cơ chế thưởng theo thành tích dự án vượt trội, và các gói bảo hiểm sức khỏe nâng cao cho nhân sự chính thức.',
    pages: 22,
    samplePrompt: 'Quy chế thi đua khen thưởng và đánh giá hiệu suất KPI cuối năm như thế nào?',
  },
  {
    id: 'doc-07',
    code: 'HD-02/AI',
    title: 'Hướng dẫn vận hành Trợ lý Cá nhân AI & quy chuẩn phê duyệt Safe Write',
    category: 'security',
    categoryLabel: 'Bảo mật & Kỹ thuật',
    effectiveDate: '20/02/2026',
    authority: 'Nhóm Kỹ thuật AI',
    summary:
      'Quy chuẩn phân quyền Human-in-the-loop: Mọi hành động gửi email hay sửa lịch đều bắt buộc tạo Approval Token và chờ người dùng xác nhận rõ ràng.',
    pages: 14,
    samplePrompt: 'Quy chuẩn phê duyệt hành động ghi Safe Write của Trợ lý AI hoạt động thế nào?',
  },
]

export const KnowledgeWorkspace: React.FC<KnowledgeWorkspaceProps> = ({ onSelectCitation }) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [citations, setCitations] = useState<RAGCitation[]>([])
  const [answer, setAnswer] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState<string>('all')

  const handleSearch = async (queryToRun?: string) => {
    const q = queryToRun || searchQuery
    if (!q.trim() || isLoading) return

    setIsLoading(true)
    setError(null)
    setAnswer(null)

    try {
      const res = await apiClient.submitQuery(`Tìm trong tài liệu nội bộ: ${q}`)
      setAnswer(res.message)
      if (res.data?.citations && Array.isArray(res.data.citations)) {
        setCitations(res.data.citations as RAGCitation[])
      } else {
        setCitations([])
      }
    } catch (err: any) {
      setError(err?.message || 'Không thể tra cứu kho tri thức nội bộ.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleResetSearch = () => {
    setSearchQuery('')
    setAnswer(null)
    setCitations([])
  }

  const filteredDocs = useMemo(() => {
    return INTERNAL_DOCUMENTS.filter((doc) => {
      const matchesCat = selectedCategory === 'all' || doc.category === selectedCategory
      const matchesSearch =
        !searchQuery.trim() ||
        doc.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        doc.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
        doc.summary.toLowerCase().includes(searchQuery.toLowerCase())
      return matchesCat && matchesSearch
    })
  }, [selectedCategory, searchQuery])

  const sampleQueries = [
    'Quy chế họp và báo trước 24h',
    'Quy trình phê duyệt chi phí & tạm ứng',
    'Chính sách bảo mật dữ liệu',
    'Chế độ làm việc hybrid và nghỉ phép',
    'Quy chế thi đua khen thưởng',
  ]

  const isShowingSearchResults = Boolean(answer || citations.length > 0)

  return (
    <div className="workspace-container knowledge-workspace">
      {/* Search Bar Hero Toolbar */}
      <div className="knowledge-search-hero">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSearch()
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
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="knowledge-clear-btn"
                onClick={() => setSearchQuery('')}
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
          {sampleQueries.map((query, i) => (
            <button
              key={i}
              className="sample-chip"
              onClick={() => {
                setSearchQuery(query)
                handleSearch(query)
              }}
            >
              {query}
            </button>
          ))}
        </div>
      </div>

      {/* Content Area */}
      <div className="workspace-content-scroll">
        {isLoading ? (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim text-primary" />
            <span>Đang đối chiếu cơ sở dữ liệu RAG và trích xuất dẫn chứng...</span>
          </div>
        ) : error ? (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Lỗi tra cứu</h4>
              <p>{error}</p>
            </div>
            <button className="btn-retry" onClick={() => handleSearch()}>
              Thử lại
            </button>
          </div>
        ) : isShowingSearchResults ? (
          /* SEARCH RESULTS VIEW (AI Answer + Extracted Citations) */
          <div className="knowledge-results-layout">
            <div className="knowledge-back-bar">
              <button className="btn-back-to-catalog" onClick={handleResetSearch}>
                <ArrowLeft size={14} />
                <span>Quay lại danh mục văn bản ({INTERNAL_DOCUMENTS.length} tài liệu)</span>
              </button>
            </div>

            {/* AI Summarized Answer */}
            {answer && (
              <div className="knowledge-answer-card">
                <div className="answer-header">
                  <Sparkles size={16} className="text-primary" />
                  <h4>Tổng hợp nội dung từ quy chế & tài liệu nội bộ</h4>
                </div>
                <div className="answer-body">
                  <p>{answer}</p>
                </div>
              </div>
            )}

            {/* Citations Grid */}
            <div className="knowledge-citations-section">
              <h4 className="citations-section-title">
                Dẫn chứng trích xuất từ văn bản gốc ({citations.length})
              </h4>

              {citations.length === 0 ? (
                <p className="no-citations-hint">Không có đoạn trích dẫn cụ thể nào.</p>
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
        ) : (
          /* DEFAULT VIEW: CORPORATE KNOWLEDGE CATALOG */
          <div className="knowledge-catalog-view">
            {/* Catalog Header & Category Tabs */}
            <div className="catalog-header-bar">
              <div className="catalog-title-wrap">
                <h3 className="catalog-heading">Danh mục Văn bản & Quy chế Doanh nghiệp</h3>
                <p className="catalog-subheading">
                  Tổng hợp các chính sách, quy chế và quy trình điều hành nội bộ đã được số hóa trên hệ thống RAG.
                </p>
              </div>

              {/* Category Filter Pills */}
              <div className="catalog-category-pills">
                <button
                  className={`category-pill ${selectedCategory === 'all' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('all')}
                >
                  Tất cả ({INTERNAL_DOCUMENTS.length})
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'policy' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('policy')}
                >
                  Quy chế điều hành
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'finance' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('finance')}
                >
                  Tài chính & Chi phí
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'hr' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('hr')}
                >
                  Nhân sự & Phúc lợi
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'security' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('security')}
                >
                  Bảo mật & Kỹ thuật
                </button>
              </div>
            </div>

            {/* Document Cards Grid */}
            <div className="knowledge-docs-grid">
              {filteredDocs.map((doc) => (
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
                    <button
                      className="doc-action-btn doc-action-btn--primary"
                      onClick={() => {
                        setSearchQuery(doc.samplePrompt)
                        handleSearch(doc.samplePrompt)
                      }}
                      title="Yêu cầu Trợ lý AI tra cứu văn bản này"
                    >
                      <Sparkles size={13} />
                      <span>Tra cứu quy định</span>
                    </button>

                    <button
                      className="doc-action-btn doc-action-btn--secondary"
                      onClick={() => {
                        setSearchQuery(`Tóm tắt nội dung văn bản ${doc.title}`)
                        handleSearch(`Tóm tắt nội dung văn bản ${doc.title}`)
                      }}
                      title="Tóm tắt văn bản này"
                    >
                      <Eye size={13} />
                      <span>Tóm tắt</span>
                    </button>
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
