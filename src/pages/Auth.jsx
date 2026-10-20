import React, { useState } from 'react'
import { get, post } from '../api.js'
import { Field, Loading, go } from '../ui.jsx'
import { useApi } from '../api.js'

function Frame({ children }) {
  return (
    <main className="auth">
      <div className="auth-card">
        <p className="brand auth-brand"><span className="brand-mark" aria-hidden="true" />Sounding</p>
        {children}
      </div>
    </main>
  )
}

export function LoginPage({ onDone }) {
  const demo = useApi('/auth/demo')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      onDone(await post('/auth/login', { email, password }))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Frame>
      <h1>Log in</h1>
      <p className="muted">Your meetings, notes and team calls.</p>
      <form onSubmit={submit} className="stack-form">
        <Field label="Work email"><input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
        <Field label="Password"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? 'Logging in' : 'Log in'}</button>
      </form>
      {demo.data?.available && (
        <div className="demo-box">
          <p><b>Reviewing Sounding?</b> Open the demo workspace. It has real recorded meetings, including an hour-long call with eight people.</p>
          <button className="secondary" disabled={busy} onClick={async () => {
            setBusy(true); setError(null)
            try { onDone(await post('/auth/demo')) } catch (err) { setError(err.message) } finally { setBusy(false) }
          }}>Explore the demo workspace</button>
        </div>
      )}
      <p className="muted small">New here? Ask an admin in your organization for an invite link.</p>
    </Frame>
  )
}

export function InvitePage({ token, onDone }) {
  const info = useApi(`/auth/invite/${token}`)
  const [f, setF] = useState({ name: '', email: '', password: '' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  if (info.loading) return <Frame><Loading /></Frame>
  if (info.error) {
    return (
      <Frame>
        <h1>Invite link not valid</h1>
        <p className="muted">{info.error.message}</p>
        <button className="secondary" onClick={() => go('#/')}>Go to log in</button>
      </Frame>
    )
  }
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const me = await post(`/auth/invite/${token}`, f)
      go('#/')
      onDone(me)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Frame>
      <h1>Join {info.data.org}</h1>
      <p className="muted">Create your account to see the meetings your team shares with you.</p>
      <form onSubmit={submit} className="stack-form">
        <Field label="Your name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required autoFocus autoComplete="name" /></Field>
        {info.data.email
          ? <Field label="Work email"><input value={info.data.email} disabled /></Field>
          : <Field label="Work email"><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required autoComplete="username" /></Field>}
        <Field label="Password" hint="At least 10 characters.">
          <input type="password" minLength={10} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required autoComplete="new-password" />
        </Field>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? 'Creating account' : 'Create account'}</button>
      </form>
    </Frame>
  )
}
