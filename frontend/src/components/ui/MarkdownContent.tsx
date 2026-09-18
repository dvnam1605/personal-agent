import React from 'react'
import './MarkdownContent.css'

interface MarkdownContentProps {
  content: string
  className?: string
}

export const MarkdownContent: React.FC<MarkdownContentProps> = ({ content, className = '' }) => {
  // Helper to render inline formatting: bold, italic, code, links
  const renderInline = (text: string): React.ReactNode[] => {
    // Regex for inline tokens: `code`, **bold**, *italic*, [label](url), [1] citation badge
    const tokenRegex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\)|\[\d+\])/g
    const parts = text.split(tokenRegex)

    return parts.map((part, index) => {
      if (!part) return null

      // Citation badge: [1], [2]
      if (/^\[\d+\]$/.test(part)) {
        const num = part.slice(1, -1)
        return (
          <span key={index} className="md-citation-badge" title={`Nguồn dẫn chứng [${num}]`}>
            {num}
          </span>
        )
      }

      // Inline code: `code`
      if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
        return (
          <code key={index} className="md-inline-code">
            {part.slice(1, -1)}
          </code>
        )
      }

      // Bold: **bold**
      if (part.startsWith('**') && part.endsWith('**') && part.length >= 4) {
        return (
          <strong key={index} className="md-bold">
            {part.slice(2, -2)}
          </strong>
        )
      }

      // Italic: *italic*
      if (part.startsWith('*') && part.endsWith('*') && part.length >= 2) {
        return (
          <em key={index} className="md-italic">
            {part.slice(1, -1)}
          </em>
        )
      }

      // Link: [label](url)
      const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      if (linkMatch) {
        const [, label, url] = linkMatch
        const isExternal = url.startsWith('http://') || url.startsWith('https://')
        return (
          <a
            key={index}
            href={url}
            target={isExternal ? '_blank' : undefined}
            rel={isExternal ? 'noopener noreferrer' : undefined}
            className="md-link"
          >
            {label}
          </a>
        )
      }

      return <span key={index}>{part}</span>
    })
  }

  // Parse markdown blocks
  const lines = content.split('\n')
  const elements: React.ReactNode[] = []

  let inCodeBlock = false
  let codeBlockLang = ''
  let codeBlockLines: string[] = []

  let inTable = false
  let tableHeader: string[] = []
  let tableRows: string[][] = []

  let listItems: React.ReactNode[] = []
  let isNumberedList = false

  const flushList = () => {
    if (listItems.length > 0) {
      if (isNumberedList) {
        elements.push(
          <ol key={`ol-${elements.length}`} className="md-ol">
            {listItems.map((item, idx) => (
              <li key={idx}>{item}</li>
            ))}
          </ol>
        )
      } else {
        elements.push(
          <ul key={`ul-${elements.length}`} className="md-ul">
            {listItems.map((item, idx) => (
              <li key={idx}>{item}</li>
            ))}
          </ul>
        )
      }
      listItems = []
    }
  }

  const flushTable = () => {
    if (inTable && tableHeader.length > 0) {
      elements.push(
        <div key={`table-${elements.length}`} className="md-table-wrapper">
          <table className="md-table">
            <thead>
              <tr>
                {tableHeader.map((th, i) => (
                  <th key={i}>{renderInline(th.trim())}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row, rIdx) => (
                <tr key={rIdx}>
                  {row.map((cell, cIdx) => (
                    <td key={cIdx}>{renderInline(cell.trim())}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      inTable = false
      tableHeader = []
      tableRows = []
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // Code block toggle
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <div key={`code-${elements.length}`} className="md-code-block">
            {codeBlockLang && <div className="md-code-lang">{codeBlockLang}</div>}
            <pre>
              <code>{codeBlockLines.join('\n')}</code>
            </pre>
          </div>
        )
        inCodeBlock = false
        codeBlockLines = []
        codeBlockLang = ''
      } else {
        flushList()
        flushTable()
        inCodeBlock = true
        codeBlockLang = line.trim().slice(3).trim()
      }
      continue
    }

    if (inCodeBlock) {
      codeBlockLines.push(line)
      continue
    }

    // Check table
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      flushList()
      const cells = line
        .trim()
        .slice(1, -1)
        .split('|')
      
      // Check if separator line |---|---|
      const isSeparator = cells.every((c) => /^\s*:?-+:?\s*$/.test(c))
      if (isSeparator) {
        continue
      }

      if (!inTable) {
        inTable = true
        tableHeader = cells
        tableRows = []
      } else {
        tableRows.push(cells)
      }
      continue
    } else {
      flushTable()
    }

    // Check headings
    if (line.startsWith('### ')) {
      flushList()
      elements.push(
        <h3 key={`h3-${elements.length}`} className="md-h3">
          {renderInline(line.slice(4))}
        </h3>
      )
      continue
    }
    if (line.startsWith('## ')) {
      flushList()
      elements.push(
        <h2 key={`h2-${elements.length}`} className="md-h2">
          {renderInline(line.slice(3))}
        </h2>
      )
      continue
    }
    if (line.startsWith('# ')) {
      flushList()
      elements.push(
        <h1 key={`h1-${elements.length}`} className="md-h1">
          {renderInline(line.slice(2))}
        </h1>
      )
      continue
    }

    // Check Horizontal Rule
    if (/^(\*\*\*|---|___)$/.test(line.trim())) {
      flushList()
      elements.push(<hr key={`hr-${elements.length}`} className="md-hr" />)
      continue
    }

    // Check Blockquote
    if (line.startsWith('> ')) {
      flushList()
      elements.push(
        <blockquote key={`quote-${elements.length}`} className="md-blockquote">
          {renderInline(line.slice(2))}
        </blockquote>
      )
      continue
    }

    // Check Unordered List
    const ulMatch = line.match(/^(\s*)[-*+]\s+(.*)$/)
    if (ulMatch) {
      if (isNumberedList) flushList()
      isNumberedList = false
      listItems.push(renderInline(ulMatch[2]))
      continue
    }

    // Check Ordered List
    const olMatch = line.match(/^(\s*)\d+\.\s+(.*)$/)
    if (olMatch) {
      if (!isNumberedList) flushList()
      isNumberedList = true
      listItems.push(renderInline(olMatch[2]))
      continue
    }

    // If we were in a list and hit non-list line
    flushList()

    // Empty line
    if (!line.trim()) {
      continue
    }

    // Normal paragraph
    elements.push(
      <p key={`p-${elements.length}`} className="md-p">
        {renderInline(line)}
      </p>
    )
  }

  flushList()
  flushTable()

  return <div className={`markdown-content ${className}`}>{elements}</div>
}
