/**
 * Typed API Client for Personal AI Assistant FastAPI Backend.
 */

import type {
  AnswerQuestionRequestBody,
  AnswerQuestionResponse,
  AppErrorEnvelope,
  ApprovalRequestResponse,
  ApproveRequestBody,
  ApproveResponse,
  AuthResponse,
  DBConversationDetail,
  DBConversationSummary,
  DenyRequestBody,
  DenyResponse,
  ExecuteApprovalBody,
  GoogleDisconnectResponse,
  GoogleIntegrationStatus,
  HealthResponse,
  QueryRequest,
  QueryResult,
  ReadinessResponse,
  UserProfile,
  UserQuestionAnswer,
} from '../types/api'

class ApiClient {
  private userId: string = 'default-user'
  private apiKey: string = ''
  private displayName: string = 'namm'
  private token: string | null = null

  constructor() {
    // Read optional overrides and auth token from localStorage
    this.token = localStorage.getItem('personal_ai_auth_token')
    const storedUser = localStorage.getItem('personal_ai_user_id')
    const storedKey = localStorage.getItem('personal_ai_api_key')
    const storedDisplayName = localStorage.getItem('personal_ai_display_name')

    if (storedDisplayName) {
      this.displayName = storedDisplayName
    } else if (storedUser && storedUser !== 'default-user') {
      this.displayName = storedUser
    }

    if (storedKey) {
      this.apiKey = storedKey
      if (storedUser) this.userId = storedUser
    } else {
      this.userId = storedUser || 'default-user'
    }
  }

  setToken(token: string | null) {
    this.token = token
    if (token) {
      localStorage.setItem('personal_ai_auth_token', token)
    } else {
      localStorage.removeItem('personal_ai_auth_token')
    }
  }

  getToken(): string | null {
    return this.token
  }

  setDisplayName(name: string) {
    this.displayName = name
    localStorage.setItem('personal_ai_display_name', name)
  }

  getDisplayName(): string {
    return this.displayName || 'namm'
  }

  setUserId(id: string) {
    this.userId = id
    localStorage.setItem('personal_ai_user_id', id)
  }

  getUserId(): string {
    return this.userId
  }

  setApiKey(key: string) {
    this.apiKey = key
    localStorage.setItem('personal_ai_api_key', key)
  }

  getApiKey(): string {
    return this.apiKey
  }

  private getHeaders(): HeadersInit {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`
    } else {
      headers['X-User-ID'] = this.userId || 'default-user'
    }
    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey
    }
    return headers
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = path
    const mergedOptions: RequestInit = {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...(options.headers || {}),
      },
    }

    try {
      const response = await fetch(url, mergedOptions)

      if (!response.ok) {
        let errorMessage = `HTTP Error ${response.status}: ${response.statusText}`
        try {
          const errorData: AppErrorEnvelope | { detail?: string } = await response.json()
          if ('error' in errorData && errorData.error?.message) {
            errorMessage = errorData.error.message
          } else if ('detail' in errorData && typeof errorData.detail === 'string') {
            errorMessage = errorData.detail
          }
        } catch {
          // If response is not JSON, retain default error message
        }

        const error = new Error(errorMessage) as Error & { status?: number }
        error.status = response.status
        throw error
      }

      return (await response.json()) as T
    } catch (err: any) {
      if (err.name === 'TypeError' && err.message?.includes('fetch')) {
        throw new Error('Không thể kết nối đến máy chủ backend (127.0.0.1:8000). Vui lòng kiểm tra dịch vụ.')
      }
      throw err
    }
  }

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
    callbacks: {
      onMetadata?: (data: { run_id: string; status: string; route: any }) => void
      onToken?: (delta: string) => void
      onCitations?: (data: { citations?: any[]; sufficiency?: string; data?: any; approval_id?: string }) => void
      onError?: (err: Error) => void
      onDone?: (data: {
        run_id: string
        status: string
        ttft?: number
        token_count?: number
        duration?: number
        tok_per_sec?: number
      }) => void
    },
    conversationId?: string
  ): Promise<void> {
    const payload: QueryRequest & { conversation_id?: string } = {
      query,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }
    const url = '/query/stream'
    const headers = {
      ...this.getHeaders(),
      Accept: 'text/event-stream',
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      })

      if (!response.ok) {
        let errorMessage = `HTTP Error ${response.status}: ${response.statusText}`
        try {
          const errorData = await response.json()
          if (errorData?.error?.message) {
            errorMessage = errorData.error.message
          }
        } catch {
          // keep default
        }
        throw new Error(errorMessage)
      }

      if (!response.body) {
        throw new Error('ReadableStream not supported by response.')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      let currentEvent = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) {
            currentEvent = ''
            continue
          }
          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.slice(6).trim()
          } else if (trimmed.startsWith('data:')) {
            const dataStr = trimmed.slice(5).trim()
            try {
              const data = JSON.parse(dataStr)
              if (currentEvent === 'metadata' && callbacks.onMetadata) {
                callbacks.onMetadata(data)
              } else if (currentEvent === 'token' && callbacks.onToken) {
                if (data.delta) callbacks.onToken(data.delta)
              } else if (currentEvent === 'citations' && callbacks.onCitations) {
                callbacks.onCitations(data)
              } else if (currentEvent === 'done' && callbacks.onDone) {
                callbacks.onDone(data)
              } else if (currentEvent === 'error' && callbacks.onError) {
                callbacks.onError(new Error(data.message || 'Lỗi streaming'))
              }
            } catch {
              // ignore malformed JSON chunk
            }
          }
        }
      }
    } catch (err: any) {
      if (callbacks.onError) {
        callbacks.onError(err)
      } else {
        throw err
      }
    }
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
  getGoogleConnectUrl(): string {
    const token = this.getToken()
    if (token) {
      return `/auth/google/start?token=${encodeURIComponent(token)}`
    }
    return '/auth/google/start'
  }

  async getGoogleStatus(): Promise<GoogleIntegrationStatus> {
    return this.request<GoogleIntegrationStatus>('/auth/google/status')
  }

  async disconnectGoogle(): Promise<GoogleDisconnectResponse> {
    return this.request<GoogleDisconnectResponse>('/auth/google/disconnect', {
      method: 'POST',
    })
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
    this.setToken(null)
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
