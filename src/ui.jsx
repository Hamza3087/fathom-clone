// Shared building blocks. Pages import from here so the app reads as one product.
import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react'

/* ---------- formatting ---------- */
export const fmtClock = (s) => {
  s = Math.max(0, Math.floor(s || 0))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}
export const fmtDate = (iso) =>
  new Date(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) +
  ' at ' + new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
export const fmtDay = (iso) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
export const fmtMinutes = (s) => (s ? `${Math.max(1, Math.round(s / 60))} min` : '')
export const fmtAgo = (ts) => {
  const d = (Date.now() / 1000 - ts) / 60
  if (d < 1) return 'just now'
  if (d < 60) return `${Math.round(d)} min ago`
  if (d < 1440) return `${Math.round(d / 60)} h ago`
  return fmtDay(new Date(ts * 1000).toISOString())
}
export const initials = (n = '') =>
  /^Speaker \d+$/.test(n) ? `S${n.split(' ')[1]}` : n.split(/[\s@.]+/).filter(Boolean).map((p) => p[0]).slice(0, 2).join('').toUpperCase()

/* ---------- colors ---------- */
export const HUES = ['#3B5BDB', '#C77D0A', '#9C3F8E', '#2F8F5B', '#0F6B73', '#B3261E', '#5F6AD8', '#8A6D1F']
export const hueFor = (key = '') => HUES[[...String(key)].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length]
// Speakers in a meeting get colors by their order, so Speaker 1..4 are always distinct.
export const speakerHue = (list, s) => HUES[Math.max(0, list.indexOf(s)) % HUES.length]

export function Avatar({ name, id, size = 28, color }) {
  return (
    <span className="avatar" title={name} style={{ width: size, height: size, fontSize: size * 0.38, background: color || hueFor(id || name) }}>
      {initials(name)}
    </span>
  )
}

export function Faces({ names, list, max = 4 }) {
  return (
    <span className="faces" aria-label={names.join(', ')}>
      {names.slice(0, max).map((n) => <Avatar key={n} name={n} color={list ? speakerHue(list, n) : undefined} />)}
      {names.length > max && <span className="avatar more">+{names.length - max}</span>}
    </span>
  )
}

/* ---------- routing (hash) ---------- */
export function parseHash(hash = window.location.hash) {
  const [p, q = ''] = hash.replace(/^#/, '').split('?')
  const parts = p.split('/').filter(Boolean).map(decodeURIComponent)
  return { parts, query: Object.fromEntries(new URLSearchParams(q)) }
}
export function useRoute() {
  const [r, setR] = useState(parseHash())
  useEffect(() => {
    const on = () => setR(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return r
}
export const go = (to) => { window.location.hash = to }
export const setQuery = (patch) => {
  const { parts, query } = parseHash()
  const q = new URLSearchParams(Object.entries({ ...query, ...patch }).filter(([, v]) => v !== undefined && v !== null && v !== ''))
  const s = q.toString()
  window.history.replaceState(null, '', `#/${parts.map(encodeURIComponent).join('/')}${s ? `?${s}` : ''}`)
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

/* ---------- app context ---------- */
export const AppCtx = createContext(null)
export const useApp = () => useContext(AppCtx) // { me, org, teams, isAdmin, isLead, members, refreshMe, toast, openUpload }

/* ---------- toasts ---------- */
export function useToasts() {
  const [items, setItems] = useState([])
  const toast = useCallback((text, kind = 'ok') => {
    const id = Math.random()
    setItems((x) => [...x, { id, text, kind }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'error' ? 6000 : 3200)
  }, [])
  const view = (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
    </div>
  )
  return { toast, view }
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API needs HTTPS; fall back to a hidden textarea on plain HTTP.
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  }
}
export const absLink = (hashPath) => `${window.location.origin}${window.location.pathname}${hashPath.replace(/^\//, '')}`

/* ---------- primitives ---------- */
export const Spinner = ({ label }) => (
  <span className="spin-wrap"><span className="spin" aria-hidden="true" />{label && <span>{label}</span>}</span>
)

export function Loading({ label = 'Loading' }) {
  return <div className="loading"><Spinner label={label} /></div>
}

export function ErrorNote({ error, retry }) {
  if (!error) return null
  return (
    <div className="error-note" role="alert">
      <span>{error.message || String(error)}</span>
      {retry && <button className="link" onClick={retry}>Try again</button>}
    </div>
  )
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  )
}

export function Modal({ title, onClose, children, footer, wide }) {
  const box = useRef()
  useEffect(() => {
    const prev = document.activeElement
    box.current?.querySelector('input, textarea, select, button')?.focus()
    const esc = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => { window.removeEventListener('keydown', esc); prev?.focus?.() }
  }, [onClose])
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} ref={box}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" width="18" height="18"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" /></svg>
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  )
}

// A small dropdown menu. items: [{label, onClick, danger, disabled}] or null for a divider.
export function Menu({ label, items, align = 'right', className = 'icon-btn', children }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    if (!open) return
    const close = (e) => !ref.current?.contains(e.target) && setOpen(false)
    const esc = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <span className="menu-wrap" ref={ref}>
      <button className={className} aria-haspopup="menu" aria-expanded={open} aria-label={typeof label === 'string' ? label : undefined}
        onClick={(e) => { e.stopPropagation(); e.preventDefault(); setOpen((o) => !o) }}>
        {children || <svg viewBox="0 0 24 24" width="18" height="18"><circle cx="5" cy="12" r="1.8" fill="currentColor" /><circle cx="12" cy="12" r="1.8" fill="currentColor" /><circle cx="19" cy="12" r="1.8" fill="currentColor" /></svg>}
      </button>
      {open && (
        <div className={`menu ${align}`} role="menu">
          {items.map((it, i) => it === null ? <hr key={i} /> : (
            <button key={i} role="menuitem" className={it.danger ? 'danger' : ''} disabled={it.disabled}
              onClick={(e) => { e.stopPropagation(); e.preventDefault(); setOpen(false); it.onClick() }}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}

export function Tabs({ tabs, value, onChange, className = '' }) {
  return (
    <div className={`tabs ${className}`} role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? 'tab on' : 'tab'} onClick={() => onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function PageHead({ title, sub, actions }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {actions && <div className="head-actions">{actions}</div>}
    </header>
  )
}

export const VIS_LABEL = { private: 'Only people added', team: 'Team', org: 'Everyone in the organization' }

// Wraps an async action with a busy flag and toasts errors.
export function useAction(toast) {
  const [busyKey, setBusy] = useState(null)
  const run = useCallback(async (key, fn, okText) => {
    setBusy(key)
    try {
      const r = await fn()
      if (okText) toast?.(okText)
      return r
    } catch (e) {
      toast?.(e.message, 'error')
      return undefined
    } finally {
      setBusy(null)
    }
  }, [toast])
  return [busyKey, run]
}
