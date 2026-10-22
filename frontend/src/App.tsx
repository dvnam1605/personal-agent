import React, { useState, useEffect } from 'react'
import { Header, type WorkspaceType } from './components/layout/Header'
import { Sidebar } from './components/layout/Sidebar'
import { AssistantWorkspace } from './components/workspaces/AssistantWorkspace'
import { CalendarWorkspace } from './components/workspaces/CalendarWorkspace'
import { EmailWorkspace } from './components/workspaces/EmailWorkspace'
import { MeetingsWorkspace } from './components/workspaces/MeetingsWorkspace'
import { KnowledgeWorkspace } from './components/workspaces/knowledge/KnowledgeWorkspace'
import { DocumentsWorkspace } from './components/workspaces/documents/DocumentsWorkspace'
import { SettingsWorkspace } from './components/workspaces/SettingsWorkspace'
import { CitationDrawer, EmailDrawer } from './components/layout/DetailDrawers'
import { ToastProvider } from './components/ui/Toast'
import { useToast } from './components/ui/toastContext'
import { AuthModal } from './components/auth/AuthModal'
import { apiClient } from './api/client'
import { useConversations } from './hooks/useConversations'
import { useQueryStream } from './hooks/useQueryStream'
import type { GmailMessage, RAGCitation, UserProfile } from './types/api'
import './App.css'

const AppContent: React.FC = () => {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceType>('assistant')
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)

  // User authentication
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null)
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false)

  const { showToast } = useToast()

  // Database-backed conversations
  const {
    conversations,
    setConversations,
    activeConversationId,
    setActiveConversationId,
    turns,
    setTurns,
    loadConversation,
  } = useConversations(showToast)

  const { isLoading, streamQuery } = useQueryStream({ setTurns, setConversations, showToast })
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
  }, [loadConversation, setActiveConversationId, setConversations])

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

    await streamQuery(queryText, currentConvId)
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

          {activeWorkspace === 'documents' && <DocumentsWorkspace />}

          {activeWorkspace === 'settings' && <SettingsWorkspace />}
        </main>
      </div>

      <CitationDrawer citation={selectedCitation} onClose={() => setSelectedCitation(null)} />

      <EmailDrawer email={selectedEmail} onClose={() => setSelectedEmail(null)} />

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
