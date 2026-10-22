import React, { useState } from 'react'
import { ChevronDown, ChevronRight, PenLine, RefreshCw, Send, X } from 'lucide-react'
import type { GeneratedDocumentSection, SectionReview } from '../../../types/api'

const VERDICT: Record<string, { label: string; tone: string }> = {
  ok: { label: 'Đạt', tone: 'ok' },
  revised: { label: 'Đã tự sửa', tone: 'revised' },
  trimmed: { label: 'Đã lược câu sai', tone: 'trimmed' },
  skipped: { label: 'Chưa kiểm tra', tone: 'muted' },
}

const PROBLEM: Record<string, string> = {
  unsupported: 'Không có căn cứ',
  contradiction: 'Sai lệch với nguồn',
  wrong_citation: 'Dẫn sai nguồn',
  off_topic: 'Lạc đề',
}

function badgeFor(section: GeneratedDocumentSection): { label: string; tone: string } {
  if (section.status === 'missing') return { label: 'Thiếu thông tin nguồn', tone: 'muted' }
  const review: SectionReview | null | undefined = section.review
  if (!review) return { label: 'Chưa kiểm tra', tone: 'muted' }
  return VERDICT[review.verdict] || { label: review.verdict, tone: 'muted' }
}

interface Props {
  sections: GeneratedDocumentSection[]
  rewritingIndex: number | null
  disabled: boolean
  onRewrite: (index: number, feedback: string) => Promise<void>
}

/** Per-section result of the automatic source check, with "rewrite with feedback". */
export const DocumentReviewPanel: React.FC<Props> = ({
  sections,
  rewritingIndex,
  disabled,
  onRewrite,
}) => {
  const [open, setOpen] = useState<number | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [feedback, setFeedback] = useState('')

  const reviewed = sections.filter((s) => s.review && s.review.verdict !== 'skipped')
  const fixed = reviewed.filter((s) => s.review?.verdict === 'revised' || s.review?.verdict === 'trimmed')

  const submit = async (index: number) => {
    const text = feedback.trim()
    if (text.length < 3) return
    await onRewrite(index, text)
    setEditing(null)
    setFeedback('')
  }

  return (
    <div className="docgen-review">
      <div className="docgen-review__head">
        <span className="docgen-step">Kiểm tra nội dung</span>
        <span className="docgen-muted">
          {reviewed.length === 0
            ? 'Chưa có kết quả tự kiểm tra'
            : `${reviewed.length} mục đã đối chiếu với tài liệu nguồn · ${fixed.length} mục đã tự sửa`}
        </span>
      </div>
      <ul className="docgen-review__list">
        {sections.map((section, index) => {
          const badge = badgeFor(section)
          const review = section.review
          const details = (review?.issues.length || 0) + (review?.removed_sentences.length || 0)
          const isOpen = open === index
          const isRewriting = rewritingIndex === index
          return (
            <li key={`${index}-${section.heading}`} className="docgen-review__item">
              <div className="docgen-review__row">
                <button
                  className="docgen-review__toggle"
                  onClick={() => setOpen(isOpen ? null : index)}
                  disabled={details === 0}
                  title={details ? 'Xem chi tiết' : undefined}
                >
                  {details ? (
                    isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />
                  ) : (
                    <span className="docgen-review__spacer" />
                  )}
                  <span className="docgen-review__heading">{section.heading}</span>
                </button>
                <span className={`docgen-badge docgen-badge--${badge.tone}`}>{badge.label}</span>
                {review && review.rounds > 1 && (
                  <span className="docgen-muted">{review.rounds} lượt</span>
                )}
                <button
                  className="docgen-icon-btn"
                  onClick={() => {
                    setEditing(editing === index ? null : index)
                    setFeedback('')
                  }}
                  disabled={disabled}
                  title="Viết lại mục này kèm góp ý"
                >
                  {isRewriting ? <RefreshCw size={13} className="spin-anim" /> : <PenLine size={13} />}
                </button>
              </div>

              {isOpen && review && (
                <div className="docgen-review__details">
                  {review.issues.map((issue, i) => (
                    <div key={i} className="docgen-issue">
                      <span className="docgen-issue__type">{PROBLEM[issue.problem] || issue.problem}</span>
                      <q className="docgen-issue__sentence">{issue.sentence}</q>
                      {issue.explanation && (
                        <span className="docgen-issue__why">{issue.explanation}</span>
                      )}
                    </div>
                  ))}
                  {review.removed_sentences.length > 0 && (
                    <div className="docgen-issue docgen-issue--removed">
                      <span className="docgen-issue__type">Đã lược khỏi tài liệu</span>
                      {review.removed_sentences.map((s, i) => (
                        <q key={i} className="docgen-issue__sentence">
                          {s}
                        </q>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {isRewriting && (
                <p className="docgen-muted docgen-review__progress">
                  Đang viết lại và tự kiểm tra mục này… (thường 30–90 giây)
                </p>
              )}

              {editing === index && !isRewriting && (
                <div className="docgen-review__edit">
                  <textarea
                    className="docgen-textarea"
                    rows={3}
                    value={feedback}
                    autoFocus
                    placeholder="Góp ý cho mục này, ví dụ: nêu rõ số tiền thưởng của từng tập thể, viết ngắn gọn hơn, bỏ phần căn cứ pháp lý…"
                    onChange={(e) => setFeedback(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit(index)
                    }}
                  />
                  <div className="docgen-review__edit-actions">
                    <button className="docgen-secondary" onClick={() => setEditing(null)}>
                      <X size={14} /> Huỷ
                    </button>
                    <button
                      className="docgen-primary"
                      onClick={() => submit(index)}
                      disabled={disabled || feedback.trim().length < 3}
                    >
                      <Send size={14} /> Viết lại mục
                    </button>
                  </div>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
