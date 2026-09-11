import React from 'react'
import clsx from 'clsx'
import './Button.css'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'safe' | 'warning' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  isLoading?: boolean
  leftIcon?: React.ReactNode
  rightIcon?: React.ReactNode
}

export const Button: React.FC<ButtonProps> = ({
  children,
  className,
  variant = 'secondary',
  size = 'md',
  isLoading = false,
  leftIcon,
  rightIcon,
  disabled,
  ...props
}) => {
  return (
    <button
      className={clsx('btn', `btn--${variant}`, `btn--${size}`, isLoading && 'btn--loading', className)}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading && <span className="btn__spinner" />}
      {!isLoading && leftIcon && <span className="btn__icon btn__icon--left">{leftIcon}</span>}
      <span className="btn__text">{children}</span>
      {!isLoading && rightIcon && <span className="btn__icon btn__icon--right">{rightIcon}</span>}
    </button>
  )
}
