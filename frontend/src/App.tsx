import React, { useState, useEffect } from 'react'
import { Header, type WorkspaceType } from './components/layout/Header'
import { Sidebar } from './components/layout/Sidebar'
import { AssistantWorkspace } from './components/workspaces/AssistantWorkspace'
import { CalendarWorkspace } from './components/workspaces/CalendarWorkspace'
import { EmailWorkspace } from './components/workspaces/EmailWorkspace'
import { MeetingsWorkspace } from './components/workspaces/MeetingsWorkspace'
import { KnowledgeWorkspace } from './components/workspaces/KnowledgeWorkspace'
import { SettingsWorkspace } from './components/workspaces/SettingsWorkspace'
import { Drawer } from './components/ui/Drawer'
import { ToastProvider, useToast } from './components/ui/Toast'
import { AuthModal } from './components/auth/AuthModal'
import { apiClient } from './api/client'
import type { ChatTurn } from './components/conversation/MessageItem'
import type {
  ApprovalRequestResponse,
  GmailMessage,
  RAGCitation,
  UserProfile,
  DBConversationSummary,
  DBMessageItem,
} from './types/api'
import { FileText, Mail, ExternalLink } from 'lucide-react'
import './App.css'

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

const AppContent: React.FC = () => {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceType>('assistant')
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)

  // User authentication
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null)
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false)

  // Database-backed conversations
  const [conversations, setConversations] = useState<DBConversationSummary[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])

  const [isLoading, setIsLoading] = useState(false)
  const [selectedCitation, setSelectedCitation] = useState<RAGCitation | null>(null)
  const [selectedEmail, setSelectedEmail] = useState<GmailMessage | null>(null)
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const saved = localStorage.getItem('personal_ai_theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  })

  const { showToast } = useToast()

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('personal_ai_theme', theme)
  }, [theme])

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }

  // Load conversation details
  const loadConversation = async (conversationId: string) => {
    try {
      const detail = await apiClient.getConversation(conversationId)
      const loadedTurns = dbMessagesToTurns(detail.messages)
      setTurns(loadedTurns)
    } catch (err) {
      console.error('Failed to load conversation:', err)
      showToast('Không thể tải lịch sử cuộc trò chuyện này.', 'error')
    }
  }

  // Check auth & fetch user conversations on initial load
  useEffect(() => {
    const initAuth = async () => {
      const token = apiClient.getToken()
      if (!token) {
        setIsAuthModalOpen(true)
        return
      }

      try {
        const user = await apiClient.getMe()
        setCurrentUser(user)
        const convList = await apiClient.listConversations()
        setConversations(convList)
        if (convList.length > 0) {
          setActiveConversationId(convList[0].id)
          loadConversation(convList[0].id)
        }
      } catch (err) {
        console.warn('Authentication token invalid or expired:', err)
        apiClient.logout()
        setCurrentUser(null)
        setIsAuthModalOpen(true)
      }
    }

    initAuth()
  }, [])

  const handleSelectSession = (sessionId: string) => {
    if (sessionId === activeConversationId) return
    setActiveConversationId(sessionId)
    setActiveWorkspace('assistant')
    loadConversation(sessionId)
  }

  const handleNewSession = async () => {
    if (!currentUser) {
      setIsAuthModalOpen(true)
      return
    }

    // If currently on a brand-new empty session, keep it
    if (turns.length === 0 && activeConversationId) {
      setActiveWorkspace('assistant')
      return
    }

    try {
      const newConv = await apiClient.createConversation('Cuộc trò chuyện mới')
      setConversations((prev) => [newConv, ...prev])
      setActiveConversationId(newConv.id)
      setTurns([])
      setActiveWorkspace('assistant')
      showToast('Đã mở cuộc trò chuyện mới.', 'info')
    } catch (err: any) {
      console.error('Failed to create new conversation:', err)
      showToast('Không thể tạo cuộc trò chuyện mới.', 'error')
    }
  }

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await apiClient.deleteConversation(sessionId)
      const remaining = conversations.filter((c) => c.id !== sessionId)
      setConversations(remaining)
      if (activeConversationId === sessionId) {
        if (remaining.length > 0) {
          setActiveConversationId(remaining[0].id)
          loadConversation(remaining[0].id)
        } else {
          setActiveConversationId(null)
          setTurns([])
        }
      }
      showToast('Đã xóa cuộc trò chuyện.', 'info')
    } catch (err: any) {
      console.error('Failed to delete conversation:', err)
      showToast('Lỗi khi xóa cuộc trò chuyện.', 'error')
    }
  }

  const handleAuthSuccess = async (user: UserProfile) => {
    setCurrentUser(user)
    setIsAuthModalOpen(false)
    showToast(`Chào mừng, ${user.full_name || user.email}!`, 'success')

    try {
      const convList = await apiClient.listConversations()
      setConversations(convList)
      if (convList.length > 0) {
        setActiveConversationId(convList[0].id)
        loadConversation(convList[0].id)
      } else {
        setActiveConversationId(null)
        setTurns([])
      }
    } catch (err) {
      console.error('Failed to load conversations after auth:', err)
    }
  }

  const handleLogout = () => {
    apiClient.logout()
    setCurrentUser(null)
    setConversations([])
    setActiveConversationId(null)
    setTurns([])
    showToast('Đã đăng xuất tài khoản.', 'info')
  }

  const handleSendMessage = async (queryText: string) => {
    if (!queryText.trim() || isLoading) return

    if (!currentUser) {
      setIsAuthModalOpen(true)
      showToast('Vui lòng đăng nhập để lưu trữ hội thoại của bạn.', 'info')
      return
    }

    // Ensure we are in assistant workspace when sending messages
    setActiveWorkspace('assistant')

    let currentConvId = activeConversationId
    if (!currentConvId) {
      try {
        const previewTitle =
          queryText.length > 36 ? queryText.slice(0, 36).trim() + '...' : queryText.trim()
        const newConv = await apiClient.createConversation(previewTitle)
        currentConvId = newConv.id
        setActiveConversationId(newConv.id)
        setConversations((prev) => [newConv, ...prev])
      } catch (err) {
        console.error('Failed to auto-create conversation:', err)
      }
    }

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

  const handleApproveAction = async (id: string, execute: boolean = true) => {
    try {
      const resp = await apiClient.approveAction(id, undefined, execute)
      if (resp.executed || resp.approved) {
        showToast('Đã phê duyệt và thực thi hành động thành công trên Google!', 'success')
      } else {
        showToast('Đã phê duyệt hành động (chờ thực thi).', 'info')
      }

      setTurns((prev) =>
        prev.map((turn) => {
          if (turn.pendingApproval?.id === id) {
            return {
              ...turn,
              pendingApproval: {
                ...turn.pendingApproval,
                status: 'approved',
                approved: true,
              },
            }
          }
          return turn
        })
      )
    } catch (err: any) {
      showToast(err?.message || 'Lỗi khi phê duyệt hành động.', 'error')
      throw err
    }
  }

  const handleDenyAction = async (id: string, reason?: string) => {
    try {
      await apiClient.denyAction(id, reason)
      showToast('Đã từ chối yêu cầu.', 'info')

      setTurns((prev) =>
        prev.map((turn) => {
          if (turn.pendingApproval?.id === id) {
            return {
              ...turn,
              pendingApproval: {
                ...turn.pendingApproval,
                status: 'rejected',
                approved: false,
              },
            }
          }
          return turn
        })
      )
    } catch (err: any) {
      showToast(err?.message || 'Lỗi khi từ chối yêu cầu.', 'error')
      throw err
    }
  }

  return (
    <div className="executive-app">
      <Header
        onToggleSidebar={() => {
          if (window.innerWidth >= 1024) {
            setIsSidebarCollapsed((prev) => !prev)
          } else {
            setIsSidebarOpen((prev) => !prev)
          }
        }}
        isSidebarOpen={isSidebarOpen}
        activeWorkspace={activeWorkspace}
        onNavigate={(ws) => setActiveWorkspace(ws)}
        theme={theme}
        onToggleTheme={toggleTheme}
        currentUser={currentUser}
        onOpenAuth={() => setIsAuthModalOpen(true)}
        onLogout={handleLogout}
      />

      <div className="executive-app__body">
        <Sidebar
          isOpen={isSidebarOpen}
          onClose={() => setIsSidebarOpen(false)}
          isCollapsed={isSidebarCollapsed}
          onToggleCollapse={() => setIsSidebarCollapsed((prev) => !prev)}
          activeWorkspace={activeWorkspace}
          onSelectWorkspace={(ws) => setActiveWorkspace(ws)}
          onNewSession={handleNewSession}
          sessions={conversations}
          activeSessionId={activeConversationId || undefined}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
        />

        <main className="executive-app__main">
          {activeWorkspace === 'assistant' && (
            <AssistantWorkspace
              turns={turns}
              isLoading={isLoading}
              onSendMessage={handleSendMessage}
              onSelectCitation={(cite) => setSelectedCitation(cite)}
              onSelectEmailMessage={(msg) => setSelectedEmail(msg)}
              onApproveAction={handleApproveAction}
              onDenyAction={handleDenyAction}
              onClarifyAnswer={handleSendMessage}
              onNavigateWorkspace={(ws) => setActiveWorkspace(ws)}
            />
          )}

          {activeWorkspace === 'calendar' && (
            <CalendarWorkspace
              onScheduleMeeting={(prompt) => {
                setActiveWorkspace('assistant')
                handleSendMessage(prompt)
              }}
            />
          )}

          {activeWorkspace === 'email' && (
            <EmailWorkspace
              onSelectEmail={(msg) => setSelectedEmail(msg)}
              onDraftEmail={(prompt) => {
                setActiveWorkspace('assistant')
                handleSendMessage(prompt)
              }}
            />
          )}

          {activeWorkspace === 'meetings' && (
            <MeetingsWorkspace
              onSelectCitation={(cite) => setSelectedCitation(cite)}
              onAskPrepQuestion={(prompt) => {
                setActiveWorkspace('assistant')
                handleSendMessage(prompt)
              }}
            />
          )}

          {activeWorkspace === 'knowledge' && (
            <KnowledgeWorkspace
              onSelectCitation={(cite) => setSelectedCitation(cite)}
            />
          )}

          {activeWorkspace === 'settings' && <SettingsWorkspace />}
        </main>
      </div>

      {/* Citation Detail Slide-out Drawer */}
      <Drawer
        isOpen={Boolean(selectedCitation)}
        onClose={() => setSelectedCitation(null)}
        title={selectedCitation?.title || 'Dẫn chứng tài liệu'}
        subtitle={
          selectedCitation?.page_number
            ? `Văn bản nội bộ · Trang ${selectedCitation.page_number}`
            : 'Kho tri thức nội bộ'
        }
      >
        {selectedCitation && (
          <div className="drawer-evidence-view">
            <div className="evidence-badge-row">
              <span className="evidence-type-badge">
                <FileText size={12} /> {selectedCitation.source_type || 'Tài liệu'}
              </span>
              {selectedCitation.score && (
                <span className="evidence-score-badge">
                  Độ khớp: {Math.round(selectedCitation.score * 100)}%
                </span>
              )}
            </div>

            {selectedCitation.section_title && (
              <h4 className="evidence-section-title">{selectedCitation.section_title}</h4>
            )}

            <div className="evidence-quote-box">
              <p>{selectedCitation.text}</p>
            </div>

            {selectedCitation.source_uri && (
              <div className="evidence-source-link">
                <a href={selectedCitation.source_uri} target="_blank" rel="noreferrer">
                  <span>Mở tệp gốc</span> <ExternalLink size={12} />
                </a>
              </div>
            )}
          </div>
        )}
      </Drawer>

      {/* Email Detail Slide-out Drawer */}
      <Drawer
        isOpen={Boolean(selectedEmail)}
        onClose={() => setSelectedEmail(null)}
        title={selectedEmail?.subject || 'Chi tiết email'}
        subtitle={`Từ: ${selectedEmail?.from}`}
      >
        {selectedEmail && (
          <div className="drawer-email-view">
            <div className="email-meta-box">
              <div className="email-meta-row">
                <span className="email-meta-label">Người gửi:</span>
                <span className="email-meta-value">{selectedEmail.from}</span>
              </div>
              {selectedEmail.when && (
                <div className="email-meta-row">
                  <span className="email-meta-label">Thời gian:</span>
                  <span className="email-meta-value">
                    {new Date(selectedEmail.when).toLocaleString('vi-VN')}
                  </span>
                </div>
              )}
            </div>

            <div className="email-body-box">
              <p>{selectedEmail.snippet}</p>
            </div>

            <div className="email-actions-box">
              <a
                href={`https://mail.google.com/mail/u/0/#inbox/${selectedEmail.thread_id || selectedEmail.id}`}
                target="_blank"
                rel="noreferrer"
                className="btn-open-gmail"
              >
                <Mail size={14} /> Mở trong Gmail
              </a>
            </div>
          </div>
        )}
      </Drawer>

      {/* Authentication Modal */}
      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        onSuccess={handleAuthSuccess}
      />
    </div>
  )
}

export const App: React.FC = () => {
  return (
    <ToastProvider>
      <AppContent />
    </ToastProvider>
  )
}

export default App
