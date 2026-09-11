import React, { useState, useEffect } from 'react'
import {
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  Clock,
  Check,
  X,
  Calendar,
  Mail,
} from 'lucide-react'
import type { ApprovalRequestResponse, RiskLevel } from '../../types/api'
import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import './ApprovalCard.css'

interface ApprovalCardProps {
  approval: ApprovalRequestResponse
  onApprove: (id: string, execute: boolean) => Promise<void>
  onDeny: (id: string, reason?: string) => Promise<void>
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({
  approval,
  onApprove,
  onDeny,
}) => {
  const [isApproving, setIsApproving] = useState(false)
  const [isDenying, setIsDenying] = useState(false)
  const [timeLeft, setTimeLeft] = useState<string | null>(null)
  const [isExpired, setIsExpired] = useState(false)

  // Countdown timer
  useEffect(() => {
    if (!approval.expires_at) return

    const updateCountdown = () => {
      const now = new Date().getTime()
      const expiry = new Date(approval.expires_at!).getTime()
      const diff = expiry - now

      if (diff <= 0) {
        setTimeLeft('Đã hết hạn')
        setIsExpired(true)
        return
      }

      const minutes = Math.floor(diff / 60000)
      const seconds = Math.floor((diff % 60000) / 1000)
      setTimeLeft(`${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`)
    }

    updateCountdown()
    const timer = setInterval(updateCountdown, 1000)
    return () => clearInterval(timer)
  }, [approval.expires_at])

  const handleApprove = async () => {
    try {
      setIsApproving(true)
      await onApprove(approval.id, true)
    } finally {
      setIsApproving(false)
    }
  }

  const handleDeny = async () => {
    try {
      setIsDenying(true)
      await onDeny(approval.id, 'Người dùng từ chối thao tác.')
    } finally {
      setIsDenying(false)
    }
  }

  const getRiskBadge = (risk: RiskLevel) => {
    const r = (risk || '').toUpperCase()
    if (r === 'DESTRUCTIVE') {
      return (
        <Badge variant="danger" icon={<ShieldAlert size={12} />}>
          Rủi ro cao (Xóa/Hủy)
        </Badge>
      )
    }
    if (r === 'SENSITIVE') {
      return (
        <Badge variant="warning" icon={<AlertTriangle size={12} />}>
          Cần xác nhận
        </Badge>
      )
    }
    return (
      <Badge variant="safe" icon={<ShieldCheck size={12} />}>
        An toàn
      </Badge>
    )
  }

  const getToolIcon = (toolName?: string | null) => {
    if (toolName?.includes('calendar')) return <Calendar size={16} />
    if (toolName?.includes('gmail') || toolName?.includes('mail')) return <Mail size={16} />
    return <ShieldAlert size={16} />
  }

  const isPending = approval.status === 'pending' && !isExpired

  return (
    <div className={`approval-card approval-card--${(approval.risk_level || 'SENSITIVE').toLowerCase()}`}>
      <div className="approval-card__top">
        <div className="approval-card__badge-row">
          <div className="approval-card__icon-wrap">
            {getToolIcon(approval.tool_name)}
          </div>
          {getRiskBadge(approval.risk_level)}
          {timeLeft && (
            <div className={`approval-card__countdown ${isExpired ? 'approval-card__countdown--expired' : ''}`}>
              <Clock size={12} />
              <span>{isExpired ? 'Đã hết hạn' : `Hết hạn sau: ${timeLeft}`}</span>
            </div>
          )}
        </div>
      </div>

      <div className="approval-card__body">
        <h3 className="approval-card__title">Xác nhận thực thi hành động</h3>
        <p className="approval-card__desc">{approval.description}</p>

        {/* Important parameters breakdown */}
        {approval.important_arguments && Object.keys(approval.important_arguments).length > 0 && (
          <div className="approval-card__params">
            {Object.entries(approval.important_arguments).map(([key, value]) => (
              <div key={key} className="approval-param-row">
                <span className="approval-param-row__key">{key}:</span>
                <span className="approval-param-row__value">
                  {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Decision Actions Bar */}
      <div className="approval-card__footer">
        {isPending ? (
          <>
            <Button
              size="md"
              variant="ghost"
              onClick={handleDeny}
              isLoading={isDenying}
              disabled={isApproving}
              leftIcon={<X size={15} />}
            >
              Từ chối
            </Button>
            <Button
              size="md"
              variant={approval.risk_level === 'DESTRUCTIVE' ? 'danger' : 'warning'}
              onClick={handleApprove}
              isLoading={isApproving}
              disabled={isDenying}
              leftIcon={<Check size={15} />}
            >
              Phê duyệt & thực thi
            </Button>
          </>
        ) : (
          <div className="approval-card__status-banner">
            {approval.status === 'approved' && (
              <span className="approval-status--approved">
                <Check size={14} /> Đã phê duyệt và thực thi thành công
              </span>
            )}
            {approval.status === 'rejected' && (
              <span className="approval-status--rejected">
                <X size={14} /> Yêu cầu đã bị từ chối
              </span>
            )}
            {isExpired && approval.status === 'pending' && (
              <span className="approval-status--expired">
                <Clock size={14} /> Yêu cầu phê duyệt đã hết hạn
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
