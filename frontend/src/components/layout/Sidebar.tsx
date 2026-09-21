import React from 'react'
import {
  MessageSquarePlus,
  Settings,
  ChevronLeft,
  ChevronRight,
  Clock,
  X,
  MessageSquare,
} from 'lucide-react'
import clsx from 'clsx'
import type { WorkspaceType } from './Header'
import { NammLogo } from '../common/NammLogo'
import './Sidebar.css'

interface SidebarProps {
  isOpen: boolean
  onClose: () => void
  isCollapsed: boolean
  onToggleCollapse: () => void
  activeWorkspace: WorkspaceType
  onSelectWorkspace: (workspace: WorkspaceType) => void
  onNewSession: () => void
  historyTitles?: string[]
  onSelectHistory?: (title: string) => void
}

export const Sidebar: React.FC<SidebarProps> = ({
  isOpen,
  onClose,
  isCollapsed,
  onToggleCollapse,
  activeWorkspace,
  onSelectWorkspace,
  onNewSession,
  historyTitles = [],
  onSelectHistory,
}) => {
  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={clsx('sidebar-backdrop', isOpen && 'sidebar-backdrop--visible')}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        className={clsx(
          'executive-sidebar',
          isOpen && 'executive-sidebar--open',
          isCollapsed && 'executive-sidebar--collapsed'
        )}
      >
        {/* Brand Bar */}
        <div className="sidebar-brand-bar">
          <div className="sidebar-brand-logo">
            <div className="brand-gem">
              <NammLogo size={20} />
            </div>
            {!isCollapsed && (
              <div className="brand-info">
                <span className="brand-title">Naot</span>
                <span className="brand-badge">Executive</span>
              </div>
            )}
          </div>
          <button
            className="sidebar-mobile-close"
            onClick={onClose}
            aria-label="Đóng menu"
          >
            <X size={18} />
          </button>
        </div>

        {/* 1. New Conversation Button */}
        <div className="sidebar-section">
          <button
            className="sidebar-new-chat-btn"
            onClick={() => {
              onNewSession()
              onSelectWorkspace('assistant')
              if (window.innerWidth < 1024) onClose()
            }}
            title="Tạo phiên hội thoại mới"
          >
            <MessageSquarePlus size={16} />
            {!isCollapsed && <span>Hội thoại mới</span>}
          </button>
        </div>

        {/* 2. Chat History & Sessions */}
        <div className="sidebar-section sidebar-section--recent">
          {!isCollapsed && (
            <div className="sidebar-history-header">
              <span className="sidebar-section-title">Lịch sử hội thoại</span>
              <span className="history-count-badge">{historyTitles.length}</span>
            </div>
          )}

          {historyTitles.length === 0 ? (
            !isCollapsed && (
              <div className="sidebar-empty-history">
                <MessageSquare size={20} className="empty-history-icon" />
                <span>Chưa có phiên gần đây</span>
              </div>
            )
          ) : (
            <div className="sidebar-recent-list">
              {historyTitles.slice(-12).reverse().map((title, idx) => (
                <button
                  key={idx}
                  className="sidebar-recent-item"
                  onClick={() => {
                    onSelectHistory?.(title)
                    onSelectWorkspace('assistant')
                    if (window.innerWidth < 1024) onClose()
                  }}
                  title={title}
                >
                  <Clock size={13} className="recent-item-icon" />
                  {!isCollapsed && <span className="recent-item-text">{title}</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 3. Footer: Settings & Collapse Toggle */}
        <div className="sidebar-footer">
          <button
            className={clsx(
              'sidebar-footer-item',
              activeWorkspace === 'settings' && 'sidebar-footer-item--active'
            )}
            onClick={() => {
              onSelectWorkspace('settings')
              if (window.innerWidth < 1024) onClose()
            }}
            title="Cài đặt & Tài khoản"
          >
            <Settings size={15} />
            {!isCollapsed && <span>Cài đặt hệ thống</span>}
          </button>

          {/* Desktop collapse rail toggle */}
          <button
            className="sidebar-collapse-toggle"
            onClick={onToggleCollapse}
            title={isCollapsed ? 'Mở rộng menu' : 'Thu gọn menu'}
            aria-label={isCollapsed ? 'Mở rộng menu' : 'Thu gọn menu'}
          >
            {isCollapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
            {!isCollapsed && <span>Thu gọn</span>}
          </button>
        </div>
      </aside>
    </>
  )
}
