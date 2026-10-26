import React, { useEffect, useState } from 'react'
import {
  ExternalLink,
  Menu,
  User,
  CheckCircle2,
  AlertTriangle,
  Sun,
  Moon,
  LogOut,
  LogIn,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { GoogleIntegrationStatus, UserProfile } from '../../types/api'
import { NammLogo } from '../common/NammLogo'
import { Hint } from '../ui/Hint'
import { useToast } from '../ui/toastContext'
import { WORKSPACE_TABS, type WorkspaceType } from './workspaces'
import './Header.css'

export type { WorkspaceType } from './workspaces'

interface HeaderProps {
  onToggleSidebar: () => void
  isSidebarOpen: boolean
  activeWorkspace: WorkspaceType
  onNavigate: (workspace: WorkspaceType) => void
  theme?: 'dark' | 'light'
  onToggleTheme?: () => void
  currentUser?: UserProfile | null
  onOpenAuth?: () => void
  onLogout?: () => void
}

export const Header: React.FC<HeaderProps> = ({
  onToggleSidebar,
  activeWorkspace,
  onNavigate,
  theme = 'dark',
  onToggleTheme,
  currentUser,
  onOpenAuth,
  onLogout,
}) => {
  const { showToast } = useToast()
  const [googleStatus, setGoogleStatus] = useState<GoogleIntegrationStatus | null>(null)

  useEffect(() => {
    // No state update after unmount; the poll keeps the badge current.
    let active = true
    const fetchStatus = async () => {
      try {
        const status = await apiClient.getGoogleStatus()
        if (active) setGoogleStatus(status)
      } catch {
        if (active) setGoogleStatus(null)
      }
    }
    fetchStatus()
    const interval = setInterval(fetchStatus, 30000)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [])

  const handleConnectGoogle = async () => {
    try {
      await apiClient.startGoogleConnect()
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không thể bắt đầu kết nối Google.', 'error')
    }
  }

  const isConnected = Boolean(googleStatus?.connected && googleStatus?.healthy)
  const hasMissingScopes = Boolean(googleStatus?.missing_scopes && googleStatus.missing_scopes.length > 0)

  return (
    <header className="executive-header">
      <div className="header-left">
        <Hint label="Mở hoặc thu gọn menu bên">
          <button
            className="header-sidebar-toggle"
            onClick={onToggleSidebar}
            aria-label="Mở hoặc thu gọn menu bên"
          >
            <Menu size={18} />
          </button>
        </Hint>

        <button
          type="button"
          className="header-brand-badge"
          onClick={() => onNavigate('assistant')}
          aria-label="Về trang Trợ lý Noat"
        >
          <span className="header-brand-gem">
            <NammLogo size={18} />
          </span>
          <span className="header-brand-text">
            <span className="brand-main-name">Noat</span>
            {/* <span className="brand-sub-name">Executive AI</span> */}
          </span>
        </button>
      </div>

      {/* Center Segmented Workspace Tabs */}
      <nav className="header-center-tabs">
        {WORKSPACE_TABS.map((tab) => {
          const isActive = activeWorkspace === tab.id
          const Icon = tab.icon
          return (
            <Hint key={tab.id} label={tab.label}>
              <button
                className={`header-tab-pill ${isActive ? 'header-tab-pill--active' : ''}`}
                onClick={() => onNavigate(tab.id)}
                aria-label={tab.label}
                aria-current={isActive ? 'page' : undefined}
              >
                <span className="tab-pill-icon"><Icon size={14} /></span>
                <span className="tab-pill-label">{tab.label}</span>
                {isActive && <span className="tab-pill-glow" />}
              </button>
            </Hint>
          )
        })}
      </nav>

      <div className="header-right">
        {/* Google Status Pill */}
        <div className={`google-status-pill ${isConnected && !hasMissingScopes ? 'google-status-pill--ok' : 'google-status-pill--warn'}`}>
          {isConnected && !hasMissingScopes ? (
            <div className="google-status-info" title={`Đã đồng bộ với tài khoản: ${googleStatus?.email || 'Google Workspace'}`}>
              <CheckCircle2 size={13} className="google-status-icon google-status-icon--ok" />
              <span className="google-status-label">{googleStatus?.email || 'Google Sync OK'}</span>
            </div>
          ) : (
            <div className="google-status-action">
              <AlertTriangle size={13} className="google-status-icon google-status-icon--warn" />
              <span className="google-status-label">
                {hasMissingScopes ? 'Cần cấp quyền' : 'Chưa kết nối'}
              </span>
              <Hint label="Cấp quyền liên kết Google Workspace">
                <button
                  className="btn-connect-google"
                  onClick={handleConnectGoogle}
                >
                  <span>Kết nối</span>
                  <ExternalLink size={11} />
                </button>
              </Hint>
            </div>
          )}
        </div>

        {/* User Identity Chip or Login Button */}
        {currentUser ? (
          <div className="header-user-badge" title={`Đã đăng nhập: ${currentUser.email}`}>
            <div className="header-user-avatar">
              <User size={13} />
            </div>
            <span className="header-user-name">
              {currentUser.full_name || currentUser.email.split('@')[0]}
            </span>
            {onLogout && (
              <Hint label="Đăng xuất khỏi tài khoản">
                <button
                  className="header-logout-btn"
                  onClick={onLogout}
                  aria-label="Đăng xuất"
                >
                  <LogOut size={13} />
                </button>
              </Hint>
            )}
          </div>
        ) : (
          <Hint label="Đăng nhập tài khoản">
            <button
              className="header-login-btn"
              onClick={onOpenAuth}
              aria-label="Đăng nhập"
            >
              <LogIn size={13} />
              <span>Đăng nhập</span>
            </button>
          </Hint>
        )}

        {/* Theme Toggle (Light / Dark) */}
        {onToggleTheme && (
          <Hint label={theme === 'dark' ? 'Chuyển sang giao diện Sáng (Light Mode)' : 'Chuyển sang giao diện Tối (Dark Mode)'}>
            <button
              className="header-theme-toggle"
              onClick={onToggleTheme}
              aria-label="Chuyển chế độ sáng tối"
            >
              {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
            </button>
          </Hint>
        )}
      </div>
    </header>
  )
}
