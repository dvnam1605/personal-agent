/**
 * Rich Markdown leaves: document images and math.
 *
 * Images live behind the authenticated `/documents/images/{key}` endpoint, so they are fetched as
 * object URLs (revoked on unmount). Math is rendered to MathML by Temml, which escapes text and
 * rejects `\href`-style commands by default, so its output is safe to inject.
 */

import React from 'react'
import temml from 'temml'
import 'temml/dist/Temml-Local.css'
import { apiClient } from '../../../api/client'

export const DocumentImage: React.FC<{ imageKey: string; alt: string }> = ({ imageKey, alt }) => {
  const [url, setUrl] = React.useState<string | null>(null)
  const [failed, setFailed] = React.useState(false)
  React.useEffect(() => {
    let objectUrl: string | null = null
    let cancelled = false
    apiClient
      .fetchDocumentImage(imageKey)
      .then((u) => {
        objectUrl = u
        if (cancelled) URL.revokeObjectURL(u)
        else setUrl(u)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [imageKey])
  const label = alt.split(' — ')[0]
  return (
    <figure className="md-figure">
      {url ? (
        <img src={url} alt={alt} title={alt} className="md-figure-img" />
      ) : (
        <div className="md-figure-placeholder">{failed ? 'Không tải được hình' : 'Đang tải hình…'}</div>
      )}
      <figcaption className="md-figure-caption">{label}</figcaption>
    </figure>
  )
}

export const InlineMath: React.FC<{ latex: string }> = ({ latex }) => {
  const html = React.useMemo(
    () => temml.renderToString(latex, { displayMode: false, throwOnError: false }),
    [latex],
  )
  return <span className="md-inline-math" dangerouslySetInnerHTML={{ __html: html }} />
}

export const Equation: React.FC<{ latex: string; number?: string }> = ({ latex, number }) => {
  const html = React.useMemo(
    () => temml.renderToString(latex, { displayMode: true, throwOnError: false }),
    [latex],
  )
  return (
    <div className="md-equation">
      <span className="md-equation-body" dangerouslySetInnerHTML={{ __html: html }} />
      {number && <span className="md-equation-number">({number})</span>}
    </div>
  )
}
