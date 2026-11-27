/**
 * Session state and the JSON request helper shared by every API domain.
 *
 * The session (bearer token, or the legacy user id / API key pair) is mirrored in localStorage so a
 * reload stays signed in. Errors are turned into Vietnamese messages, preferring the server's own
 * `error.message` / `detail` text when the response carries one.
 */

import type { AppErrorEnvelope } from '../types/api'

const TOKEN_KEY = 'personal_ai_auth_token'
const USER_ID_KEY = 'personal_ai_user_id'
const API_KEY_KEY = 'personal_ai_api_key'
const DISPLAY_NAME_KEY = 'personal_ai_display_name'
const DEFAULT_USER_ID = 'default-user'
const DEFAULT_DISPLAY_NAME = 'namm'

export const CONNECTION_ERROR_MESSAGE =
  'Không thể kết nối đến máy chủ. Vui lòng kiểm tra dịch vụ backend.'

/** Read the server's error text from a failed response, or fall back to a generic Vietnamese message. */
export async function readErrorMessage(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as AppErrorEnvelope | { detail?: unknown }
    if ('error' in data && data.error?.message) return data.error.message
    if ('detail' in data && typeof data.detail === 'string') return data.detail
  } catch {
    // The body is not JSON; the generic message below is used.
  }
  return `Máy chủ trả về lỗi ${response.status}. Vui lòng thử lại.`
}

/** Build an `Error` carrying the HTTP status from a failed response. */
export async function errorFromResponse(response: Response): Promise<Error & { status?: number }> {
  const error = new Error(await readErrorMessage(response)) as Error & { status?: number }
  error.status = response.status
  return error
}

/** Holds the signed-in session and performs authenticated JSON requests. */
export class ApiCore {
  private userId: string = DEFAULT_USER_ID
  private apiKey: string = ''
  private displayName: string = DEFAULT_DISPLAY_NAME
  private token: string | null = null

  constructor() {
    this.token = localStorage.getItem(TOKEN_KEY)
    const storedUser = localStorage.getItem(USER_ID_KEY)
    const storedKey = localStorage.getItem(API_KEY_KEY)
    const storedDisplayName = localStorage.getItem(DISPLAY_NAME_KEY)

    if (storedDisplayName) {
      this.displayName = storedDisplayName
    } else if (storedUser && storedUser !== DEFAULT_USER_ID) {
      this.displayName = storedUser
    }

    if (storedKey) {
      this.apiKey = storedKey
      if (storedUser) this.userId = storedUser
    } else {
      this.userId = storedUser || DEFAULT_USER_ID
    }
  }

  setToken(token: string | null) {
    this.token = token
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }

  getToken(): string | null {
    return this.token
  }

  setDisplayName(name: string) {
    this.displayName = name
    localStorage.setItem(DISPLAY_NAME_KEY, name)
  }

  getDisplayName(): string {
    return this.displayName || DEFAULT_DISPLAY_NAME
  }

  setUserId(id: string) {
    this.userId = id
    localStorage.setItem(USER_ID_KEY, id)
  }

  getUserId(): string {
    return this.userId
  }

  setApiKey(key: string) {
    this.apiKey = key
    localStorage.setItem(API_KEY_KEY, key)
  }

  getApiKey(): string {
    return this.apiKey
  }

  /** Forget the whole session: token, API key and user id. */
  clearSession() {
    this.setToken(null)
    this.apiKey = ''
    this.userId = DEFAULT_USER_ID
    localStorage.removeItem(API_KEY_KEY)
    localStorage.removeItem(USER_ID_KEY)
  }

  getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`
    } else {
      headers['X-User-ID'] = this.userId || DEFAULT_USER_ID
    }
    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey
    }
    return headers
  }

  /** Send an authenticated request and parse the JSON reply; a 204 resolves to `undefined`. */
  protected async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const mergedOptions: RequestInit = {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...(options.headers || {}),
      },
    }

    try {
      const response = await fetch(path, mergedOptions)
      if (!response.ok) throw await errorFromResponse(response)
      if (response.status === 204) return undefined as T
      return (await response.json()) as T
    } catch (err: any) {
      if (err?.name === 'TypeError' && err.message?.includes('fetch')) {
        throw new Error(CONNECTION_ERROR_MESSAGE)
      }
      throw err
    }
  }

  /** Fetch a binary resource with the session headers and return it as a blob. */
  protected async fetchBlob(path: string): Promise<Blob> {
    const response = await fetch(path, { headers: this.getHeaders() })
    if (!response.ok) throw await errorFromResponse(response)
    return response.blob()
  }
}
