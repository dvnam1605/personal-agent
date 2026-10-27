import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, CalendarClock, CheckCheck, Sunrise } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { NotificationItem } from '../../types/api'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/shadcn/popover'
import { MarkdownContent } from '../ui/MarkdownContent'
import { Hint } from '../ui/Hint'
import { useToast } from '../ui/toastContext'
import './NotificationBell.css'

const POLL_INTERVAL_MS = 60000

interface NotificationBellProps {
  enabled: boolean
}

/**
 * Notification centre for what the assistant prepared on its own (morning briefing,
 * meeting dossiers). Polls the backend; a notification that appears between two polls
 * is announced with a toast, and opening one marks it read.
 */
export const NotificationBell: React.FC<NotificationBellProps> = ({ enabled }) => {
  const { showToast } = useToast()
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const seenIds = useRef<Set<string> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const response = await apiClient.getNotifications()
      setItems(response.items)
      setUnreadCount(response.unread_count)
      const previous = seenIds.current
      if (previous !== null) {
        response.items
          .filter((item) => !item.read_at && !previous.has(item.id))
          .forEach((item) => showToast(item.title, 'info'))
      }
      seenIds.current = new Set(response.items.map((item) => item.id))
    } catch {
      // Keep the last list; the next poll retries.
    }
  }, [showToast])

  useEffect(() => {
    seenIds.current = null
    if (!enabled) return
    let active = true
    const tick = () => {
      if (active) void refresh()
    }
    tick()
    const interval = setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [enabled, refresh])

  if (!enabled) return null

  const handleOpenItem = async (item: NotificationItem) => {
    setExpandedId((current) => (current === item.id ? null : item.id))
    if (item.read_at) return
    const readAt = new Date().toISOString()
    setItems((current) =>
      current.map((entry) => (entry.id === item.id ? { ...entry, read_at: readAt } : entry))
    )
    setUnreadCount((count) => Math.max(0, count - 1))
    try {
      await apiClient.markNotificationRead(item.id)
    } catch {
      void refresh()
    }
  }

  const handleMarkAll = async () => {
    try {
      await apiClient.markAllNotificationsRead()
      await refresh()
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không đánh dấu được thông báo.', 'error')
    }
  }

  return (
    <Popover>
      <Hint label="Thông báo từ trợ lý">
        <PopoverTrigger asChild>
          <button className="notification-bell" aria-label={`Thông báo, ${unreadCount} chưa đọc`}>
            <Bell size={15} />
            {unreadCount > 0 && (
              <span className="notification-bell-badge">{unreadCount > 9 ? '9+' : unreadCount}</span>
            )}
          </button>
        </PopoverTrigger>
      </Hint>
      <PopoverContent align="end" sideOffset={8} className="notification-panel">
        <div className="notification-panel-header">
          <span className="notification-panel-title">Thông báo</span>
          {unreadCount > 0 && (
            <button className="notification-mark-all" onClick={handleMarkAll}>
              <CheckCheck size={13} />
              <span>Đọc hết</span>
            </button>
          )}
        </div>
        {items.length === 0 ? (
          <p className="notification-empty">
            Chưa có thông báo. Bản tin sáng và hồ sơ họp do trợ lý tự chuẩn bị sẽ hiện ở đây.
          </p>
        ) : (
          <ul className="notification-list">
            {items.map((item) => {
              const Icon = item.kind === 'meeting_prep' ? CalendarClock : Sunrise
              const isExpanded = expandedId === item.id
              return (
                <li
                  key={item.id}
                  className={`notification-item ${item.read_at ? '' : 'notification-item--unread'}`}
                >
                  <button
                    className="notification-item-head"
                    onClick={() => handleOpenItem(item)}
                    aria-expanded={isExpanded}
                  >
                    <Icon size={14} className="notification-item-icon" />
                    <span className="notification-item-text">
                      <span className="notification-item-title">{item.title}</span>
                      <span className="notification-item-time">{formatWhen(item.created_at)}</span>
                    </span>
                  </button>
                  {isExpanded && (
                    <div className="notification-item-body">
                      <MarkdownContent content={item.body} />
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  )
}

function formatWhen(iso: string): string {
  const created = new Date(iso)
  const minutes = Math.round((Date.now() - created.getTime()) / 60000)
  if (minutes < 1) return 'Vừa xong'
  if (minutes < 60) return `${minutes} phút trước`
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} giờ trước`
  return created.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' })
}
