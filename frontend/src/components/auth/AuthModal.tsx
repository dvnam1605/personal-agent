import React, { useState } from 'react'
import { LogIn, UserPlus, X, Mail, Lock, User, AlertCircle, ShieldAlert } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { UserProfile } from '../../types/api'
import { NammLogo } from '../common/NammLogo'
import './AuthModal.css'

interface AuthModalProps {
  isOpen: boolean
  onClose?: () => void
  onSuccess: (user: UserProfile) => void
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [isRegister, setIsRegister] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLocked, setIsLocked] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

  if (!isOpen) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      if (isRegister) {
        if (!email.trim() || !password.trim()) {
          throw new Error('Vui lòng điền đầy đủ email và mật khẩu.')
        }
        if (password.length < 6) {
          throw new Error('Mật khẩu phải có ít nhất 6 ký tự.')
        }
        const resp = await apiClient.register(email, password, fullName)
        onSuccess(resp.user)
      } else {
        if (!email.trim() || !password.trim()) {
          throw new Error('Vui lòng nhập email và mật khẩu.')
        }
        const resp = await apiClient.login(email, password)
        onSuccess(resp.user)
      }
    } catch (err: any) {
      const msg = err?.message || 'Có lỗi xảy ra trong quá trình xác thực.'
      setError(msg)
      if (err?.status === 429 || msg.includes('tạm khóa') || msg.includes('quá nhiều')) {
        setIsLocked(true)
      } else {
        setIsLocked(false)
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="auth-modal-overlay">
      <div className="auth-modal-card">
        {onClose && (
          <button className="auth-modal-close" onClick={onClose} aria-label="Đóng">
            <X size={18} />
          </button>
        )}

        <div className="auth-modal-header">
          <div className="auth-brand-logo">
            <NammLogo size={28} />
          </div>
          <h2 className="auth-modal-title">
            {isRegister ? 'Tạo tài khoản Naot' : 'Đăng nhập Naot'}
          </h2>
          <p className="auth-modal-subtitle">
            {isRegister
              ? 'Đăng ký tài khoản để đồng bộ và bảo mật lịch sử hội thoại của bạn'
              : 'Đăng nhập để xem và quản lý các phiên làm việc của bạn'}
          </p>
        </div>

        {error && (
          <div className={`auth-error-banner ${isLocked ? 'auth-error-banner--locked' : ''}`}>
            {isLocked ? <ShieldAlert size={16} /> : <AlertCircle size={15} />}
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="auth-form">
          {isRegister && (
            <div className="auth-field">
              <label className="auth-label">Họ và tên</label>
              <div className="auth-input-wrapper">
                <User size={15} className="auth-input-icon" />
                <input
                  type="text"
                  className="auth-input"
                  placeholder="Ví dụ: Nguyễn Văn A"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  disabled={isLoading}
                />
              </div>
            </div>
          )}

          <div className="auth-field">
            <label className="auth-label">Email</label>
            <div className="auth-input-wrapper">
              <Mail size={15} className="auth-input-icon" />
              <input
                type="email"
                required
                className="auth-input"
                placeholder="tenban@vov.vn"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isLoading}
              />
            </div>
          </div>

          <div className="auth-field">
            <label className="auth-label">Mật khẩu</label>
            <div className="auth-input-wrapper">
              <Lock size={15} className="auth-input-icon" />
              <input
                type="password"
                required
                className="auth-input"
                placeholder="Tối thiểu 6 ký tự"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isLoading}
              />
            </div>
          </div>

          <button type="submit" className="auth-submit-btn" disabled={isLoading}>
            {isLoading ? (
              <span className="auth-loading-spinner" />
            ) : isRegister ? (
              <>
                <UserPlus size={16} />
                <span>Đăng ký tài khoản</span>
              </>
            ) : (
              <>
                <LogIn size={16} />
                <span>Đăng nhập ngay</span>
              </>
            )}
          </button>
        </form>

        <div className="auth-modal-footer">
          {isRegister ? (
            <span>
              Đã có tài khoản?{' '}
              <button
                type="button"
                className="auth-switch-btn"
                onClick={() => {
                  setIsRegister(false)
                  setError(null)
                  setIsLocked(false)
                }}
              >
                Đăng nhập
              </button>
            </span>
          ) : (
            <span>
              Chưa có tài khoản?{' '}
              <button
                type="button"
                className="auth-switch-btn"
                onClick={() => {
                  setIsRegister(true)
                  setError(null)
                  setIsLocked(false)
                }}
              >
                Đăng ký tài khoản mới
              </button>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
