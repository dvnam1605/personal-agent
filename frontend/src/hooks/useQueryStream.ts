import { useState } from 'react'
import type React from 'react'
import { apiClient } from '../api/client'
import type { ChatTurn } from '../components/conversation/MessageItem'
import type { ToastType } from '../components/ui/Toast'
import type { ApprovalRequestResponse, DBConversationSummary } from '../types/api'

interface UseQueryStreamOptions {
  setTurns: React.Dispatch<React.SetStateAction<ChatTurn[]>>
  setConversations: React.Dispatch<React.SetStateAction<DBConversationSummary[]>>
  showToast: (message: string, type?: ToastType) => void
}

/** Streams one query into a new turn and persists the finished turn to the conversation. */
export function useQueryStream({ setTurns, setConversations, showToast }: UseQueryStreamOptions) {
  const [isLoading, setIsLoading] = useState(false)

  const streamQuery = async (queryText: string, currentConvId: string | null) => {
    const turnId = Math.random().toString(36).substring(2, 9)
    const timestamp = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })

    const newTurn: ChatTurn = {
      id: turnId,
      userQuery: queryText,
      timestamp,
    }

    setTurns((prev) => [...prev, newTurn])
    setIsLoading(true)

    const startTime = Date.now()
    let firstTokenReceived = false
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
            setTurns((prev) =>
              prev.map((turn) =>
                turn.id === turnId
                  ? {
                      ...turn,
                      result: {
                        run_id: data.run_id,
                        status: data.status,
                        message: '',
                        route: data.route,
                        data: {},
                      },
                    }
                  : turn
              )
            )
          },
          onToken: (delta) => {
            const now = Date.now()
            if (!firstTokenReceived) {
              firstTokenReceived = true
              firstTokenTime = now
              setIsLoading(false)
            }
            const words = delta.trim() ? delta.trim().split(/\s+/).length : 0
            tokenCount += Math.max(1, words)
            const streamDurationSec = Math.max(0.05, (now - (firstTokenTime || now)) / 1000)
            const runningTokPerSec = tokenCount / streamDurationSec
            accumulatedMessage += delta

            setTurns((prev) =>
              prev.map((turn) => {
                if (turn.id === turnId) {
                  const existing = turn.result?.message || ''
                  const ttft =
                    turn.ttft ??
                    (firstTokenTime ? (firstTokenTime - startTime) / 1000 : (now - startTime) / 1000)
                  return {
                    ...turn,
                    ttft,
                    tokPerSec: runningTokPerSec,
                    tokenCount,
                    result: {
                      ...(turn.result || {
                        run_id: 'pending',
                        status: 'processing',
                        route: { route_type: 'direct_specialist', confidence: 1, domains: [] },
                        data: {},
                      }),
                      message: existing + delta,
                    },
                  }
                }
                return turn
              })
            )
          },
          onCitations: async (citeData) => {
            if (citeData.answer) accumulatedMessage = citeData.answer
            let pendingApproval: ApprovalRequestResponse | null = null
            const approvalId = citeData.approval_id
            if (approvalId) {
              accumulatedApprovalId = approvalId
              try {
                const pending = await apiClient.getPendingApprovals(currentRunId || undefined)
                pendingApproval =
                  pending.find((p) => p.id === approvalId) ||
                  pending.find((p) => p.run_id === currentRunId) ||
                  pending[0] ||
                  null
              } catch (err) {
                console.error('Failed to load pending approval in onCitations:', err)
              }
            }

            setTurns((prev) =>
              prev.map((turn) => {
                if (turn.id === turnId && turn.result) {
                  const mergedData = {
                    ...(turn.result.data || {}),
                    ...(citeData.data || {}),
                  }
                  if (citeData.citations) {
                    mergedData.citations = citeData.citations
                  }
                  if (citeData.sufficiency) {
                    mergedData.sufficiency = citeData.sufficiency
                  }
                  accumulatedData = mergedData
                  return {
                    ...turn,
                    result: {
                      ...turn.result,
                      message: citeData.answer || turn.result.message,
                      data: mergedData,
                      approval_id: citeData.approval_id || turn.result.approval_id,
                    },
                    pendingApproval: pendingApproval || turn.pendingApproval,
                  }
                }
                return turn
              })
            )
          },
          onDone: async (doneData) => {
            setIsLoading(false)
            const latency = (Date.now() - startTime) / 1000
            const runId = doneData.run_id || currentRunId
            const finalTtft = doneData.ttft ?? (firstTokenTime ? (firstTokenTime - startTime) / 1000 : undefined)
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
                fetchedApproval =
                  pending.find((p) => p.run_id === runId) ||
                  pending[0] ||
                  null
              } catch (err) {
                console.error('Failed to load pending approval in onDone:', err)
              }
            } else if (doneData.status === 'clarification_needed') {
              showToast('Trợ lý cần bạn làm rõ thêm thông tin.', 'info')
            }

            setTurns((prev) =>
              prev.map((turn) => {
                if (turn.id === turnId && turn.result) {
                  return {
                    ...turn,
                    latency,
                    ttft: finalTtft ?? turn.ttft,
                    tokPerSec: finalTokPerSec ?? turn.tokPerSec,
                    tokenCount: finalTokenCount ?? turn.tokenCount,
                    pendingApproval: fetchedApproval || turn.pendingApproval,
                    result: {
                      ...turn.result,
                      status: doneData.status,
                    },
                  }
                }
                return turn
              })
            )

            // Persist the complete turn into the database
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
                // Refresh list so updated_at & title changes reflect in sidebar
                const convList = await apiClient.listConversations()
                setConversations(convList)
              } catch (saveErr) {
                console.error('Failed to persist turn to database:', saveErr)
              }
            }
          },
          onError: (err) => {
            const errMsg = err?.message || 'Không thể xử lý yêu cầu.'
            setTurns((prev) =>
              prev.map((turn) =>
                turn.id === turnId
                  ? {
                      ...turn,
                      error: errMsg,
                    }
                  : turn
              )
            )
          },
        },
        currentConvId || undefined
      )
    } catch (err: any) {
      const errMsg = err?.message || 'Không thể xử lý yêu cầu.'
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId
            ? {
                ...turn,
                error: errMsg,
              }
            : turn
        )
      )
    } finally {
      setIsLoading(false)
    }
  }

  return { isLoading, streamQuery }
}
