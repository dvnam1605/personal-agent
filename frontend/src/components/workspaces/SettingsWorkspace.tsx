import React, { useState, useEffect } from 'react'
import {
  Settings,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
  Activity,
  Key,
  RefreshCw,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { GoogleIntegrationStatus, ReadinessResponse } from '../../types/api'
import { useToast } from '../ui/Toast'
import './Workspaces.css'

export const SettingsWorkspace: React.FC = () => {
  const [googleStatus, setGoogleStatus] = useState<GoogleIntegrationStatus | null>(null)
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [displayName, setDisplayName] = useState(apiClient.getDisplayName())
  const [userId, setUserId] = useState(apiClient.getUserId())
  const [apiKey, setApiKey] = useState(apiClient.getApiKey())
  const [isLoading, setIsLoading] = useState(false)

  const { showToast } = useToast()

  const loadStatus = async () => {
    setIsLoading(true)
    try {
      const [gStatus, ready] = await Promise.allSettled([
        apiClient.getGoogleStatus(),
        apiClient.getReadiness(),
      ])
      if (gStatus.status === 'fulfilled') setGoogleStatus(gStatus.value)
      if (ready.status === 'fulfilled') setReadiness(ready.value)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadStatus()
  }, [])

  const handleSaveAuth = (e: React.FormEvent) => {
    e.preventDefault()
    apiClient.setDisplayName(displayName)
    apiClient.setUserId(userId)
    apiClient.setApiKey(apiKey)
    showToast('Đã lưu cấu hình tài khoản.', 'success')
  }

  const handleConnectGoogle = () => {
    window.location.href = apiClient.getGoogleConnectUrl()
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

        {/* 1. Google Workspace Card */}
        <div className="settings-card">
          <div className="settings-card-header">
            <div className="card-header-titles">
              <h3>Liên kết Google Workspace</h3>
              <p>Kết nối quyền truy cập Google Calendar, Gmail và Google Drive cho trợ lý cá nhân.</p>
            </div>
            <button
              className="toolbar-btn"
              onClick={loadStatus}
              disabled={isLoading}
              title="Kiểm tra lại trạng thái"
            >
              <RefreshCw size={13} className={isLoading ? 'spin-anim' : ''} />
              <span>Kiểm tra lại</span>
            </button>
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

        {/* 2. User Identity & API Key */}
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

        {/* 3. System Diagnostics */}
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
      </div>
    </div>
  )
}
