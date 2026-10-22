import React, { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { DocumentOutlineColumn, GeneratedTableRow } from '../../../types/api'

const VERDICT: Record<string, { label: string; tone: string }> = {
  ok: { label: 'Đạt', tone: 'ok' },
  revised: { label: 'Đã tự sửa', tone: 'revised' },
  trimmed: { label: 'Có ô để trống', tone: 'trimmed' },
  skipped: { label: 'Chưa kiểm tra', tone: 'muted' },
  metadata: { label: 'Từ thông tin văn bản', tone: 'ok' },
}

const PROBLEM: Record<string, string> = {
  unsupported: 'Không có căn cứ',
  contradiction: 'Sai lệch với nguồn',
  wrong_citation: 'Dẫn sai nguồn',
  wrong_column: 'Sai cột',
  missing_row: 'Thiếu dòng',
}

interface Props {
  columns: DocumentOutlineColumn[]
  rows: GeneratedTableRow[]
  citations: { number: number; title?: string; section_title?: string; page_start?: number }[]
}

/** The generated table as it will appear in Excel, plus the per-document source check. */
export const TablePreview: React.FC<Props> = ({ columns, rows, citations }) => {
  const [open, setOpen] = useState<string | null>(null)
  const cleared = rows.reduce(
    (n, r) => n + r.cells.filter((c) => c.status === 'cleared').length,
    0
  )
  const byDocument = new Map<string, GeneratedTableRow[]>()
  rows.forEach((r) => byDocument.set(r.document_id, [...(byDocument.get(r.document_id) || []), r]))

  return (
    <>
      <p className="docgen-muted docgen-table__summary">
        {rows.length} dòng từ {byDocument.size} văn bản
        {cleared > 0 && ` · ${cleared} ô chưa xác minh được (để trống, tô vàng trong Excel)`}
      </p>
      <div className="docgen-table__wrap">
        <table className="docgen-table">
          <thead>
            <tr>
              <th>STT</th>
              {columns.map((c) => (
                <th key={c.name} title={c.description}>
                  {c.name}
                </th>
              ))}
              <th>Nguồn</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length + 2} className="docgen-muted">
                  Không trích được dữ liệu nào từ các văn bản nguồn.
                </td>
              </tr>
            )}
            {rows.map((row, i) => (
              <tr key={`${row.document_id}-${i}`}>
                <td className="docgen-table__stt">{i + 1}</td>
                {row.cells.map((cell, j) => (
                  <td
                    key={j}
                    className={`docgen-cell docgen-cell--${cell.status}`}
                    title={
                      cell.status === 'cleared'
                        ? `Chưa xác minh được: ${cell.note || 'không khớp văn bản nguồn'}`
                        : cell.status === 'meta'
                          ? 'Lấy từ thông tin văn bản'
                          : undefined
                    }
                  >
                    {cell.value || (cell.status === 'cleared' ? '—' : '')}
                  </td>
                ))}
                <td className="docgen-table__src">
                  {row.citations.map((n) => `[${n}]`).join('')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="docgen-review">
        <div className="docgen-review__head">
          <span className="docgen-step">Kiểm tra dữ liệu</span>
          <span className="docgen-muted">Mỗi ô trích từ nội dung được đối chiếu với văn bản nguồn</span>
        </div>
        <ul className="docgen-review__list">
          {[...byDocument.entries()].map(([docId, docRows]) => {
            const review = docRows[0].review
            const badge = VERDICT[review?.verdict || 'skipped'] || { label: review?.verdict, tone: 'muted' }
            const issues = review?.issues || []
            const isOpen = open === docId
            return (
              <li key={docId} className="docgen-review__item">
                <div className="docgen-review__row">
                  <button
                    className="docgen-review__toggle"
                    onClick={() => setOpen(isOpen ? null : docId)}
                    disabled={issues.length === 0}
                  >
                    {issues.length ? (
                      isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />
                    ) : (
                      <span className="docgen-review__spacer" />
                    )}
                    <span className="docgen-review__heading">{docRows[0].source_title}</span>
                  </button>
                  <span className={`docgen-badge docgen-badge--${badge.tone}`}>{badge.label}</span>
                  {review?.rounds && review.rounds > 1 ? (
                    <span className="docgen-muted">{review.rounds} lượt</span>
                  ) : null}
                </div>
                {isOpen && (
                  <div className="docgen-review__details">
                    {issues.map((issue, k) => (
                      <div key={k} className="docgen-issue">
                        <span className="docgen-issue__type">
                          {PROBLEM[issue.problem] || issue.problem}
                          {issue.cell ? ` · ô ${issue.cell}` : ''}
                        </span>
                        {issue.explanation && (
                          <span className="docgen-issue__why">{issue.explanation}</span>
                        )}
                      </div>
                    ))}
                    {(review?.cleared || []).map((c, k) => (
                      <div key={`c${k}`} className="docgen-issue docgen-issue--removed">
                        <span className="docgen-issue__type">Đã để trống ô {c.cell}</span>
                        <q className="docgen-issue__sentence">{c.value}</q>
                      </div>
                    ))}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </div>

      {citations.length > 0 && (
        <div className="docgen-sources docgen-table__refs">
          <div className="docgen-label">Nguồn</div>
          <ul>
            {citations.map((c) => (
              <li key={c.number}>
                [{c.number}] {c.title}
                {c.section_title ? `, mục ${c.section_title}` : ''}
                {c.page_start ? `, tr. ${c.page_start}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
