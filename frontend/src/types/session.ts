import type { ChatTurn } from '../components/conversation/MessageItem'

export interface ChatSession {
  id: string
  title: string
  turns: ChatTurn[]
  createdAt: number
  updatedAt: number
}
