import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Circle, ExternalLink, ListChecks, Plus, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { TaskItem } from '../../types/api'
import { useToast } from '../ui/toastContext'
import { Hint } from '../ui/Hint'
import { QuotedText } from '../ui/QuotedText'
import './Workspaces.css'
import './TasksWorkspace.css'

type Group = 'overdue' | 'today' | 'upcoming' | 'nodate'
type Filter = 'all' | Group

const GROUP_LABELS: Record<Group, string> = {
  overdue: 'Quá hạn',
  today: 'Hôm nay',
  upcoming: 'Sắp tới',
  nodate: 'Không hạn',
}
const GROUP_ORDER: Group[] = ['overdue', 'today', 'upcoming', 'nodate']

interface PendingChange {
  approvalId: string
  description: string
}

function localToday(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function groupOf(task: TaskItem, today: string): Group {
  if (!task.due) return 'nodate'
  if (task.due < today) return 'overdue'
  return task.due === today ? 'today' : 'upcoming'
}

function formatDue(due: string): string {
  const [y, m, d] = due.split('-')
  return `${d}/${m}/${y}`
}

/**
 * Google Tasks at a glance: open tasks grouped by due date, a quick-add form and a
 * tick to complete. Adding or completing never writes directly — the server returns an
 * approval request, shown here as a card, and only "Duyệt" makes the change.
 */
export const TasksWorkspace: React.FC = () => {
  const { showToast } = useToast()
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [pending, setPending] = useState<PendingChange | null>(null)
  const [busy, setBusy] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let active = true
    apiClient
      .listTasks()
      .then((items) => {
        if (!active) return
        setTasks(items)
        setError(null)
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : 'Không tải được Google Tasks.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [reloadKey])

  const reload = useCallback(() => {
    setLoading(true)
    setReloadKey((key) => key + 1)
  }, [])

  const today = localToday()
  const grouped = useMemo(() => {
    const groups: Record<Group, TaskItem[]> = { overdue: [], today: [], upcoming: [], nodate: [] }
    for (const task of tasks) groups[groupOf(task, today)].push(task)
    for (const group of GROUP_ORDER) {
      groups[group].sort((a, b) => (a.due || '').localeCompare(b.due || '') || a.title.localeCompare(b.title))
    }
    return groups
  }, [tasks, today])

  const propose = async (body: Parameters<typeof apiClient.proposeTask>[0]) => {
    setBusy(true)
    try {
      const response = await apiClient.proposeTask(body)
      setPending({ approvalId: response.approval_id, description: response.description })
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không tạo được yêu cầu.', 'error')
    } finally {
      setBusy(false)
    }
  }

  const handleAdd = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!title.trim() || pending) return
    await propose({ action: 'create', title: title.trim(), due: due || null })
  }

  const handleApprove = async () => {
    if (!pending) return
    setBusy(true)
    try {
      const result = await apiClient.approveAction(pending.approvalId, undefined, true)
      if (result.executed) {
        showToast('Đã cập nhật Google Tasks.', 'success')
        setTitle('')
        setDue('')
        reload()
      } else {
        showToast(result.error || 'Không thực hiện được thay đổi.', 'error')
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không duyệt được.', 'error')
    } finally {
      setPending(null)
      setBusy(false)
    }
  }

  const handleCancel = async () => {
    if (!pending) return
    const approvalId = pending.approvalId
    setPending(null)
    try {
      await apiClient.denyAction(approvalId, 'cancelled from tasks workspace', 'cancelled')
    } catch {
      // The request expires on its own; nothing was written.
    }
  }

  const visibleGroups = filter === 'all' ? GROUP_ORDER : [filter]
  const visibleCount = visibleGroups.reduce((sum, group) => sum + grouped[group].length, 0)

  return (
    <div className="workspace-container tasks-workspace">
      <div className="workspace-toolbar">
        <div className="toolbar-left">
          <div className="toolbar-filter-group">
            {(['all', ...GROUP_ORDER] as Filter[]).map((value) => (
              <button
                key={value}
                className={`toolbar-filter-btn ${filter === value ? 'toolbar-filter-btn--active' : ''}`}
                onClick={() => setFilter(value)}
              >
                {value === 'all' ? 'Tất cả' : GROUP_LABELS[value]}
                {value !== 'all' && grouped[value].length > 0 && (
                  <span className="tasks-count">{grouped[value].length}</span>
                )}
              </button>
            ))}
          </div>
        </div>
        <div className="toolbar-right">
          <Hint label="Làm mới danh sách việc">
            <button className="toolbar-btn" onClick={reload} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'spin-anim' : ''} />
              <span>Làm mới</span>
            </button>
          </Hint>
          <a
            href="https://tasks.google.com"
            target="_blank"
            rel="noreferrer"
            className="toolbar-btn toolbar-btn--link"
          >
            <span>Mở Google Tasks</span>
            <ExternalLink size={13} />
          </a>
        </div>
      </div>

      <div className="workspace-content-scroll">
        <form className="tasks-add" onSubmit={handleAdd}>
          <Plus size={16} className="tasks-add-icon" />
          <input
            className="tasks-add-title"
            value={title}
            maxLength={1024}
            placeholder="Thêm việc mới, ví dụ: Gửi báo cáo tuần"
            onChange={(event) => setTitle(event.target.value)}
            disabled={Boolean(pending)}
          />
          <input
            type="date"
            className="tasks-add-due"
            value={due}
            min={today}
            onChange={(event) => setDue(event.target.value)}
            aria-label="Hạn"
            disabled={Boolean(pending)}
          />
          <button type="submit" className="tasks-add-btn" disabled={!title.trim() || busy || Boolean(pending)}>
            Thêm
          </button>
        </form>

        {pending && (
          <div className="tasks-approval" role="alertdialog" aria-label="Xác nhận thay đổi">
            <ShieldCheck size={18} className="tasks-approval-icon" />
            <span className="tasks-approval-text">
              <QuotedText text={pending.description} />
            </span>
            <button className="tasks-approval-btn tasks-approval-btn--ok" onClick={handleApprove} disabled={busy}>
              Duyệt
            </button>
            <button className="tasks-approval-btn" onClick={handleCancel} disabled={busy} aria-label="Hủy">
              <X size={14} />
            </button>
          </div>
        )}

        {loading && tasks.length === 0 ? (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim" />
            <span>Đang tải Google Tasks…</span>
          </div>
        ) : error ? (
          <div className="workspace-empty-state">
            <div className="empty-icon-wrap">
              <ListChecks size={24} />
            </div>
            <h3>Không tải được Google Tasks</h3>
            <p>{error}</p>
          </div>
        ) : visibleCount === 0 ? (
          <div className="workspace-empty-state">
            <div className="empty-icon-wrap">
              <CheckCircle2 size={24} />
            </div>
            <h3>{filter === 'all' ? 'Không còn việc nào đang mở' : `Không có việc “${GROUP_LABELS[filter as Group]}”`}</h3>
            <p>Thêm việc ở ô phía trên, hoặc nói với trợ lý “thêm việc: … hạn thứ 6”.</p>
          </div>
        ) : (
          visibleGroups.map((group) =>
            grouped[group].length === 0 ? null : (
              <section key={group} className="tasks-group">
                <h4 className={`tasks-group-title tasks-group-title--${group}`}>
                  {GROUP_LABELS[group]} <span>{grouped[group].length}</span>
                </h4>
                <ul className="tasks-list">
                  {grouped[group].map((task) => (
                    <li key={task.id} className="tasks-item">
                      <Hint label="Đánh dấu xong">
                        <button
                          className="tasks-check"
                          aria-label={`Đánh dấu xong: ${task.title}`}
                          onClick={() => propose({ action: 'complete', task_id: task.id })}
                          disabled={busy || Boolean(pending)}
                        >
                          <Circle size={18} />
                        </button>
                      </Hint>
                      <div className="tasks-item-body">
                        <span className="tasks-item-title">{task.title || '(không tiêu đề)'}</span>
                        {task.notes && <span className="tasks-item-notes">{task.notes}</span>}
                      </div>
                      {task.due && (
                        <span className={`tasks-due tasks-due--${group}`}>{formatDue(task.due)}</span>
                      )}
                      {task.web_link && (
                        <a
                          href={task.web_link}
                          target="_blank"
                          rel="noreferrer"
                          className="tasks-open"
                          aria-label="Mở trong Google Tasks"
                        >
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )
          )
        )}
      </div>
    </div>
  )
}
