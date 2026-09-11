import React from 'react'
import { Calendar, Clock, MapPin, ExternalLink } from 'lucide-react'
import type { CalendarEvent } from '../../types/api'
import './DomainCards.css'

interface CalendarCardProps {
  events: CalendarEvent[]
  window?: { start: string; end: string }
}

export const CalendarCard: React.FC<CalendarCardProps> = ({ events, window }) => {
  if (!events || events.length === 0) {
    return (
      <div className="domain-empty-card">
        <Calendar size={18} className="domain-empty-card__icon" />
        <span>Không có sự kiện nào trong khoảng thời gian này.</span>
      </div>
    )
  }

  return (
    <div className="calendar-card-container">
      <div className="domain-card-header">
        <div className="domain-card-header__left">
          <Calendar size={16} className="domain-icon--calendar" />
          <span className="domain-card-header__title">Lịch Google ({events.length} sự kiện)</span>
        </div>
        {window && (
          <span className="domain-card-header__meta">
            Khoảng: {new Date(window.start).toLocaleDateString('vi-VN')}
          </span>
        )}
      </div>

      <div className="calendar-event-list">
        {events.map((event) => (
          <div key={event.id} className="calendar-event-item">
            <div className="calendar-event-item__time">
              <Clock size={14} />
              <span>{event.when}</span>
            </div>
            <div className="calendar-event-item__content">
              <h4 className="calendar-event-item__title">{event.summary}</h4>
              {event.location && (
                <div className="calendar-event-item__location">
                  <MapPin size={13} />
                  <span>{event.location}</span>
                </div>
              )}
            </div>
            {event.html_link && (
              <a
                href={event.html_link}
                target="_blank"
                rel="noreferrer"
                className="calendar-event-item__link"
                title="Mở trong Google Calendar"
              >
                <ExternalLink size={14} />
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
