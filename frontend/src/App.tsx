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
import { apiClient } from './api/client'
import type { ChatTurn } from './components/conversation/MessageItem'
import type { ApprovalRequestResponse, GmailMessage, RAGCitation } from './types/api'
import { FileText, Mail, ExternalLink } from 'lucide-react'
import './App.css'

const AppContent: React.FC = () => {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceType>('assistant')
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [historyTitles, setHistoryTitles] = useState<string[]>([])
  const [selectedCitation, setSelectedCitation] = useState<RAGCitation | null>(null)
  const [selectedEmail, setSelectedEmail] = useState<GmailMessage | null>(null)
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const saved = localStorage.getItem('personal_ai_theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('personal_ai_theme', theme)
  }, [theme])

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }

  const { showToast } = useToast()

  const handleSendMessage = async (queryText: string) => {
    if (!queryText.trim() || isLoading) return

    // Ensure we are in assistant workspace when sending messages
    setActiveWorkspace('assistant')

    const turnId = Math.random().toString(36).substring(2, 9)
    const timestamp = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })

    const newTurn: ChatTurn = {
      id: turnId,
      userQuery: queryText,
      timestamp,
    }

    setTurns((prev) => [...prev, newTurn])
    setIsLoading(true)

    // Track in history titles if unique
    setHistoryTitles((prev) => {
      const exists = prev.includes(queryText)
      return exists ? prev : [...prev, queryText]
    })

    const startTime = Date.now()
    let firstTokenReceived = false
    let currentRunId: string | null = null

    try {
      await apiClient.submitQueryStream(queryText, {
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
          if (!firstTokenReceived) {
            firstTokenReceived = true
            setIsLoading(false)
          }
          setTurns((prev) =>
            prev.map((turn) => {
              if (turn.id === turnId) {
                const existing = turn.result?.message || ''
                const ttft = turn.ttft ?? (Date.now() - startTime) / 1000
                return {
                  ...turn,
                  ttft,
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
      })
    } catch (err: any) {
      const errMsg = err?.message || 'Không thể xử lý yêu cầu.'
      // Set error solely on the chat turn — DO NOT emit duplicate toast error!
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

      // Update approval card state in turns
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

      // Update approval card state in turns
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

  const handleNewSession = () => {
    setTurns([])
    setActiveWorkspace('assistant')
    showToast('Đã mở cuộc trò chuyện mới.', 'info')
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
          historyTitles={historyTitles}
          onSelectHistory={handleSendMessage}
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
