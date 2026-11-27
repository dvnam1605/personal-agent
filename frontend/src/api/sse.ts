/**
 * Server-sent-event streams read over `fetch`.
 *
 * `EventSource` cannot send auth headers or a POST body, so the response body is split into
 * `event:` / `data:` frames by hand. Frames are handed to an async handler strictly one at a time:
 * the next frame waits until the previous handler has settled, so a slow handler (one that fetches
 * a pending approval, say) can never be overtaken by the `done` event that follows it.
 */

import { CONNECTION_ERROR_MESSAGE, errorFromResponse } from './core'

/** What a frame handler tells the reader: keep going, or stop reading the stream. */
type FrameVerdict = 'continue' | 'stop'

/** Read `body` frame by frame until it ends or the handler asks to stop; resolves to how it ended. */
export async function readSseFrames(
  body: ReadableStream<Uint8Array>,
  onFrame: (event: string, data: string) => Promise<FrameVerdict> | FrameVerdict
): Promise<'eof' | 'stopped'> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let currentEvent = ''

  const handleLine = async (line: string): Promise<FrameVerdict> => {
    const trimmed = line.trim()
    if (!trimmed) {
      currentEvent = ''
      return 'continue'
    }
    if (trimmed.startsWith('event:')) {
      currentEvent = trimmed.slice(6).trim()
      return 'continue'
    }
    if (trimmed.startsWith('data:')) {
      return onFrame(currentEvent, trimmed.slice(5).trim())
    }
    return 'continue'
  }

  while (true) {
    const { done, value } = await reader.read()
    if (!done) buffer += decoder.decode(value, { stream: true })
    else buffer += decoder.decode()

    const lines = buffer.split('\n')
    buffer = done ? '' : lines.pop() || ''
    for (const line of lines) {
      if ((await handleLine(line)) === 'stop') {
        reader.cancel().catch(() => undefined)
        return 'stopped'
      }
    }
    if (done) return 'eof'
  }
}

/** Parse one frame's JSON payload; a malformed frame is logged and skipped. */
function parseFrame(event: string, data: string): any | undefined {
  try {
    return JSON.parse(data)
  } catch (err) {
    console.warn(`Skipping malformed SSE frame for event "${event}":`, err)
    return undefined
  }
}

export interface QueryStreamMetadata {
  run_id: string
  status: string
  route: any
}

/** `answer`: the final text with the cited images placed, replacing the streamed text. */
export interface QueryStreamCitations {
  citations?: any[]
  sufficiency?: string
  data?: any
  approval_id?: string
  answer?: string
}

export interface QueryStreamDone {
  run_id: string
  status: string
  ttft?: number
  token_count?: number
  duration?: number
  tok_per_sec?: number
}

/** Handlers for one query stream; each may be async and is awaited before the next event. */
export interface QueryStreamCallbacks {
  onMetadata?: (data: QueryStreamMetadata) => void | Promise<void>
  onToken?: (delta: string) => void | Promise<void>
  onCitations?: (data: QueryStreamCitations) => void | Promise<void>
  onError?: (err: Error) => void | Promise<void>
  onDone?: (data: QueryStreamDone) => void | Promise<void>
}

export const STREAM_INTERRUPTED_MESSAGE =
  'Kết nối bị ngắt trước khi trợ lý trả lời xong. Vui lòng thử lại.'

/**
 * POST a query to `/query/stream` and dispatch its events in order. The stream counts as finished
 * only on `done` or `error`; an end of stream without either is reported through `onError`.
 * Aborting `signal` rejects with the fetch `AbortError` so the caller can tell a cancel from a failure.
 */
export async function streamQuery(
  headers: Record<string, string>,
  payload: object,
  callbacks: QueryStreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  const fail = async (err: Error) => {
    if (callbacks.onError) await callbacks.onError(err)
    else throw err
  }

  let response: Response
  try {
    response = await fetch('/query/stream', {
      method: 'POST',
      headers: { ...headers, Accept: 'text/event-stream' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (err: any) {
    if (err?.name === 'AbortError') throw err
    return fail(new Error(CONNECTION_ERROR_MESSAGE))
  }

  if (!response.ok) return fail(await errorFromResponse(response))
  if (!response.body) return fail(new Error('Trình duyệt không hỗ trợ nhận phản hồi dạng luồng.'))

  let finished = false
  try {
    await readSseFrames(response.body, async (event, raw) => {
      const data = parseFrame(event, raw)
      if (data === undefined) return 'continue'
      if (event === 'metadata') {
        await callbacks.onMetadata?.(data)
      } else if (event === 'token') {
        if (data.delta) await callbacks.onToken?.(data.delta)
      } else if (event === 'citations') {
        await callbacks.onCitations?.(data)
      } else if (event === 'document') {
        await callbacks.onCitations?.({ data: { generated_document: data } })
      } else if (event === 'done') {
        finished = true
        await callbacks.onDone?.(data)
        return 'stop'
      } else if (event === 'error') {
        finished = true
        await fail(new Error(data.message || 'Lỗi khi nhận phản hồi từ trợ lý.'))
        return 'stop'
      }
      return 'continue'
    })
  } catch (err: any) {
    if (err?.name === 'AbortError') throw err
    if (finished) throw err
    finished = true
    return fail(err instanceof Error ? err : new Error(STREAM_INTERRUPTED_MESSAGE))
  }

  if (!finished) await fail(new Error(STREAM_INTERRUPTED_MESSAGE))
}

/**
 * Hold the live notification stream open until it ends or `signal` aborts; calls
 * `onNotification` for every new notification id. Resolves `false` when the server
 * reports live delivery unavailable, so the caller can fall back to polling.
 */
export async function streamNotifications(
  headers: Record<string, string>,
  onNotification: (id: string) => void,
  signal: AbortSignal
): Promise<boolean> {
  const response = await fetch('/notifications/stream', {
    headers: { ...headers, Accept: 'text/event-stream' },
    signal,
  })
  if (!response.ok) throw await errorFromResponse(response)
  if (!response.body) throw new Error('Trình duyệt không hỗ trợ nhận phản hồi dạng luồng.')

  let available = true
  await readSseFrames(response.body, (event, raw) => {
    if (event === 'unavailable') {
      available = false
      return 'stop'
    }
    if (event === 'notification') {
      const payload = parseFrame(event, raw) as { id?: string } | undefined
      if (payload?.id) onNotification(payload.id)
    }
    return 'continue'
  })
  return available
}
