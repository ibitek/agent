import './theme.js'
import '@fontsource-variable/nunito-sans/wght.css'
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './styles.css'
import './results.css'
import './chat.css'
import './studio.css'
import './builder.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode><App /></React.StrictMode>,
)
