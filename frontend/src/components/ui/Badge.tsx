import React from 'react'
import clsx from 'clsx'
import './Badge.css'

export interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'teal' | 'cyan' | 'safe' | 'warning' | 'danger' | 'outline'
  size?: 'sm' | 'md'
  icon?: React.ReactNode
  className?: string
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  size = 'md',
  icon,
  className,
}) => {
  return (
    <span className={clsx('badge', `badge--${variant}`, `badge--${size}`, className)}>
      {icon && <span className="badge__icon">{icon}</span>}
      <span className="badge__label">{children}</span>
    </span>
  )
}
