import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './d0.css'
import D0App from './D0App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <D0App />
  </StrictMode>,
)
