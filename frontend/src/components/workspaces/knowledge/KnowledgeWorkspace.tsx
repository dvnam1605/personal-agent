import React, { useState, useMemo } from 'react'
import { AlertCircle } from 'lucide-react'
import { apiClient } from '../../../api/client'
import type { RAGCitation } from '../../../types/api'
import { INTERNAL_DOCUMENTS } from './internalDocuments'
import { KnowledgeSearchHero } from './KnowledgeSearchHero'
import { KnowledgeResults } from './KnowledgeResults'
import { KnowledgeCatalog } from './KnowledgeCatalog'
import '../Workspaces.css'
import './KnowledgeWorkspace.css'

interface KnowledgeWorkspaceProps {
  onSelectCitation: (citation: RAGCitation) => void
}

export const KnowledgeWorkspace: React.FC<KnowledgeWorkspaceProps> = ({ onSelectCitation }) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [citations, setCitations] = useState<RAGCitation[]>([])
  const [answer, setAnswer] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState<string>('all')

  const handleSearch = async (queryToRun?: string) => {
    const q = queryToRun || searchQuery
    if (!q.trim() || isLoading) return

    if (queryToRun) {
      setSearchQuery(queryToRun)
    }

    setIsLoading(true)
    setError(null)
    setAnswer(null)
    setCitations([])

    try {
      await apiClient.submitQueryStream(`Tìm trong tài liệu nội bộ: ${q}`, {
        onToken: (delta) => {
          setAnswer((prev) => (prev !== null ? prev + delta : delta))
        },
        onCitations: (data) => {
          if (data.citations && Array.isArray(data.citations)) {
            setCitations(data.citations as RAGCitation[])
          }
          if (data.answer) setAnswer(data.answer)
        },
        onError: (err) => {
          setError(err?.message || 'Không thể tra cứu kho tri thức nội bộ.')
        },
      })
    } catch (err: any) {
      setError(err?.message || 'Không thể tra cứu kho tri thức nội bộ.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleResetSearch = () => {
    setSearchQuery('')
    setAnswer(null)
    setCitations([])
  }

  const filteredDocs = useMemo(() => {
    return INTERNAL_DOCUMENTS.filter((doc) => {
      const matchesCat = selectedCategory === 'all' || doc.category === selectedCategory
      const matchesSearch =
        !searchQuery.trim() ||
        doc.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        doc.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
        doc.summary.toLowerCase().includes(searchQuery.toLowerCase())
      return matchesCat && matchesSearch
    })
  }, [selectedCategory, searchQuery])

  const isShowingSearchResults = Boolean(answer !== null || citations.length > 0)

  return (
    <div className="workspace-container knowledge-workspace">
      {/* Search Bar Hero Toolbar */}
      <KnowledgeSearchHero
        searchQuery={searchQuery}
        isLoading={isLoading}
        onQueryChange={setSearchQuery}
        onSearch={handleSearch}
      />

      {/* Content Area */}
      <div className="workspace-content-scroll">
        {error ? (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Lỗi tra cứu</h4>
              <p>{error}</p>
            </div>
            <button className="btn-retry" onClick={() => handleSearch()}>
              Thử lại
            </button>
          </div>
        ) : isShowingSearchResults ? (
          <KnowledgeResults
            answer={answer}
            citations={citations}
            isLoading={isLoading}
            onBack={handleResetSearch}
            onSelectCitation={onSelectCitation}
          />
        ) : (
          <KnowledgeCatalog
            docs={filteredDocs}
            selectedCategory={selectedCategory}
            onSelectCategory={setSelectedCategory}
            onRunQuery={(query) => {
              setSearchQuery(query)
              handleSearch(query)
            }}
          />
        )}
      </div>
    </div>
  )
}
