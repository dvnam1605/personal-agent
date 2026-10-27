import React, { useEffect, useState } from 'react'
import { Send, Link2Off, KeyRound } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { TelegramLinkCode, TelegramStatus } from '../../types/api'
import { useToast } from '../ui/toastContext'
import './MemoryPanel.css'

/**
 * Link this account to a Telegram chat: issue a one-time code to send to the bot as
 * "/link <code>", show whether a chat is linked, and unlink it.
 */
export const TelegramPanel: React.FC = () => {
  const { showToast } = useToast()
  const [status, setStatus] = useState<TelegramStatus | null>(null)
  const [code, setCode] = useState<TelegramLinkCode | null>(null)

  useEffect(() => {
    let active = true
    apiClient
      .getTelegramStatus()
      .then((value) => {
        if (active) setStatus(value)
      })
      .catch(() => {
        if (active) setStatus(null)
      })
    return () => {
      active = false
    }
  }, [])

  const handleCode = async () => {
    try {
      setCode(await apiClient.createTelegramLinkCode())
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không tạo được mã.', 'error')
    }
  }

  const handleUnlink = async () => {
    try {
      setStatus(await apiClient.unlinkTelegram())
      setCode(null)
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Không hủy được liên kết.', 'error')
    }
  }

  const bot = status?.bot_username ? `@${status.bot_username}` : 'bot của trợ lý'

  return (
    <div className="settings-card">
      <div className="settings-card-header">
        <div className="card-header-titles">
          <h3>Telegram</h3>
          <p>
            Hỏi trợ lý, duyệt hành động bằng nút Duyệt / Từ chối và nhận thông báo ngay trên
            Telegram.
          </p>
        </div>
        <Send size={18} className="text-dim" />
      </div>

      {!status?.enabled ? (
        <p className="memory-empty">
          Máy chủ chưa bật bot Telegram (thiếu TELEGRAM__BOT_TOKEN trong .env).
        </p>
      ) : status.linked ? (
        <div className="memory-add">
          <span className="memory-content">Đã liên kết với một chat Telegram.</span>
          <button className="memory-button memory-button--danger" onClick={handleUnlink}>
            <Link2Off size={14} />
            <span>Hủy liên kết</span>
          </button>
        </div>
      ) : (
        <>
          <div className="memory-add">
            <span className="memory-content">
              Tạo mã rồi gửi cho {bot}: <code>/link &lt;mã&gt;</code>
            </span>
            <button className="memory-button memory-button--primary" onClick={handleCode}>
              <KeyRound size={14} />
              <span>Tạo mã liên kết</span>
            </button>
          </div>
          {code && (
            <p className="memory-empty">
              Gửi <code>/link {code.code}</code> cho {bot}. Mã hết hạn lúc{' '}
              {new Date(code.expires_at).toLocaleTimeString('vi-VN')} và chỉ dùng được một lần.
            </p>
          )}
        </>
      )}
    </div>
  )
}
