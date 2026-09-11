import React, { useId } from 'react'

interface NammLogoProps {
  size?: number
  className?: string
  animated?: boolean
}

/**
 * Bespoke geometric monogram logo for "Namm Agent".
 * Combines an architectural "N" prism with dynamic gradient stems
 * and a glowing central neural core representing executive AI intelligence.
 */
export const NammLogo: React.FC<NammLogoProps> = ({
  size = 24,
  className = '',
  animated = false,
}) => {
  const rawId = useId()
  const id = rawId.replace(/[^a-zA-Z0-9_-]/g, '')

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`namm-logo-svg ${animated ? 'namm-logo-animated' : ''} ${className}`}
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}
      aria-label="Namm Agent Logo"
    >
      <defs>
        <linearGradient id={`namm-stem-l-${id}`} x1="5.5" y1="5" x2="11" y2="27" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#818CF8" />
          <stop offset="100%" stopColor="#4F46E5" />
        </linearGradient>

        <linearGradient id={`namm-bridge-${id}`} x1="8" y1="7" x2="24" y2="25" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#C084FC" />
          <stop offset="50%" stopColor="#6366F1" />
          <stop offset="100%" stopColor="#06B6D4" />
        </linearGradient>

        <linearGradient id={`namm-stem-r-${id}`} x1="21" y1="5" x2="26.5" y2="27" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#38BDF8" />
          <stop offset="100%" stopColor="#6366F1" />
        </linearGradient>

        <filter id={`namm-glow-${id}`} x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="1.5" stdDeviation="1.5" floodColor="#6366F1" floodOpacity="0.4" />
        </filter>
      </defs>

      <g filter={`url(#namm-glow-${id})`}>
        {/* Left vertical pillar */}
        <rect x="5.5" y="5" width="5.5" height="22" rx="2.75" fill={`url(#namm-stem-l-${id})`} />

        {/* Diagonal dynamic prism traverse */}
        <path
          d="M 8.25 7.75 L 23.75 24.25"
          stroke={`url(#namm-bridge-${id})`}
          strokeWidth="5.5"
          strokeLinecap="round"
        />

        {/* Right vertical pillar */}
        <rect x="21" y="5" width="5.5" height="22" rx="2.75" fill={`url(#namm-stem-r-${id})`} />

        {/* Central neural intelligence nexus */}
        <circle cx="16" cy="16" r="2.2" fill="#FFFFFF" />
        <circle cx="16" cy="16" r="3.8" stroke="#E0E7FF" strokeWidth="0.8" opacity="0.85" />
      </g>
    </svg>
  )
}
