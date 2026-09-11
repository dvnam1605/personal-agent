import React from 'react'
import { Mail, Clock, User, ArrowRight } from 'lucide-react'
import type { GmailMessage } from '../../types/api'
import './DomainCards.css'

interface EmailCardProps {
  messages: GmailMessage[]
  onSelectMessage?: (msg: GmailMessage) => void
}

export const EmailCard: React.FC<EmailCardProps> = ({ messages, onSelectMessage }) => {
  if (!messages || messages.length === 0) {
    return (
      <div className="domain-empty-card">
        <Mail size={18} className="domain-empty-card__icon" />
        <span>Không có email nào khớp trong hộp thư.</span>
      </div>
    )
  }

  return (
    <div className="email-card-container">
      <div className="domain-card-header">
        <div className="domain-card-header__left">
          <Mail size={16} className="domain-icon--email" />
          <span className="domain-card-header__title">Hộp thư Gmail ({messages.length} email)</span>
        </div>
      </div>

      <div className="email-message-list">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`email-message-item ${msg.unread ? 'email-message-item--unread' : ''}`}
            onClick={() => onSelectMessage?.(msg)}
          >
            <div className="email-message-item__top">
              <div className="email-message-item__sender">
                <User size={13} />
                <span>{msg.from}</span>
              </div>
              {msg.when && (
                <div className="email-message-item__time">
                  <Clock size={12} />
                  <span>{new Date(msg.when).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
              )}
            </div>

            <h4 className="email-message-item__subject">
              {msg.unread && <span className="unread-dot" />}
              {msg.subject}
            </h4>

            <p className="email-message-item__snippet">{msg.snippet}</p>

            {onSelectMessage && (
              <div className="email-message-item__footer">
                <span className="email-read-more">
                  Xem chi tiết <ArrowRight size={12} />
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
