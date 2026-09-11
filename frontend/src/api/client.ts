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
  DenyRequestBody,
  DenyResponse,
  ExecuteApprovalBody,
  GoogleDisconnectResponse,
  GoogleIntegrationStatus,
  HealthResponse,
  QueryRequest,
  QueryResult,
  ReadinessResponse,
  UserQuestionAnswer,
} from '../types/api'

class ApiClient {
  private userId: string = 'default-user'
  private apiKey: string = ''
  private displayName: string = 'namm'

  constructor() {
    // Read optional overrides from localStorage
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
      // In local dev without an API key, the backend strictly requires 'default-user'
      this.userId = 'default-user'
    }
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
      // In development without an API key, the backend requires 'default-user' to prevent 401 UNAUTHENTICATED
      'X-User-ID': this.apiKey ? this.userId : 'default-user',
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
}

export const apiClient = new ApiClient()
