import React from 'react'
import { X } from 'lucide-react'
import clsx from 'clsx'
import { Dialog as DialogPrimitive } from '@animate-ui/react/primitives/radix-ui'
import { Sheet, SheetClose, SheetContent } from '@/components/shadcn/sheet'
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

/**
 * Right-hand detail panel built on the Animate UI sheet, so it animates in and out
 * (Escape, outside click and focus trapping come from Radix); the app's own CSS
 * keeps the look of the previous hand-written drawer.
 */
export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  children,
  footer,
  size = 'md',
}) => (
  <Sheet open={isOpen} onOpenChange={(open) => !open && onClose()}>
    <SheetContent
      side="right"
      from="right"
      showCloseButton={false}
      overlayClassName="drawer-overlay"
      className={clsx('drawer-panel', `drawer-panel--${size}`)}
      aria-describedby={undefined}
    >
      <div className="drawer-header">
        <div className="drawer-header__titles">
          <DialogPrimitive.Title asChild>
            <h2 className="drawer-title">{title}</h2>
          </DialogPrimitive.Title>
          {subtitle && <p className="drawer-subtitle">{subtitle}</p>}
        </div>
        <SheetClose asChild>
          <button className="drawer-close-btn" aria-label="Đóng ngăn chi tiết">
            <X size={18} />
          </button>
        </SheetClose>
      </div>

      <div className="drawer-content">{children}</div>

      {footer && <div className="drawer-footer">{footer}</div>}
    </SheetContent>
  </Sheet>
)
