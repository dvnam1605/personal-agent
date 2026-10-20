import React, { useCallback, useEffect, useState } from 'react'
import {
  FilePlus2,
  FileText,
  Download,
  RefreshCw,
  Plus,
  Trash2,
  ArrowUp,
  ArrowDown,
  CheckCircle2,
  AlertCircle,
  Sparkles,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type {
  DocumentOutlineSection,
  GeneratedDocumentSummary,
  GeneratedDocumentView,
} from '../../types/api'
import { MarkdownContent } from '../ui/MarkdownContent'
import { DocumentReviewPanel } from './DocumentReviewPanel'
import './Workspaces.css'
import './DocumentsWorkspace.css'

const EXAMPLES = [
  'Viết báo cáo tổng hợp về các quyết định khen thưởng năm 2026',
  'Soạn tờ trình xin kinh phí tổ chức tập huấn ứng dụng AI',
  'Viết tóm tắt Quy chế làm việc của Đài ban hành năm 2026',
  'Lập kế hoạch triển khai chế độ báo cáo định kỳ theo Quy chế làm việc',
]

const STATUS_LABEL: Record<string, string> = {
  outline: 'Chờ duyệt dàn ý',
  writing: 'Đang viết',
  done: 'Hoàn thành',
  failed: 'Lỗi',
  cancelled: 'Đã huỷ',
}

export const DocumentsWorkspace: React.FC = () => {
  const [request, setRequest] = useState('')
  const [history, setHistory] = useState<GeneratedDocumentSummary[]>([])
  const [current, setCurrent] = useState<GeneratedDocumentView | null>(null)
  const [title, setTitle] = useState('')
  const [sections, setSections] = useState<DocumentOutlineSection[]>([])
  const [busy, setBusy] = useState<'outline' | 'writing' | 'loading' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rewritingIndex, setRewritingIndex] = useState<number | null>(null)

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await apiClient.listGeneratedDocuments())
    } catch {
      setHistory([])
    }
  }, [])

  useEffect(() => {
    refreshHistory()
  }, [refreshHistory])

  const show = (doc: GeneratedDocumentView) => {
    setCurrent(doc)
    setTitle(doc.outline?.title || doc.title)
    setSections(doc.outline?.sections ? doc.outline.sections.map((s) => ({ ...s })) : [])
  }

  const createOutline = async () => {
    if (request.trim().length < 3) return
    setBusy('outline')
    setError(null)
    try {
      show(await apiClient.createDocumentOutline(request.trim()))
      refreshHistory()
    } catch (err: any) {
      setError(err?.message || 'Không lập được dàn ý.')
    } finally {
      setBusy(null)
    }
  }

  const openDocument = async (id: string) => {
    setBusy('loading')
    setError(null)
    try {
      show(await apiClient.getGeneratedDocument(id))
    } catch (err: any) {
      setError(err?.message || 'Không mở được tài liệu.')
    } finally {
      setBusy(null)
    }
  }

  const approve = async () => {
    if (!current) return
    const cleaned = sections
      .map((s) => ({ heading: s.heading.trim(), purpose: s.purpose.trim() }))
      .filter((s) => s.heading)
    if (cleaned.length === 0) {
      setError('Dàn ý cần ít nhất một mục.')
      return
    }
    setBusy('writing')
    setError(null)
    try {
      show(await apiClient.approveDocument(current.id, { title: title.trim(), sections: cleaned }))
      refreshHistory()
    } catch (err: any) {
      setError(err?.message || 'Không viết được tài liệu.')
    } finally {
      setBusy(null)
    }
  }

  const rewriteSection = async (index: number, feedback: string) => {
    if (!current) return
    setRewritingIndex(index)
    setError(null)
    try {
      show(await apiClient.rewriteDocumentSection(current.id, index, feedback))
    } catch (err: any) {
      setError(err?.message || 'Không viết lại được mục này.')
    } finally {
      setRewritingIndex(null)
    }
  }

  const updateSection = (idx: number, patch: Partial<DocumentOutlineSection>) =>
    setSections((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)))

  const moveSection = (idx: number, delta: number) =>
    setSections((prev) => {
      const next = [...prev]
      const target = idx + delta
      if (target < 0 || target >= next.length) return prev
      ;[next[idx], next[target]] = [next[target], next[idx]]
      return next
    })

  const omitted = current?.sections?.filter((s) => s.status === 'missing').map((s) => s.heading) ?? []
  const isOutline = current && (current.status === 'outline' || current.status === 'failed')

  return (
    <div className="workspace-container documents-workspace">
      <aside className="docgen-sidebar">
        <div className="docgen-request">
          <label htmlFor="docgen-request" className="docgen-label">
            Yêu cầu tài liệu
          </label>
          <textarea
            id="docgen-request"
            className="docgen-textarea"
            placeholder="Ví dụ: Viết báo cáo tổng hợp về các quyết định khen thưởng năm 2026"
            value={request}
            rows={4}
            onChange={(e) => setRequest(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) createOutline()
            }}
          />
          <button
            className="docgen-primary"
            onClick={createOutline}
            disabled={busy !== null || request.trim().length < 3}
          >
            {busy === 'outline' ? <RefreshCw size={14} className="spin-anim" /> : <Sparkles size={14} />}
            <span>{busy === 'outline' ? 'Đang lập dàn ý…' : 'Lập dàn ý'}</span>
          </button>
          <div className="docgen-examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} className="docgen-example" onClick={() => setRequest(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>

        <div className="docgen-history">
          <div className="docgen-history__head">
            <span>Tài liệu đã tạo</span>
            <button className="docgen-icon-btn" onClick={refreshHistory} title="Làm mới">
              <RefreshCw size={13} />
            </button>
          </div>
          {history.length === 0 ? (
            <p className="docgen-muted">Chưa có tài liệu nào.</p>
          ) : (
            history.map((item) => (
              <button
                key={item.id}
                className={`docgen-history__item ${current?.id === item.id ? 'is-active' : ''}`}
                onClick={() => openDocument(item.id)}
              >
                <FileText size={14} />
                <span className="docgen-history__title">{item.title}</span>
                <span className={`docgen-status docgen-status--${item.status}`}>
                  {STATUS_LABEL[item.status] || item.status}
                </span>
              </button>
            ))
          )}
        </div>
      </aside>

      <section className="docgen-main workspace-content-scroll">
        {error && (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Có lỗi</h4>
              <p>{error}</p>
            </div>
          </div>
        )}

        {busy === 'writing' && (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim text-primary" />
            <span>Đang đọc tài liệu nguồn và viết từng mục… (thường mất 30–90 giây)</span>
          </div>
        )}

        {!current && busy === null && (
          <div className="workspace-empty-state">
            <div className="empty-icon-wrap">
              <FilePlus2 size={32} />
            </div>
            <h3>Tạo tài liệu từ kho văn bản</h3>
            <p>
              Nhập yêu cầu (báo cáo, tờ trình, biên bản, kế hoạch, công văn, tóm tắt…). Trợ lý chọn
              tài liệu nguồn, đề xuất dàn ý để bạn chỉnh sửa, rồi viết tài liệu có trích dẫn và xuất
              file Word.
            </p>
          </div>
        )}

        {current && isOutline && busy !== 'writing' && (
          <div className="docgen-outline">
            <div className="docgen-step">Bước 1 · Duyệt dàn ý</div>
            <label className="docgen-label" htmlFor="docgen-title">
              Tiêu đề
            </label>
            <input
              id="docgen-title"
              className="docgen-input docgen-input--title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />

            <div className="docgen-label">Các mục</div>
            <ol className="docgen-sections">
              {sections.map((s, idx) => (
                <li key={idx} className="docgen-section">
                  <div className="docgen-section__fields">
                    <input
                      className="docgen-input"
                      value={s.heading}
                      placeholder="Tên mục"
                      onChange={(e) => updateSection(idx, { heading: e.target.value })}
                    />
                    <input
                      className="docgen-input docgen-input--muted"
                      value={s.purpose}
                      placeholder="Mục này cần nói gì"
                      onChange={(e) => updateSection(idx, { purpose: e.target.value })}
                    />
                  </div>
                  <div className="docgen-section__actions">
                    <button className="docgen-icon-btn" onClick={() => moveSection(idx, -1)} title="Lên">
                      <ArrowUp size={13} />
                    </button>
                    <button className="docgen-icon-btn" onClick={() => moveSection(idx, 1)} title="Xuống">
                      <ArrowDown size={13} />
                    </button>
                    <button
                      className="docgen-icon-btn docgen-icon-btn--danger"
                      onClick={() => setSections((prev) => prev.filter((_, i) => i !== idx))}
                      title="Xoá mục"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </li>
              ))}
            </ol>
            {sections.length < 8 && (
              <button
                className="docgen-secondary"
                onClick={() => setSections((prev) => [...prev, { heading: '', purpose: '' }])}
              >
                <Plus size={14} /> Thêm mục
              </button>
            )}

            {current.outline?.source_titles && current.outline.source_titles.length > 0 && (
              <div className="docgen-sources">
                <div className="docgen-label">Tài liệu nguồn ({current.outline.source_titles.length})</div>
                <ul>
                  {current.outline.source_titles.map((t, i) => (
                    <li key={i}>{t}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="docgen-actions">
              <button className="docgen-primary" onClick={approve} disabled={busy !== null}>
                <CheckCircle2 size={14} />
                <span>Duyệt & viết tài liệu</span>
              </button>
            </div>
          </div>
        )}

        {current && current.status === 'done' && busy !== 'writing' && (
          <div className="docgen-result">
            <div className="docgen-result__bar">
              <div className="docgen-step">Bước 2 · Tài liệu</div>
              {current.has_docx && (
                <button
                  className="docgen-primary"
                  onClick={() =>
                    apiClient
                      .downloadGeneratedDocument(current.id, current.title)
                      .catch((err) => setError(err?.message || 'Không tải được file.'))
                  }
                >
                  <Download size={14} />
                  <span>Tải .docx</span>
                </button>
              )}
            </div>
            {omitted.length > 0 && (
              <p className="docgen-note">
                Đã lược khỏi tài liệu vì chưa đủ thông tin trong nguồn: {omitted.join('; ')}
              </p>
            )}
            {current.sections?.length > 0 && (
              <DocumentReviewPanel
                sections={current.sections}
                rewritingIndex={rewritingIndex}
                disabled={busy !== null || rewritingIndex !== null}
                onRewrite={rewriteSection}
              />
            )}
            <article className="docgen-paper">
              <MarkdownContent content={current.markdown || ''} />
            </article>
          </div>
        )}
      </section>
    </div>
  )
}
