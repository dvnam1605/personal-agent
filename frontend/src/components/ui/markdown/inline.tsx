/**
 * Inline Markdown: one line of text split into tokens — inline math, `code`, **bold**, *italic*,
 * [label](url) links and [1] / [1, 2] citation badges. Stray 32-hex evidence ids that escaped the
 * backend's replacement are dropped.
 *
 * Link targets come from model output, so only http(s), mailto and relative URLs become links;
 * anything else (javascript:, data:, …) is shown as plain text.
 */

import React from 'react'
import { InlineMath } from './media'

const TOKEN_PATTERN =
  /(\$(?=\S)[^$\n]+?(?<=\S)\$|`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\)|\[\d+(?:\s*,\s*\d+)*\]|\[[0-9a-fA-F]{32}[^\]\n]*\]?)/g

const SAFE_SCHEMES = new Set(['http:', 'https:', 'mailto:'])

/** Return `url` if it is safe to put in an `href` (allowed scheme or relative), otherwise `null`. */
export function safeHref(url: string): string | null {
  const trimmed = url.trim()
  if (!trimmed) return null
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed)) {
    return trimmed.startsWith('//') ? null : trimmed
  }
  try {
    return SAFE_SCHEMES.has(new URL(trimmed).protocol) ? trimmed : null
  } catch {
    return null
  }
}

function renderCitationBadges(part: string, index: number): React.ReactNode {
  const nums = part
    .slice(1, -1)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  return (
    <React.Fragment key={index}>
      {nums.map((num, nIdx) => (
        <span key={`${index}-${nIdx}`} className="md-citation-badge" title={`Nguồn dẫn chứng [${num}]`}>
          {num}
        </span>
      ))}
    </React.Fragment>
  )
}

function renderLink(label: string, url: string, index: number): React.ReactNode {
  const href = safeHref(url)
  if (!href) return <span key={index}>{label}</span>
  const isExternal = /^https?:\/\//i.test(href)
  return (
    <a
      key={index}
      href={href}
      target={isExternal ? '_blank' : undefined}
      rel={isExternal ? 'noopener noreferrer' : undefined}
      className="md-link"
    >
      {label}
    </a>
  )
}

/** Render one line of inline Markdown into React nodes. */
export function renderInline(text: string): React.ReactNode[] {
  return text.split(TOKEN_PATTERN).map((part, index) => {
    if (!part) return null
    if (/^\[\d+(?:\s*,\s*\d+)*\]$/.test(part)) return renderCitationBadges(part, index)
    if (/^\$[^$\n]+\$$/.test(part)) return <InlineMath key={index} latex={part.slice(1, -1)} />
    if (/^\[?[0-9a-fA-F]{32}/.test(part)) return null
    if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
      return (
        <code key={index} className="md-inline-code">
          {part.slice(1, -1)}
        </code>
      )
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length >= 4) {
      return (
        <strong key={index} className="md-bold">
          {part.slice(2, -2)}
        </strong>
      )
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length >= 2) {
      return (
        <em key={index} className="md-italic">
          {part.slice(1, -1)}
        </em>
      )
    }
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (linkMatch) return renderLink(linkMatch[1], linkMatch[2], index)
    return <span key={index}>{part}</span>
  })
}
