import React from 'react'
import { renderInline } from './markdown/inline'
import { DocumentImage, Equation } from './markdown/media'
import './MarkdownContent.css'

interface MarkdownContentProps {
  content: string
  className?: string
}

/** An image of a source document on its own line: "![Hình 4 — mô tả tự động: …](image:<key>)". */
const IMAGE_LINE = /^!\[([^\]\n]*)\]\(image:([0-9a-f]{24})\)$/

/** A display equation on its own line, optionally numbered: "$$\frac{a}{b}$$ (3.2)". */
const EQUATION_LINE = /^\$\$(.+?)\$\$\s*(?:\((\d+(?:\.\d+)+)\))?\s*$/

const HEADING_LINE = /^(#{1,6}) (.*)$/
const HEADING_TAGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'] as const

/**
 * A small Markdown renderer for assistant answers and generated documents.
 *
 * It walks the text line by line and builds blocks — fenced code, pipe tables, headings, rules,
 * quotes, bullet and numbered lists, document images, display equations and paragraphs — while
 * inline formatting is delegated to `markdown/inline`. Raw evidence ids that escaped the backend
 * are stripped first. No HTML from the text is ever injected; only Temml's MathML is.
 */
export const MarkdownContent: React.FC<MarkdownContentProps> = ({ content, className = '' }) => {
  const cleanContent = content
    .replace(/\[\s*[0-9a-fA-F]{32}[^\]\n]*\]?/g, '')
    .replace(/\b[0-9a-fA-F]{32}\b/g, '')
    .replace(/\[\s*\]/g, '')
    .replace(/[ \t]+([.,;:])/, '$1')

  const lines = cleanContent.split('\n')
  const elements: React.ReactNode[] = []

  let inCodeBlock = false
  let codeBlockLang = ''
  let codeBlockLines: string[] = []

  let inTable = false
  let tableHeader: string[] = []
  let tableRows: string[][] = []

  let listItems: React.ReactNode[] = []
  let isNumberedList = false
  // An image or equation between steps splits the list; keep counting from its number.
  let listStart = 1

  const flushList = () => {
    if (listItems.length > 0) {
      if (isNumberedList) {
        elements.push(
          <ol key={`ol-${elements.length}`} className="md-ol" start={listStart}>
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

    const picture = line.trim().match(IMAGE_LINE)
    if (picture) {
      flushList()
      flushTable()
      elements.push(
        <DocumentImage key={`img-${elements.length}`} imageKey={picture[2]} alt={picture[1]} />,
      )
      continue
    }

    const equation = line.trim().match(EQUATION_LINE)
    if (equation) {
      flushList()
      flushTable()
      elements.push(
        <Equation key={`eq-${elements.length}`} latex={equation[1]} number={equation[2]} />,
      )
      continue
    }

    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      flushList()
      const cells = line
        .trim()
        .slice(1, -1)
        .split('|')

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

    const heading = line.trimStart().match(HEADING_LINE)
    if (heading) {
      flushList()
      const level = heading[1].length
      const Tag = HEADING_TAGS[level - 1]
      elements.push(
        <Tag key={`h${level}-${elements.length}`} className={`md-h${level}`}>
          {renderInline(heading[2])}
        </Tag>
      )
      continue
    }

    if (/^(\*\*\*|---|___)$/.test(line.trim())) {
      flushList()
      elements.push(<hr key={`hr-${elements.length}`} className="md-hr" />)
      continue
    }

    if (line.startsWith('> ')) {
      flushList()
      elements.push(
        <blockquote key={`quote-${elements.length}`} className="md-blockquote">
          {renderInline(line.slice(2))}
        </blockquote>
      )
      continue
    }

    const ulMatch = line.match(/^(\s*)[-*+]\s+(.*)$/)
    if (ulMatch) {
      if (isNumberedList) flushList()
      isNumberedList = false
      listItems.push(renderInline(ulMatch[2]))
      continue
    }

    const olMatch = line.match(/^(\s*)(\d+)\.\s+(.*)$/)
    if (olMatch) {
      if (!isNumberedList) flushList()
      if (listItems.length === 0) listStart = Number(olMatch[2]) || 1
      isNumberedList = true
      listItems.push(renderInline(olMatch[3]))
      continue
    }

    flushList()

    if (!line.trim()) {
      continue
    }

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
