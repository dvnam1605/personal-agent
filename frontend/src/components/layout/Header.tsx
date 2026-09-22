import React, { useEffect, useState } from 'react'
import {
  Calendar,
  Mail,
  Users,
  FileText,
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
import './Header.css'

export type WorkspaceType = 'assistant' | 'calendar' | 'email' | 'meetings' | 'knowledge' | 'settings'

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
  const [googleStatus, setGoogleStatus] = useState<GoogleIntegrationStatus | null>(null)

  const fetchStatus = async () => {
    try {
      const status = await apiClient.getGoogleStatus()
      setGoogleStatus(status)
    } catch {
      setGoogleStatus(null)
    }
  }

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 30000)
    return () => clearInterval(interval)
  }, [])

  const handleConnectGoogle = () => {
    window.location.href = apiClient.getGoogleConnectUrl()
  }

  const isConnected = Boolean(googleStatus?.connected && googleStatus?.healthy)
  const hasMissingScopes = Boolean(googleStatus?.missing_scopes && googleStatus.missing_scopes.length > 0)

  const workspaceTabs: { id: WorkspaceType; label: string; icon: React.ReactNode }[] = [
    { id: 'assistant', label: 'Trợ lý điều hành', icon: <NammLogo size={14} /> },
    { id: 'calendar', label: 'Lịch Google', icon: <Calendar size={14} /> },
    { id: 'email', label: 'Hộp thư Gmail', icon: <Mail size={14} /> },
    { id: 'meetings', label: 'Hồ sơ họp WF-05', icon: <Users size={14} /> },
    { id: 'knowledge', label: 'Kho tri thức', icon: <FileText size={14} /> },
  ]

  return (
    <header className="executive-header">
      <div className="header-left">
        <button
          className="header-sidebar-toggle"
          onClick={onToggleSidebar}
          aria-label="Mở hoặc thu gọn menu bên"
          title="Mở hoặc thu gọn menu bên"
        >
          <Menu size={18} />
        </button>

        <div className="header-brand-badge" onClick={() => onNavigate('assistant')} title="Trợ lý Noat">
          <div className="header-brand-gem">
            <NammLogo size={18} />
          </div>
          <div className="header-brand-text">
            <span className="brand-main-name">Noat</span>
            {/* <span className="brand-sub-name">Executive AI</span> */}
          </div>
        </div>
      </div>

      {/* Center Segmented Workspace Tabs */}
      <nav className="header-center-tabs">
        {workspaceTabs.map((tab) => {
          const isActive = activeWorkspace === tab.id
          return (
            <button
              key={tab.id}
              className={`header-tab-pill ${isActive ? 'header-tab-pill--active' : ''}`}
              onClick={() => onNavigate(tab.id)}
            >
              <span className="tab-pill-icon">{tab.icon}</span>
              <span className="tab-pill-label">{tab.label}</span>
              {isActive && <span className="tab-pill-glow" />}
            </button>
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
              <button
                className="btn-connect-google"
                onClick={handleConnectGoogle}
                title="Cấp quyền liên kết Google Workspace"
              >
                <span>Kết nối</span>
                <ExternalLink size={11} />
              </button>
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
              <button
                className="header-logout-btn"
                onClick={onLogout}
                title="Đăng xuất khỏi tài khoản"
                aria-label="Đăng xuất"
              >
                <LogOut size={13} />
              </button>
            )}
          </div>
        ) : (
          <button
            className="header-login-btn"
            onClick={onOpenAuth}
            title="Đăng nhập tài khoản"
          >
            <LogIn size={13} />
            <span>Đăng nhập</span>
          </button>
        )}

        {/* Theme Toggle (Light / Dark) */}
        {onToggleTheme && (
          <button
            className="header-theme-toggle"
            onClick={onToggleTheme}
            title={theme === 'dark' ? 'Chuyển sang giao diện Sáng (Light Mode)' : 'Chuyển sang giao diện Tối (Dark Mode)'}
            aria-label="Chuyển chế độ sáng tối"
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        )}
      </div>
    </header>
  )
}
