import React, { createContext, useContext, useState, useCallback } from 'react'
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react'
import clsx from 'clsx'
import './Toast.css'

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface ToastItem {
  id: string
  type: ToastType
  message: string
}

interface ToastContextValue {
  showToast: (message: string, type?: ToastType) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const showToast = useCallback(
    (message: string, type: ToastType = 'info') => {
      const id = Math.random().toString(36).substring(2, 9)
      const toast: ToastItem = { id, type, message }
      setToasts((prev) => [...prev, toast])

      setTimeout(() => {
        removeToast(id)
      }, 4000)
    },
    [removeToast]
  )

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="toast-container" role="region" aria-label="Thông báo hệ thống">
        {toasts.map((toast) => (
          <div key={toast.id} className={clsx('toast-item', `toast-item--${toast.type}`)}>
            <span className="toast-item__icon">
              {toast.type === 'success' && <CheckCircle2 size={16} />}
              {toast.type === 'error' && <AlertCircle size={16} />}
              {toast.type === 'warning' && <AlertCircle size={16} />}
              {toast.type === 'info' && <Info size={16} />}
            </span>
            <span className="toast-item__msg">{toast.message}</span>
            <button
              className="toast-item__close"
              onClick={() => removeToast(toast.id)}
              aria-label="Đóng thông báo"
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within a ToastProvider')
  return ctx
}
