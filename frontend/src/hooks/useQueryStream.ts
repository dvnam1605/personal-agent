import { useCallback, useEffect, useRef, useState } from 'react'
import type React from 'react'
import { apiClient } from '../api/client'
import type { ChatTurn } from '../components/conversation/MessageItem'
import type { ToastType } from '../components/ui/toastContext'
import type { ApprovalRequestResponse, DBConversationSummary } from '../types/api'

interface UseQueryStreamOptions {
  setTurns: React.Dispatch<React.SetStateAction<ChatTurn[]>>
  setConversations: React.Dispatch<React.SetStateAction<DBConversationSummary[]>>
  showToast: (message: string, type?: ToastType) => void
}

const STOPPED_MESSAGE = 'Đã dừng tạo câu trả lời.'
const FAILED_MESSAGE = 'Không thể xử lý yêu cầu.'

/** Pick the approval that belongs to this run out of the pending list. */
function pickApproval(
  pending: ApprovalRequestResponse[],
  approvalId: string | null,
  runId: string | null
): ApprovalRequestResponse | null {
  return (
    (approvalId ? pending.find((p) => p.id === approvalId) : undefined) ||
    pending.find((p) => p.run_id === runId) ||
    pending[0] ||
    null
  )
}

/**
 * Streams one query into a new turn and persists the finished turn to the conversation.
 *
 * Stream events are handled strictly in order (the client awaits each handler), and everything the
 * saved turn needs — message, merged data, approval id — is accumulated in local variables as the
 * events arrive, never inside a state updater, so the persisted turn always matches what was shown.
 * `isLoading` covers the wait for the first token (the typing indicator); `isStreaming` lasts until
 * `done` or an error, and the composer stays locked for that whole time. One stream runs at a time:
 * `stop()` aborts it, and so does unmounting.
 */
export function useQueryStream({ setTurns, setConversations, showToast }: UseQueryStreamOptions) {
  const [isLoading, setIsLoading] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)

  const stop = useCallback(() => {
    controllerRef.current?.abort()
  }, [])

  useEffect(() => stop, [stop])

  const streamQuery = async (queryText: string, currentConvId: string | null) => {
    stop()
    const controller = new AbortController()
    controllerRef.current = controller

    const turnId = Math.random().toString(36).substring(2, 9)
    const timestamp = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
    const updateTurn = (update: (turn: ChatTurn) => ChatTurn) =>
      setTurns((prev) => prev.map((turn) => (turn.id === turnId ? update(turn) : turn)))
    const failTurn = (message: string) => updateTurn((turn) => ({ ...turn, error: message }))

    setTurns((prev) => [...prev, { id: turnId, userQuery: queryText, timestamp }])
    setIsLoading(true)
    setIsStreaming(true)

    const startTime = Date.now()
    let firstTokenTime: number | null = null
    let tokenCount = 0
    let currentRunId: string | null = null
    let accumulatedMessage = ''
    let accumulatedData: Record<string, any> = {}
    let accumulatedApprovalId: string | null = null

    try {
      await apiClient.submitQueryStream(
        queryText,
        {
          onMetadata: (data) => {
            currentRunId = data.run_id
            updateTurn((turn) => ({
              ...turn,
              result: {
                run_id: data.run_id,
                status: data.status,
                message: '',
                route: data.route,
                data: {},
              },
            }))
          },
          onToken: (delta) => {
            const now = Date.now()
            if (firstTokenTime === null) {
              firstTokenTime = now
              setIsLoading(false)
            }
            const words = delta.trim() ? delta.trim().split(/\s+/).length : 0
            tokenCount += Math.max(1, words)
            const runningTokPerSec = tokenCount / Math.max(0.05, (now - firstTokenTime) / 1000)
            const ttft = (firstTokenTime - startTime) / 1000
            accumulatedMessage += delta
            const message = accumulatedMessage

            updateTurn((turn) => ({
              ...turn,
              ttft: turn.ttft ?? ttft,
              tokPerSec: runningTokPerSec,
              tokenCount,
              result: {
                ...(turn.result || {
                  run_id: 'pending',
                  status: 'processing',
                  route: { route_type: 'direct_specialist', confidence: 1, domains: [] },
                  data: {},
                }),
                message,
              },
            }))
          },
          onCitations: async (citeData) => {
            if (citeData.answer) accumulatedMessage = citeData.answer
            accumulatedData = { ...accumulatedData, ...(citeData.data || {}) }
            if (citeData.citations) accumulatedData.citations = citeData.citations
            if (citeData.sufficiency) accumulatedData.sufficiency = citeData.sufficiency

            let pendingApproval: ApprovalRequestResponse | null = null
            if (citeData.approval_id) {
              accumulatedApprovalId = citeData.approval_id
              try {
                const pending = await apiClient.getPendingApprovals(currentRunId || undefined)
                pendingApproval = pickApproval(pending, accumulatedApprovalId, currentRunId)
              } catch (err) {
                console.error('Failed to load pending approval in onCitations:', err)
              }
            }

            const data = accumulatedData
            const message = accumulatedMessage
            updateTurn((turn) =>
              turn.result
                ? {
                    ...turn,
                    result: {
                      ...turn.result,
                      message: message || turn.result.message,
                      data,
                      approval_id: citeData.approval_id || turn.result.approval_id,
                    },
                    pendingApproval: pendingApproval || turn.pendingApproval,
                  }
                : turn
            )
          },
          onDone: async (doneData) => {
            setIsLoading(false)
            const latency = (Date.now() - startTime) / 1000
            const runId = doneData.run_id || currentRunId
            const finalTtft =
              doneData.ttft ?? (firstTokenTime ? (firstTokenTime - startTime) / 1000 : undefined)
            const finalTokPerSec =
              doneData.tok_per_sec ??
              (tokenCount > 0 && firstTokenTime
                ? tokenCount / Math.max(0.05, (Date.now() - firstTokenTime) / 1000)
                : undefined)
            const finalTokenCount = doneData.token_count ?? tokenCount

            let fetchedApproval: ApprovalRequestResponse | null = null
            if (doneData.status === 'needs_approval') {
              showToast('Trợ lý cần bạn xác nhận hành động ghi bảo mật.', 'warning')
              try {
                const pending = await apiClient.getPendingApprovals(runId || undefined)
                fetchedApproval = pickApproval(pending, accumulatedApprovalId, runId)
              } catch (err) {
                console.error('Failed to load pending approval in onDone:', err)
              }
            } else if (doneData.status === 'clarification_needed') {
              showToast('Trợ lý cần bạn làm rõ thêm thông tin.', 'info')
            }

            updateTurn((turn) =>
              turn.result
                ? {
                    ...turn,
                    latency,
                    ttft: finalTtft ?? turn.ttft,
                    tokPerSec: finalTokPerSec ?? turn.tokPerSec,
                    tokenCount: finalTokenCount ?? turn.tokenCount,
                    pendingApproval: fetchedApproval || turn.pendingApproval,
                    result: { ...turn.result, status: doneData.status },
                  }
                : turn
            )
            setIsStreaming(false)

            if (currentConvId) {
              try {
                await apiClient.saveTurn(currentConvId, {
                  userQuery: queryText,
                  assistantResponse: accumulatedMessage,
                  turnId,
                  runId: runId || undefined,
                  metadata: {
                    ttft: finalTtft,
                    latency,
                    tok_per_sec: finalTokPerSec,
                    token_count: finalTokenCount,
                    status: doneData.status,
                    data: accumulatedData,
                    approval_id: fetchedApproval?.id || accumulatedApprovalId,
                  },
                })
                setConversations(await apiClient.listConversations())
              } catch (saveErr) {
                console.error('Failed to persist turn to database:', saveErr)
              }
            }
          },
          onError: (err) => failTurn(err?.message || FAILED_MESSAGE),
        },
        currentConvId || undefined,
        controller.signal
      )
    } catch (err: any) {
      failTurn(err?.name === 'AbortError' ? STOPPED_MESSAGE : err?.message || FAILED_MESSAGE)
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null
        setIsLoading(false)
        setIsStreaming(false)
      }
    }
  }

  return { isLoading, isStreaming, streamQuery, stop }
}
