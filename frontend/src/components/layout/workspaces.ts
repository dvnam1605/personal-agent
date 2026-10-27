import type React from 'react'
import { Calendar, FilePlus2, FileText, ListChecks, Mail, Users } from 'lucide-react'
import { NammLogo } from '../common/NammLogo'

export type WorkspaceType =
  | 'assistant'
  | 'calendar'
  | 'email'
  | 'tasks'
  | 'meetings'
  | 'knowledge'
  | 'documents'
  | 'settings'

export interface WorkspaceTab {
  id: WorkspaceType
  label: string
  icon: React.ComponentType<{ size?: number }>
}

/** The navigable workspaces, shared by the header tabs and the sidebar menu so both stay in step. */
export const WORKSPACE_TABS: WorkspaceTab[] = [
  { id: 'assistant', label: 'Trợ lý điều hành', icon: NammLogo },
  { id: 'calendar', label: 'Lịch Google', icon: Calendar },
  { id: 'email', label: 'Hộp thư Gmail', icon: Mail },
  { id: 'tasks', label: 'Việc cần làm', icon: ListChecks },
  { id: 'meetings', label: 'Hồ sơ họp WF-05', icon: Users },
  { id: 'knowledge', label: 'Kho tri thức', icon: FileText },
  { id: 'documents', label: 'Tạo tài liệu', icon: FilePlus2 },
]
