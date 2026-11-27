/**
 * Typed client for the assistant's FastAPI backend.
 *
 * `ApiCore` (core.ts) owns the session and the JSON request helper; sse.ts reads the streamed
 * endpoints. This class adds one method per backend operation, grouped by domain, and the single
 * `apiClient` instance is what the rest of the app imports.
 */

import type {
  AnswerQuestionRequestBody,
  AnswerQuestionResponse,
  ApprovalRequestResponse,
  ApproveRequestBody,
  ApproveResponse,
  AuthResponse,
  DBConversationDetail,
  DBConversationSummary,
  DenyRequestBody,
  DenyResponse,
  DocumentOutlineColumn,
  DocumentOutlineSection,
  ExecuteApprovalBody,
  GeneratedDocumentSummary,
  GeneratedDocumentView,
  GoogleDisconnectResponse,
  GoogleStartResponse,
  GoogleIntegrationStatus,
  HealthResponse,
  MemoryView,
  TaskItem,
  TaskProposalRequest,
  TaskProposalResponse,
  TelegramLinkCode,
  TelegramStatus,
  NotificationListResponse,
  NotificationReadResponse,
  QueryRequest,
  QueryResult,
  ReadinessResponse,
  UserProfile,
  UserQuestionAnswer,
} from '../types/api'
import { ApiCore } from './core'
import { streamNotifications, streamQuery, type QueryStreamCallbacks } from './sse'

export type { QueryStreamCallbacks } from './sse'

class ApiClient extends ApiCore {
  /* 1. Natural Language Query */
  async submitQuery(query: string): Promise<QueryResult> {
    const payload: QueryRequest = { query }
    return this.request<QueryResult>('/query', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  /* 1b. Natural Language Query with SSE Streaming */
  async submitQueryStream(
    query: string,
    callbacks: QueryStreamCallbacks,
    conversationId?: string,
    signal?: AbortSignal
  ): Promise<void> {
    const payload: QueryRequest & { conversation_id?: string } = {
      query,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }
    return streamQuery(this.getHeaders(), payload, callbacks, signal)
  }

  /* 1c. Generated documents */
  async listGeneratedDocuments(): Promise<GeneratedDocumentSummary[]> {
    return this.request<GeneratedDocumentSummary[]>('/documents')
  }

  async createDocumentOutline(request: string): Promise<GeneratedDocumentView> {
    return this.request<GeneratedDocumentView>('/documents/outline', {
      method: 'POST',
      body: JSON.stringify({ request }),
    })
  }

  async getGeneratedDocument(id: string): Promise<GeneratedDocumentView> {
    return this.request<GeneratedDocumentView>(`/documents/${encodeURIComponent(id)}`)
  }

  async approveDocument(
    id: string,
    edits: {
      title?: string
      sections?: DocumentOutlineSection[]
      columns?: DocumentOutlineColumn[]
      row_unit?: string
      multi_rows?: boolean
    }
  ): Promise<GeneratedDocumentView> {
    return this.request<GeneratedDocumentView>(`/documents/${encodeURIComponent(id)}/approve`, {
      method: 'POST',
      body: JSON.stringify(edits),
    })
  }

  async rewriteDocumentSection(
    id: string,
    index: number,
    feedback: string
  ): Promise<GeneratedDocumentView> {
    return this.request<GeneratedDocumentView>(
      `/documents/${encodeURIComponent(id)}/sections/${index}/rewrite`,
      { method: 'POST', body: JSON.stringify({ feedback }) }
    )
  }

  /* .docx download needs the auth header, so no plain link */
  async downloadGeneratedDocument(id: string, title?: string, ext: string = 'docx'): Promise<void> {
    const blob = await this.fetchBlob(`/documents/${encodeURIComponent(id)}/download`)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${(title || 'tai-lieu').replace(/[\\/:*?"<>|]+/g, ' ').trim()}.${ext}`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  }

  /* Document images need the auth header too: fetched as an object URL (caller revokes it) */
  async fetchDocumentImage(key: string): Promise<string> {
    return URL.createObjectURL(await this.fetchBlob(`/documents/images/${encodeURIComponent(key)}`))
  }

  /* 2. Approvals (Human-in-the-Loop) */
  async getPendingApprovals(runId?: string): Promise<ApprovalRequestResponse[]> {
    const queryParam = runId ? `?run_id=${encodeURIComponent(runId)}` : ''
    return this.request<ApprovalRequestResponse[]>(`/approvals/pending${queryParam}`)
  }

  async approveAction(id: string, reason?: string, execute: boolean = true): Promise<ApproveResponse> {
    const body: ApproveRequestBody = { reason, execute }
    return this.request<ApproveResponse>(`/approvals/${encodeURIComponent(id)}/approve`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  async denyAction(
    id: string,
    reason?: string,
    outcome: 'rejected' | 'cancelled' | 'unavailable' = 'rejected'
  ): Promise<DenyResponse> {
    const body: DenyRequestBody = { reason, outcome }
    return this.request<DenyResponse>(`/approvals/${encodeURIComponent(id)}/deny`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  async executeApprovedAction(id: string, token: string): Promise<ApproveResponse> {
    const body: ExecuteApprovalBody = { token }
    return this.request<ApproveResponse>(`/approvals/${encodeURIComponent(id)}/execute`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  /* 3. Question Plane (Clarification) */
  async answerQuestion(
    questionId: string,
    answers: UserQuestionAnswer[],
    expectedVersion: number = 1
  ): Promise<AnswerQuestionResponse> {
    const body: AnswerQuestionRequestBody = {
      answers,
      expected_version: expectedVersion,
    }
    return this.request<AnswerQuestionResponse>(`/questions/${encodeURIComponent(questionId)}/answer`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  /* 4. Google Workspace Integration */
  /** Start Google OAuth with the session header (never a token in the URL), then open Google's consent page. */
  async startGoogleConnect(): Promise<void> {
    const { authorization_url } = await this.request<GoogleStartResponse>('/auth/google/start', {
      method: 'POST',
    })
    window.location.assign(authorization_url)
  }

  async getGoogleStatus(): Promise<GoogleIntegrationStatus> {
    return this.request<GoogleIntegrationStatus>('/auth/google/status')
  }

  async disconnectGoogle(): Promise<GoogleDisconnectResponse> {
    return this.request<GoogleDisconnectResponse>('/auth/google/disconnect', {
      method: 'POST',
    })
  }

  /* Google Tasks */
  async listTasks(): Promise<TaskItem[]> {
    return this.request<TaskItem[]>('/tasks')
  }

  /** Create the approval for adding or completing a task; nothing is written until approved. */
  async proposeTask(body: TaskProposalRequest): Promise<TaskProposalResponse> {
    return this.request<TaskProposalResponse>('/tasks/propose', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  /* Telegram */
  async getTelegramStatus(): Promise<TelegramStatus> {
    return this.request<TelegramStatus>('/telegram/status')
  }

  async createTelegramLinkCode(): Promise<TelegramLinkCode> {
    return this.request<TelegramLinkCode>('/telegram/link-code', { method: 'POST' })
  }

  async unlinkTelegram(): Promise<TelegramStatus> {
    return this.request<TelegramStatus>('/telegram/link', { method: 'DELETE' })
  }

  /* Long-term memory */
  async listMemories(): Promise<MemoryView[]> {
    return this.request<MemoryView[]>('/memories')
  }

  async createMemory(content: string): Promise<MemoryView> {
    return this.request<MemoryView>('/memories', {
      method: 'POST',
      body: JSON.stringify({ content }),
    })
  }

  async updateMemory(memoryId: string, content: string): Promise<MemoryView> {
    return this.request<MemoryView>(`/memories/${encodeURIComponent(memoryId)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    })
  }

  async deleteMemory(memoryId: string): Promise<void> {
    await this.request<unknown>(`/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' })
  }

  /* Proactive notifications (morning briefing, meeting dossiers) */
  async getNotifications(limit: number = 20): Promise<NotificationListResponse> {
    return this.request<NotificationListResponse>(`/notifications?limit=${limit}`)
  }

  async markNotificationRead(notificationId: string): Promise<NotificationReadResponse> {
    return this.request<NotificationReadResponse>(
      `/notifications/${encodeURIComponent(notificationId)}/read`,
      { method: 'POST' }
    )
  }

  async markAllNotificationsRead(): Promise<NotificationReadResponse> {
    return this.request<NotificationReadResponse>('/notifications/read-all', { method: 'POST' })
  }

  /**
   * Hold the live notification stream open until it ends or `signal` aborts; calls
   * `onNotification` for every new notification id. Resolves `false` when the server
   * reports live delivery unavailable, so the caller can fall back to polling.
   */
  async streamNotifications(
    onNotification: (id: string) => void,
    signal: AbortSignal
  ): Promise<boolean> {
    return streamNotifications(this.getHeaders(), onNotification, signal)
  }

  /* 5. Health & Diagnostics */
  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health')
  }

  async getReadiness(): Promise<ReadinessResponse> {
    return this.request<ReadinessResponse>('/ready')
  }

  /* 6. User Authentication */
  async register(email: string, password: string, fullName?: string): Promise<AuthResponse> {
    const resp = await this.request<AuthResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, full_name: fullName || null }),
    })
    if (resp.access_token) {
      this.setToken(resp.access_token)
      if (resp.user.full_name) this.setDisplayName(resp.user.full_name)
      else if (resp.user.email) this.setDisplayName(resp.user.email.split('@')[0])
      this.setUserId(resp.user.id)
    }
    return resp
  }

  async login(email: string, password: string): Promise<AuthResponse> {
    const resp = await this.request<AuthResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    if (resp.access_token) {
      this.setToken(resp.access_token)
      if (resp.user.full_name) this.setDisplayName(resp.user.full_name)
      else if (resp.user.email) this.setDisplayName(resp.user.email.split('@')[0])
      this.setUserId(resp.user.id)
    }
    return resp
  }

  async getMe(): Promise<UserProfile> {
    return this.request<UserProfile>('/auth/me')
  }

  logout() {
    this.clearSession()
  }

  /* 7. Conversations & Messages Persistence */
  async listConversations(): Promise<DBConversationSummary[]> {
    return this.request<DBConversationSummary[]>('/conversations')
  }

  async createConversation(title?: string): Promise<DBConversationSummary> {
    return this.request<DBConversationSummary>('/conversations', {
      method: 'POST',
      body: JSON.stringify({ title: title || null }),
    })
  }

  async getConversation(id: string): Promise<DBConversationDetail> {
    return this.request<DBConversationDetail>(`/conversations/${encodeURIComponent(id)}`)
  }

  async updateConversationTitle(id: string, title: string): Promise<DBConversationSummary> {
    return this.request<DBConversationSummary>(`/conversations/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    })
  }

  async deleteConversation(id: string): Promise<void> {
    await this.request<void>(`/conversations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
  }

  async saveTurn(
    conversationId: string,
    turn: {
      userQuery: string
      assistantResponse: string
      turnId?: string
      runId?: string
      metadata?: Record<string, any>
    }
  ): Promise<void> {
    await this.request<void>(`/conversations/${encodeURIComponent(conversationId)}/turn`, {
      method: 'POST',
      body: JSON.stringify({
        user_query: turn.userQuery,
        assistant_response: turn.assistantResponse,
        turn_id: turn.turnId || null,
        run_id: turn.runId || null,
        metadata: turn.metadata || {},
      }),
    })
  }
}

export const apiClient = new ApiClient()
