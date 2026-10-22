import React from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import type { DocumentOutlineColumn } from '../../../types/api'

const COLUMN_SOURCES: { value: string; label: string }[] = [
  { value: 'content', label: 'Trích từ nội dung' },
  { value: 'doc_number', label: 'Số hiệu văn bản' },
  { value: 'doc_date', label: 'Ngày ban hành' },
  { value: 'title', label: 'Tên / trích yếu' },
  { value: 'issuer', label: 'Cơ quan ban hành' },
  { value: 'doc_type', label: 'Loại văn bản' },
  { value: 'signer', label: 'Người ký' },
]

const MAX_COLUMNS = 12

interface Props {
  columns: DocumentOutlineColumn[]
  onColumns: (next: DocumentOutlineColumn[]) => void
  rowUnit: string
  onRowUnit: (value: string) => void
  multiRows: boolean
  onMultiRows: (value: boolean) => void
}

/** Edit the columns of a table outline and what one row stands for. */
export const TableOutlineEditor: React.FC<Props> = ({
  columns,
  onColumns,
  rowUnit,
  onRowUnit,
  multiRows,
  onMultiRows,
}) => {
  const update = (idx: number, patch: Partial<DocumentOutlineColumn>) =>
    onColumns(columns.map((c, i) => (i === idx ? { ...c, ...patch } : c)))

  const move = (idx: number, delta: number) => {
    const target = idx + delta
    if (target < 0 || target >= columns.length) return
    const next = [...columns]
    ;[next[idx], next[target]] = [next[target], next[idx]]
    onColumns(next)
  }

  return (
    <>
      <label className="docgen-label" htmlFor="docgen-row-unit">
        Mỗi dòng là
      </label>
      <div className="docgen-rowunit">
        <input
          id="docgen-row-unit"
          className="docgen-input"
          value={rowUnit}
          placeholder="ví dụ: văn bản, tập thể được khen thưởng"
          onChange={(e) => onRowUnit(e.target.value)}
        />
        <label className="docgen-check">
          <input
            type="checkbox"
            checked={multiRows}
            onChange={(e) => onMultiRows(e.target.checked)}
          />
          Một văn bản có thể có nhiều dòng
        </label>
      </div>

      <div className="docgen-label">Các cột (STT và Nguồn được thêm tự động)</div>
      <ol className="docgen-sections">
        {columns.map((c, idx) => (
          <li key={idx} className="docgen-section">
            <div className="docgen-section__fields">
              <div className="docgen-column__top">
                <input
                  className="docgen-input"
                  value={c.name}
                  placeholder="Tên cột"
                  onChange={(e) => update(idx, { name: e.target.value })}
                />
                <select
                  className="docgen-input docgen-select"
                  value={c.source}
                  onChange={(e) => update(idx, { source: e.target.value })}
                  title="Lấy dữ liệu cột từ đâu"
                >
                  {COLUMN_SOURCES.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
              <input
                className="docgen-input docgen-input--muted"
                value={c.description}
                placeholder="Cột này ghi gì"
                onChange={(e) => update(idx, { description: e.target.value })}
              />
            </div>
            <div className="docgen-section__actions">
              <button className="docgen-icon-btn" onClick={() => move(idx, -1)} title="Sang trái">
                <ArrowUp size={13} />
              </button>
              <button className="docgen-icon-btn" onClick={() => move(idx, 1)} title="Sang phải">
                <ArrowDown size={13} />
              </button>
              <button
                className="docgen-icon-btn docgen-icon-btn--danger"
                onClick={() => onColumns(columns.filter((_, i) => i !== idx))}
                title="Xoá cột"
              >
                <Trash2 size={13} />
              </button>
            </div>
          </li>
        ))}
      </ol>
      {columns.length < MAX_COLUMNS && (
        <button
          className="docgen-secondary"
          onClick={() => onColumns([...columns, { name: '', description: '', source: 'content' }])}
        >
          <Plus size={14} /> Thêm cột
        </button>
      )}
    </>
  )
}
