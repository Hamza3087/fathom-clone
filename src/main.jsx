import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css' // base first, so page stylesheets imported by App can override it
import App from './App.jsx'

createRoot(document.getElementById('root')).render(<App />)
