import { useState, useCallback } from 'react'
import { apiClient } from '../api/client'
import type { ChatTurn } from '../components/conversation/MessageItem'
import type { ToastType } from '../components/ui/toastContext'
import type { DBConversationSummary, DBMessageItem } from '../types/api'

function dbMessagesToTurns(messages: DBMessageItem[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  let pendingUserMsg: DBMessageItem | null = null

  for (const msg of messages) {
    if (msg.role === 'user') {
      if (pendingUserMsg) {
        turns.push({
          id: (pendingUserMsg.metadata?.turn_id as string) || pendingUserMsg.id,
          userQuery: pendingUserMsg.content,
          timestamp: new Date(pendingUserMsg.created_at).toLocaleTimeString('vi-VN', {
            hour: '2-digit',
            minute: '2-digit',
          }),
        })
      }
      pendingUserMsg = msg
    } else if (msg.role === 'assistant') {
      const meta = msg.metadata || {}
      const timeStr = new Date(msg.created_at).toLocaleTimeString('vi-VN', {
        hour: '2-digit',
        minute: '2-digit',
      })
      const turnId =
        (meta.turn_id as string) ||
        (pendingUserMsg ? (pendingUserMsg.metadata?.turn_id as string) || pendingUserMsg.id : msg.id)
      const userQuery = pendingUserMsg ? pendingUserMsg.content : ''

      turns.push({
        id: turnId,
        userQuery,
        timestamp: timeStr,
        ttft: meta.ttft,
        latency: meta.latency,
        tokPerSec: meta.tok_per_sec,
        tokenCount: meta.token_count,
        result: {
          run_id: (meta.run_id as string) || 'db_saved',
          status: (meta.status as any) || 'completed',
          message: msg.content,
          route: meta.route || { route_type: 'direct_specialist', confidence: 1, domains: [] },
          data: meta.data || {},
          approval_id: meta.approval_id || null,
        },
      })
      pendingUserMsg = null
    }
  }

  if (pendingUserMsg) {
    turns.push({
      id: (pendingUserMsg.metadata?.turn_id as string) || pendingUserMsg.id,
      userQuery: pendingUserMsg.content,
      timestamp: new Date(pendingUserMsg.created_at).toLocaleTimeString('vi-VN', {
        hour: '2-digit',
        minute: '2-digit',
      }),
    })
  }

  return turns
}

/** Owns the database-backed conversation list, the active conversation and its turns. */
export function useConversations(showToast: (message: string, type?: ToastType) => void) {
  const [conversations, setConversations] = useState<DBConversationSummary[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])

  const loadConversation = useCallback(
    async (conversationId: string) => {
      try {
        const detail = await apiClient.getConversation(conversationId)
        const loadedTurns = dbMessagesToTurns(detail.messages)
        setTurns(loadedTurns)
      } catch (err) {
        console.error('Failed to load conversation:', err)
        showToast('Không thể tải lịch sử cuộc trò chuyện này.', 'error')
      }
    },
    [showToast],
  )

  return {
    conversations,
    setConversations,
    activeConversationId,
    setActiveConversationId,
    turns,
    setTurns,
    loadConversation,
  }
}
