import React from 'react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/shadcn/tooltip'
import './Hint.css'

export interface HintProps {
  label: string
  side?: 'top' | 'right' | 'bottom' | 'left'
  children: React.ReactElement
}

/**
 * Animated tooltip for icon buttons, replacing the browser's native `title`
 * bubble; the wrapped element becomes the trigger, so it must accept a ref.
 */
export const Hint: React.FC<HintProps> = ({ label, side = 'bottom', children }) => (
  <Tooltip>
    <TooltipTrigger asChild>{children}</TooltipTrigger>
    <TooltipContent side={side} sideOffset={6} className="app-hint">
      {label}
    </TooltipContent>
  </Tooltip>
)
