import React, { useState, useEffect } from 'react'
import {
  Settings,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
  Activity,
  Key,
  RefreshCw,
  Link2,
  Palette,
} from 'lucide-react'
import { Switch } from '@/components/shadcn/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/shadcn/tabs'
import { apiClient } from '../../api/client'
import type { GoogleIntegrationStatus, ReadinessResponse } from '../../types/api'
import { useToast } from '../ui/toastContext'
import { Hint } from '../ui/Hint'
import './Workspaces.css'
import './SettingsWorkspace.css'

interface SettingsWorkspaceProps {
  theme: 'dark' | 'light'
  onToggleTheme: () => void
}

export const SettingsWorkspace: React.FC<SettingsWorkspaceProps> = ({ theme, onToggleTheme }) => {
  const [googleStatus, setGoogleStatus] = useState<GoogleIntegrationStatus | null>(null)
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [displayName, setDisplayName] = useState(apiClient.getDisplayName())
  const [userId, setUserId] = useState(apiClient.getUserId())
  const [apiKey, setApiKey] = useState(apiClient.getApiKey())
  const [isLoading, setIsLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  const { showToast } = useToast()

  const loadStatus = () => {
    setIsLoading(true)
    setReloadKey((key) => key + 1)
  }

  useEffect(() => {
    let active = true
    const load = async () => {
      const [gStatus, ready] = await Promise.allSettled([
        apiClient.getGoogleStatus(),
        apiClient.getReadiness(),
      ])
      if (!active) return
      if (gStatus.status === 'fulfilled') setGoogleStatus(gStatus.value)
      if (ready.status === 'fulfilled') setReadiness(ready.value)
      setIsLoading(false)
    }
    load()
    return () => {
      active = false
    }
  }, [reloadKey])

  const handleSaveAuth = (e: React.FormEvent) => {
    e.preventDefault()
    apiClient.setDisplayName(displayName)
    apiClient.setUserId(userId)
    apiClient.setApiKey(apiKey)
    showToast('Đã lưu cấu hình tài khoản.', 'success')
  }

  const handleConnectGoogle = async () => {
    try {
      await apiClient.startGoogleConnect()
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không thể bắt đầu kết nối Google.', 'error')
    }
  }

  const isConnected = Boolean(googleStatus?.connected && googleStatus?.healthy)
  const hasMissingScopes = Boolean(googleStatus?.missing_scopes && googleStatus.missing_scopes.length > 0)
  const isReady = readiness?.status === 'ready'

  return (
    <div className="workspace-container settings-workspace">
      <div className="workspace-content-scroll settings-content-wrapper">
        <div className="settings-page-header">
          <Settings size={22} className="settings-header-icon" />
          <div>
            <h2>Cài đặt hệ thống & Tài khoản</h2>
            <p>Quản lý liên kết Google Workspace, định danh người dùng và kiểm tra trạng thái hệ thống.</p>
          </div>
        </div>

        <Tabs defaultValue="google" className="settings-tabs">
          <TabsList className="settings-tabs-list">
            <TabsTrigger value="google">
              <Link2 /> Google Workspace
            </TabsTrigger>
            <TabsTrigger value="account">
              <Key /> Tài khoản
            </TabsTrigger>
            <TabsTrigger value="appearance">
              <Palette /> Giao diện
            </TabsTrigger>
            <TabsTrigger value="system">
              <Activity /> Hệ thống
            </TabsTrigger>
          </TabsList>

          <TabsContent value="appearance">
            <div className="settings-card">
              <div className="settings-card-header">
                <div className="card-header-titles">
                  <h3>Giao diện</h3>
                  <p>Tùy chỉnh cách trợ lý hiển thị trên thiết bị này.</p>
                </div>
                <Palette size={18} className="text-dim" />
              </div>

              <div className="settings-toggle-row">
                <label htmlFor="setting-darkMode">
                  <span className="settings-toggle-title">Chế độ tối</span>
                  <span className="settings-toggle-hint">
                    Dùng nền tối, dịu mắt khi làm việc buổi tối. Đồng bộ với nút trên thanh tiêu đề.
                  </span>
                </label>
                <Switch
                  id="setting-darkMode"
                  checked={theme === 'dark'}
                  onCheckedChange={onToggleTheme}
                />
              </div>
            </div>
          </TabsContent>

          <TabsContent value="google">
            <div className="settings-card">
              <div className="settings-card-header">
                <div className="card-header-titles">
                  <h3>Liên kết Google Workspace</h3>
                  <p>Kết nối quyền truy cập Google Calendar, Gmail và Google Drive cho trợ lý cá nhân.</p>
                </div>
                <Hint label="Kiểm tra lại trạng thái">
                  <button
                    className="toolbar-btn"
                    onClick={loadStatus}
                    disabled={isLoading}
                  >
                    <RefreshCw size={13} className={isLoading ? 'spin-anim' : ''} />
                    <span>Kiểm tra lại</span>
                  </button>
                </Hint>
              </div>

              <div className="settings-card-body">
                <div className="status-indicator-box">
                  <div className="status-indicator-left">
                    {isConnected && !hasMissingScopes ? (
                      <CheckCircle2 size={24} className="text-success" />
                    ) : (
                      <AlertTriangle size={24} className="text-warning" />
                    )}
                    <div>
                      <h4>
                        {isConnected && !hasMissingScopes
                          ? 'Đã kết nối thành công'
                          : hasMissingScopes
                          ? 'Cần bổ sung quyền truy cập'
                          : 'Chưa liên kết tài khoản Google'}
                      </h4>
                      <p>
                        {googleStatus?.email
                          ? `Tài khoản: ${googleStatus.email}`
                          : 'Trợ lý cần quyền truy cập Google để xem lịch và xử lý email.'}
                      </p>
                    </div>
                  </div>

                  <button
                    className="btn-action-primary"
                    onClick={handleConnectGoogle}
                  >
                    <span>{isConnected ? 'Kết nối lại Google' : 'Liên kết Google Workspace'}</span>
                    <ExternalLink size={13} />
                  </button>
                </div>

                {hasMissingScopes && (
                  <div className="scopes-warning-box">
                    <strong>Quyền còn thiếu:</strong>
                    <ul>
                      {googleStatus?.missing_scopes?.map((sc, i) => (
                        <li key={i}>{sc}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="account">
            <div className="settings-card">
              <div className="settings-card-header">
                <div className="card-header-titles">
                  <h3>Định danh người dùng & Khóa bảo mật</h3>
                  <p>Cấu hình tiêu đề X-User-ID và X-API-Key được gửi kèm trong các truy vấn.</p>
                </div>
                <Key size={18} className="text-dim" />
              </div>

              <form onSubmit={handleSaveAuth} className="settings-form">
                <div className="form-row">
                  <label htmlFor="setting-displayName">Tên người dùng hiển thị</label>
                  <input
                    id="setting-displayName"
                    type="text"
                    className="settings-input"
                    placeholder="Ví dụ: namm"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                  />
                </div>

                <div className="form-row">
                  <label htmlFor="setting-userId">Mã định danh hệ thống (X-User-ID)</label>
                  <input
                    id="setting-userId"
                    type="text"
                    className="settings-input"
                    value={userId}
                    onChange={(e) => setUserId(e.target.value)}
                    required
                  />
                  <span className="settings-hint-text" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', marginTop: '4px' }}>
                    Mặc định: <code>default-user</code> để đồng bộ với Google Workspace và cơ sở dữ liệu nội bộ.
                  </span>
                </div>

                <div className="form-row">
                  <label htmlFor="setting-apiKey">Khóa truy cập API (X-API-Key — Tùy chọn)</label>
                  <input
                    id="setting-apiKey"
                    type="password"
                    className="settings-input"
                    placeholder="Để trống nếu không yêu cầu khóa bảo mật..."
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                  />
                </div>

                <div className="form-actions">
                  <button type="submit" className="btn-action-primary">
                    Lưu cấu hình
                  </button>
                </div>
              </form>
            </div>
          </TabsContent>

          <TabsContent value="system">
            <div className="settings-card">
              <div className="settings-card-header">
                <div className="card-header-titles">
                  <h3>Trạng thái hạ tầng máy chủ</h3>
                  <p>Kiểm tra tính sẵn sàng của cơ sở dữ liệu PostgreSQL và bộ nhớ đệm Redis.</p>
                </div>
                <Activity size={18} className="text-dim" />
              </div>

              <div className="diagnostics-grid">
                <div className="diagnostic-item">
                  <span className="diag-label">Trạng thái API:</span>
                  <span className={`diag-badge ${isReady ? 'diag-badge--ok' : 'diag-badge--warn'}`}>
                    {isReady ? 'Sẵn sàng hoạt động' : 'Đang khởi động / Chưa kết nối'}
                  </span>
                </div>

                <div className="diagnostic-item">
                  <span className="diag-label">Cơ sở dữ liệu (PostgreSQL):</span>
                  <span className="diag-val">
                    {readiness?.checks?.database?.status === 'ok' ? 'Đã kết nối' : 'Đang kiểm tra...'}
                  </span>
                </div>

                <div className="diagnostic-item">
                  <span className="diag-label">Bộ nhớ đệm (Redis):</span>
                  <span className="diag-val">
                    {readiness?.checks?.redis?.status === 'ok' ? 'Đã kết nối' : 'Đang kiểm tra...'}
                  </span>
                </div>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
