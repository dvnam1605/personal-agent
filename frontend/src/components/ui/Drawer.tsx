import React, { useEffect } from 'react'
import { X } from 'lucide-react'
import clsx from 'clsx'
import './Drawer.css'

export interface DrawerProps {
  isOpen: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: React.ReactNode
  footer?: React.ReactNode
  size?: 'md' | 'lg'
}

export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  children,
  footer,
  size = 'md',
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div className="drawer-overlay animate-fade-in" onClick={onClose} role="dialog" aria-modal="true">
      <div
        className={clsx('drawer-panel', `drawer-panel--${size}`, 'animate-slide-in-right')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-header">
          <div className="drawer-header__titles">
            <h2 className="drawer-title">{title}</h2>
            {subtitle && <p className="drawer-subtitle">{subtitle}</p>}
          </div>
          <button
            className="drawer-close-btn"
            onClick={onClose}
            aria-label="Đóng ngăn chi tiết"
          >
            <X size={18} />
          </button>
        </div>

        <div className="drawer-content">{children}</div>

        {footer && <div className="drawer-footer">{footer}</div>}
      </div>
    </div>
  )
}
