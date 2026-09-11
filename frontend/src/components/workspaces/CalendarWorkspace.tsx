import React, { useState, useEffect, useMemo } from 'react'
import {
  Calendar as CalendarIcon,
  Clock,
  ExternalLink,
  RefreshCw,
  Plus,
  MapPin,
  Users,
  Video,
  AlertCircle,
  Search,
  Sparkles,
  Mail,
  X,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { CalendarEvent } from '../../types/api'
import './Workspaces.css'

interface CalendarWorkspaceProps {
  onScheduleMeeting: (prompt: string) => void
}

type CalendarOmnibarMode = 'schedule' | 'search' | 'summarize'

export const CalendarWorkspace: React.FC<CalendarWorkspaceProps> = ({ onScheduleMeeting }) => {
  const [filter, setFilter] = useState<'today' | 'tomorrow' | 'week'>('today')
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeMode, setActiveMode] = useState<CalendarOmnibarMode>('schedule')
  const [inputValue, setInputValue] = useState('')

  const fetchCalendar = async (scope: 'today' | 'tomorrow' | 'week') => {
    setIsLoading(true)
    setError(null)
    const query =
      scope === 'today'
        ? 'Lịch của tôi hôm nay?'
        : scope === 'tomorrow'
        ? 'Lịch của tôi ngày mai?'
        : 'Lịch của tôi trong tuần này?'

    try {
      const res = await apiClient.submitQuery(query)
      if (res.data?.events && Array.isArray(res.data.events)) {
        setEvents(res.data.events as CalendarEvent[])
      } else {
        setEvents([])
      }
    } catch (err: any) {
      setError(err?.message || 'Không thể tải lịch trình từ Google Calendar.')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchCalendar(filter)
  }, [filter])

  const formatEventTime = (timeVal?: string | { dateTime?: string; date?: string }) => {
    if (!timeVal) return ''
    const raw = typeof timeVal === 'string' ? timeVal : timeVal.dateTime || timeVal.date || ''
    if (!raw) return ''
    try {
      const date = new Date(raw)
      return date.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
    } catch {
      return raw
    }
  }

  // Real-time filter when in search mode
  const filteredEvents = useMemo(() => {
    if (!inputValue.trim() || activeMode !== 'search') return events
    const q = inputValue.toLowerCase()
    return events.filter(
      (ev) =>
        (ev.summary && ev.summary.toLowerCase().includes(q)) ||
        (ev.location && ev.location.toLowerCase().includes(q))
    )
  }, [events, inputValue, activeMode])

  const handleOmnibarSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputValue.trim()) return

    if (activeMode === 'schedule') {
      const trimmed = inputValue.trim()
      const prompt = /^(tạo|đặt|lên|book|create|schedule)/i.test(trimmed)
        ? trimmed
        : `Tạo lịch: ${trimmed}`
      onScheduleMeeting(prompt)
      setInputValue('')
    } else if (activeMode === 'search') {
      onScheduleMeeting(`Kiểm tra lịch trình hoặc tìm khung giờ rảnh: ${inputValue}`)
    } else if (activeMode === 'summarize') {
      onScheduleMeeting(`Tóm tắt lịch trình: ${inputValue}`)
      setInputValue('')
    }
  }

  const handleQuickPrompt = (prompt: string) => {
    onScheduleMeeting(prompt)
  }

  const calendarQuickChips = [
    { label: '⚡ Tóm tắt lịch hôm nay', prompt: 'Lịch trình của tôi hôm nay có những cuộc họp nào?' },
    { label: '🔍 Tìm khung giờ rảnh ngày mai', prompt: 'Tìm các khoảng thời gian trống của tôi trong ngày mai' },
    { label: '📅 Lên lịch họp 1:1 với Nam', prompt: 'Đặt lịch họp 1:1 với Nam lúc 15:00 ngày mai' },
    { label: '👥 Chuẩn bị cuộc họp tiếp theo', prompt: 'Chuẩn bị cuộc họp tiếp theo' },
  ]

  return (
    <div className="workspace-container calendar-workspace">
      {/* Workspace Header Toolbar */}
      <div className="workspace-toolbar">
        <div className="toolbar-left">
          <div className="toolbar-filter-group">
            <button
              className={`toolbar-filter-btn ${filter === 'today' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setFilter('today')}
            >
              Hôm nay
            </button>
            <button
              className={`toolbar-filter-btn ${filter === 'tomorrow' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setFilter('tomorrow')}
            >
              Ngày mai
            </button>
            <button
              className={`toolbar-filter-btn ${filter === 'week' ? 'toolbar-filter-btn--active' : ''}`}
              onClick={() => setFilter('week')}
            >
              Tuần này
            </button>
          </div>
        </div>

        <div className="toolbar-right">
          <button
            className="toolbar-btn"
            onClick={() => fetchCalendar(filter)}
            disabled={isLoading}
            title="Làm mới lịch trình"
          >
            <RefreshCw size={14} className={isLoading ? 'spin-anim' : ''} />
            <span>Làm mới</span>
          </button>
          <a
            href="https://calendar.google.com"
            target="_blank"
            rel="noreferrer"
            className="toolbar-btn toolbar-btn--link"
          >
            <span>Mở Google Calendar</span>
            <ExternalLink size={13} />
          </a>
        </div>
      </div>

      {/* Intelligent Unified Calendar Omnibar */}
      <div className="calendar-omnibar-card">
        {/* Mode Selector Tabs */}
        <div className="omnibar-mode-selector">
          <button
            className={`omnibar-mode-tab ${activeMode === 'schedule' ? 'omnibar-mode-tab--active' : ''}`}
            onClick={() => setActiveMode('schedule')}
          >
            <Plus size={13} />
            <span>Lên lịch họp mới</span>
          </button>
          <button
            className={`omnibar-mode-tab ${activeMode === 'search' ? 'omnibar-mode-tab--active' : ''}`}
            onClick={() => setActiveMode('search')}
          >
            <Search size={13} />
            <span>Tìm sự kiện & Giờ rảnh</span>
          </button>
          <button
            className={`omnibar-mode-tab ${activeMode === 'summarize' ? 'omnibar-mode-tab--active' : ''}`}
            onClick={() => setActiveMode('summarize')}
          >
            <Sparkles size={13} />
            <span>Tóm tắt lịch trình</span>
          </button>
        </div>

        {/* Omnibar Input Form */}
        <form onSubmit={handleOmnibarSubmit} className="omnibar-form">
          <div className="omnibar-input-container">
            {activeMode === 'schedule' && <Plus size={16} className="omnibar-input-icon text-primary" />}
            {activeMode === 'search' && <Search size={16} className="omnibar-input-icon text-muted" />}
            {activeMode === 'summarize' && <Sparkles size={16} className="omnibar-input-icon text-accent" />}

            <input
              type="text"
              className="omnibar-input"
              placeholder={
                activeMode === 'schedule'
                  ? 'Nhập yêu cầu đặt lịch (ví dụ: Họp dự án với team lúc 14:00 ngày mai tại phòng họp A)...'
                  : activeMode === 'search'
                  ? 'Tìm kiếm sự kiện theo tên hoặc tra cứu khoảng thời gian trống...'
                  : 'Yêu cầu tóm tắt (ví dụ: Tóm tắt toàn bộ lịch trình và thời gian di chuyển hôm nay)...'
              }
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
            />

            {inputValue.trim() && (
              <button
                type="button"
                className="omnibar-clear-btn"
                onClick={() => setInputValue('')}
                title="Xóa nội dung"
              >
                <X size={14} />
              </button>
            )}
          </div>

          <button
            type="submit"
            className="omnibar-submit-btn"
            disabled={!inputValue.trim() && activeMode !== 'summarize'}
          >
            {activeMode === 'schedule' && (
              <>
                <Plus size={13} />
                <span>Đặt lịch họp</span>
              </>
            )}
            {activeMode === 'search' && (
              <>
                <Search size={13} />
                <span>Tìm với AI</span>
              </>
            )}
            {activeMode === 'summarize' && (
              <>
                <Sparkles size={13} />
                <span>Tóm tắt ngay</span>
              </>
            )}
          </button>
        </form>

        {/* Quick Action Suggestion Chips */}
        <div className="omnibar-chips-row">
          <span className="omnibar-chips-label">Gợi ý tác vụ:</span>
          {calendarQuickChips.map((chip, idx) => (
            <button
              key={idx}
              type="button"
              className="omnibar-suggestion-chip"
              onClick={() => handleQuickPrompt(chip.prompt)}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content Area */}
      <div className="workspace-content-scroll">
        {isLoading ? (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim text-primary" />
            <span>Đang đồng bộ dữ liệu từ Google Calendar...</span>
          </div>
        ) : error ? (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Không thể đồng bộ lịch</h4>
              <p>{error}</p>
            </div>
            <button className="btn-retry" onClick={() => fetchCalendar(filter)}>
              Thử lại
            </button>
          </div>
        ) : filteredEvents.length === 0 ? (
          <div className="workspace-empty-state">
            <div className="empty-icon-wrap">
              <CalendarIcon size={32} />
            </div>
            <h3>Không có sự kiện nào</h3>
            <p>
              {inputValue
                ? `Không tìm thấy cuộc họp nào khớp với từ khóa "${inputValue}".`
                : filter === 'today'
                ? 'Bạn không có lịch trình nào trong ngày hôm nay.'
                : filter === 'tomorrow'
                ? 'Ngày mai không có lịch trình nào đã lên trước.'
                : 'Không có sự kiện nào trong khoảng thời gian này.'}
            </p>
            {inputValue && (
              <button
                className="btn-ask-ai-search"
                onClick={() => onScheduleMeeting(`Kiểm tra lịch sự kiện: ${inputValue}`)}
              >
                <Sparkles size={14} />
                <span>Nhờ Trợ lý tìm kiếm: "{inputValue}"</span>
              </button>
            )}
          </div>
        ) : (
          <div className="calendar-events-grid">
            {filteredEvents.map((evt, idx) => (
              <div key={idx} className="calendar-agenda-card">
                <div className="agenda-card-time">
                  <Clock size={14} />
                  <span>
                    {formatEventTime(evt.start)} – {formatEventTime(evt.end)}
                  </span>
                </div>

                <div className="agenda-card-body">
                  <h4 className="agenda-card-title">{evt.summary || 'Cuộc họp không có tiêu đề'}</h4>

                  {evt.location && (
                    <div className="agenda-card-detail">
                      <MapPin size={13} />
                      <span>{evt.location}</span>
                    </div>
                  )}

                  {(evt as any).attendees && (evt as any).attendees.length > 0 && (
                    <div className="agenda-card-detail">
                      <Users size={13} />
                      <span>
                        {Array.isArray((evt as any).attendees)
                          ? (evt as any).attendees.join(', ')
                          : (evt as any).attendees}
                      </span>
                    </div>
                  )}

                  {(evt as any).hangout_link && (
                    <div className="agenda-card-meet">
                      <a
                        href={(evt as any).hangout_link}
                        target="_blank"
                        rel="noreferrer"
                        className="btn-join-meet"
                      >
                        <Video size={13} />
                        <span>Tham gia Google Meet</span>
                      </a>
                    </div>
                  )}

                  {/* Smart Actions on each Calendar Card */}
                  <div className="calendar-card-actions">
                    <button
                      className="calendar-action-btn calendar-action-btn--prep"
                      onClick={() =>
                        handleQuickPrompt(
                          `Chuẩn bị cuộc họp: ${evt.summary || 'Cuộc họp sắp tới'}`
                        )
                      }
                      title="Yêu cầu Trợ lý chuẩn bị hồ sơ cuộc họp này"
                    >
                      <Sparkles size={12} />
                      <span>Chuẩn bị họp</span>
                    </button>

                    <button
                      className="calendar-action-btn calendar-action-btn--email"
                      onClick={() =>
                        handleQuickPrompt(
                          `Soạn email gửi người tham dự cuộc họp "${evt.summary}" để thông báo chuẩn bị tài liệu`
                        )
                      }
                      title="Soạn thư gửi các thành viên tham dự"
                    >
                      <Mail size={12} />
                      <span>Gửi email</span>
                    </button>
                  </div>
                </div>

                {evt.html_link && (
                  <div className="agenda-card-footer">
                    <a
                      href={evt.html_link}
                      target="_blank"
                      rel="noreferrer"
                      className="agenda-open-link"
                    >
                      <span>Xem trong Calendar</span>
                      <ExternalLink size={11} />
                    </a>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
