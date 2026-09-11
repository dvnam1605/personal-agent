import React, { useState } from 'react'
import { HelpCircle, Send } from 'lucide-react'
import { Button } from '../ui/Button'
import './QuestionPlaneCard.css'

interface QuestionPlaneCardProps {
  questionText: string
  suggestedChoices?: string[]
  onAnswer: (answer: string) => void
  disabled?: boolean
}

export const QuestionPlaneCard: React.FC<QuestionPlaneCardProps> = ({
  questionText,
  suggestedChoices = ['Hôm nay', 'Ngày mai', 'Tuần này', 'Huỷ yêu cầu'],
  onAnswer,
  disabled = false,
}) => {
  const [customText, setCustomText] = useState('')

  const handleChoiceClick = (choice: string) => {
    if (disabled) return
    onAnswer(choice)
  }

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!customText.trim() || disabled) return
    onAnswer(customText.trim())
    setCustomText('')
  }

  return (
    <div className="question-plane-card">
      <div className="question-plane-card__header">
        <HelpCircle size={16} className="question-plane-card__icon" />
        <span className="question-plane-card__title">Cần thêm thông tin làm rõ</span>
      </div>

      <div className="question-plane-card__body">
        <p className="question-plane-card__prompt">{questionText}</p>

        {suggestedChoices.length > 0 && (
          <div className="question-choices-tray">
            {suggestedChoices.map((choice, i) => (
              <button
                key={i}
                type="button"
                className="question-choice-chip"
                onClick={() => handleChoiceClick(choice)}
                disabled={disabled}
              >
                {choice}
              </button>
            ))}
          </div>
        )}

        <form className="question-custom-form" onSubmit={handleCustomSubmit}>
          <input
            type="text"
            className="question-custom-input"
            placeholder="Nhập câu trả lời hoặc thời gian cụ thể..."
            value={customText}
            onChange={(e) => setCustomText(e.target.value)}
            disabled={disabled}
          />
          <Button
            type="submit"
            size="sm"
            variant="primary"
            disabled={!customText.trim() || disabled}
            rightIcon={<Send size={12} />}
          >
            Gửi
          </Button>
        </form>
      </div>
    </div>
  )
}
