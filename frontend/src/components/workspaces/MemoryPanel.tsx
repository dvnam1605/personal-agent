import React, { useCallback, useEffect, useState } from 'react'
import { Brain, Check, Pencil, Plus, Trash2, X } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { MemoryView } from '../../types/api'
import { useToast } from '../ui/toastContext'
import { Hint } from '../ui/Hint'
import './MemoryPanel.css'

const CATEGORY_LABELS: Record<string, string> = {
  signature: 'Chữ ký',
  tone: 'Giọng văn',
  addressing: 'Xưng hô',
  language: 'Ngôn ngữ',
  working_hours: 'Giờ làm việc',
}

/**
 * "Trợ lý nhớ gì về tôi": every preference and fact the assistant keeps, with add, edit
 * and delete. The same items are what chat commands ("nhớ rằng …", "quên …") change.
 */
export const MemoryPanel: React.FC = () => {
  const { showToast } = useToast()
  const [items, setItems] = useState<MemoryView[]>([])
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')

  const load = useCallback(async () => {
    try {
      setItems(await apiClient.listMemories())
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không tải được bộ nhớ.', 'error')
    }
  }, [showToast])

  useEffect(() => {
    let active = true
    apiClient
      .listMemories()
      .then((list) => {
        if (active) setItems(list)
      })
      .catch((err: unknown) => {
        if (active) showToast(err instanceof Error ? err.message : 'Không tải được bộ nhớ.', 'error')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [showToast])

  const handleAdd = async (event: React.FormEvent) => {
    event.preventDefault()
    const content = draft.trim()
    if (!content) return
    try {
      await apiClient.createMemory(content)
      setDraft('')
      await load()
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không lưu được.', 'error')
    }
  }

  const handleSave = async (id: string) => {
    const content = editText.trim()
    if (!content) return
    try {
      const updated = await apiClient.updateMemory(id, content)
      setItems((current) => current.map((item) => (item.id === id ? updated : item)))
      setEditingId(null)
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không sửa được.', 'error')
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await apiClient.deleteMemory(id)
      setItems((current) => current.filter((item) => item.id !== id))
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không xóa được.', 'error')
    }
  }

  return (
    <div className="settings-card">
      <div className="settings-card-header">
        <div className="card-header-titles">
          <h3>Trợ lý nhớ gì về tôi</h3>
          <p>
            Sở thích và thông tin bạn nhờ trợ lý ghi nhớ, dùng khi soạn email và trả lời. Trong chat
            bạn cũng có thể nói “nhớ rằng …” hoặc “quên …”.
          </p>
        </div>
        <Brain size={18} className="text-dim" />
      </div>

      <form className="memory-add" onSubmit={handleAdd}>
        <input
          className="settings-input memory-input"
          value={draft}
          maxLength={500}
          placeholder="Ví dụ: chữ ký của tôi là Nam — Phòng Kỹ thuật"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="memory-button memory-button--primary" disabled={!draft.trim()}>
          <Plus size={14} />
          <span>Ghi nhớ</span>
        </button>
      </form>

      {loading ? (
        <p className="memory-empty">Đang tải…</p>
      ) : items.length === 0 ? (
        <p className="memory-empty">Trợ lý chưa ghi nhớ điều gì về bạn.</p>
      ) : (
        <ul className="memory-list">
          {items.map((item) => (
            <li key={item.id} className="memory-item">
              <span className="memory-tag">
                {item.category
                  ? (CATEGORY_LABELS[item.category] ?? item.category)
                  : item.memory_type === 'preference'
                    ? 'Sở thích'
                    : 'Thông tin'}
              </span>
              {editingId === item.id ? (
                <>
                  <input
                    className="settings-input memory-input"
                    value={editText}
                    maxLength={500}
                    autoFocus
                    onChange={(event) => setEditText(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void handleSave(item.id)
                      if (event.key === 'Escape') setEditingId(null)
                    }}
                  />
                  <Hint label="Lưu">
                    <button className="memory-button" onClick={() => handleSave(item.id)} aria-label="Lưu">
                      <Check size={14} />
                    </button>
                  </Hint>
                  <Hint label="Hủy">
                    <button className="memory-button" onClick={() => setEditingId(null)} aria-label="Hủy">
                      <X size={14} />
                    </button>
                  </Hint>
                </>
              ) : (
                <>
                  <span className="memory-content">{item.content}</span>
                  <Hint label="Sửa">
                    <button
                      className="memory-button"
                      aria-label="Sửa"
                      onClick={() => {
                        setEditingId(item.id)
                        setEditText(item.content)
                      }}
                    >
                      <Pencil size={14} />
                    </button>
                  </Hint>
                  <Hint label="Xóa">
                    <button
                      className="memory-button memory-button--danger"
                      aria-label="Xóa"
                      onClick={() => handleDelete(item.id)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </Hint>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
