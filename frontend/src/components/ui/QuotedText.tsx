import React from 'react'

const QUOTED = /“([^”]+)”/g

/**
 * Plain server text with its quoted names (“Gửi báo cáo tuần”) shown in bold, without
 * the quote marks. Everything stays text — nothing is parsed as markup or HTML.
 */
export const QuotedText: React.FC<{ text: string }> = ({ text }) => {
  const parts: React.ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(QUOTED)) {
    const start = match.index ?? 0
    if (start > last) parts.push(text.slice(last, start))
    parts.push(<strong key={start}>{match[1]}</strong>)
    last = start + match[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return <>{parts}</>
}
