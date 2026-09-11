import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Ensure local dev backend uses default-user to maintain 200 OK authentication
const storedUser = localStorage.getItem('personal_ai_user_id')
if (storedUser && storedUser !== 'default-user' && !localStorage.getItem('personal_ai_api_key')) {
  localStorage.setItem('personal_ai_display_name', storedUser)
  localStorage.setItem('personal_ai_user_id', 'default-user')
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
