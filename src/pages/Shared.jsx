// What someone sees when they open a share link: a meeting or a single clip, read-only.
import React, { useRef, useState } from 'react'
import { useApi } from '../api.js'
import { Loading, fmtClock, fmtDate, speakerHue } from '../ui.jsx'

export function PublicShare({ token }) {
  const r = useApi(`/public/${token}`)
  const el = useRef()
  const [now, setNow] = useState(0)
  if (r.loading) return <Loading />
  if (r.error) {
    return (
      <main className="auth"><div className="auth-card">
        <p className="brand auth-brand"><span className="brand-mark" aria-hidden="true" />Sounding</p>
        <h1>{r.error.status === 401 ? 'Log in to view this' : 'Link not available'}</h1>
        <p className="muted">{r.error.message}</p>
        {r.error.status === 401 && <a className="primary as-btn" href="#/">Log in</a>}
      </div></main>
    )
  }
  const d = r.data
  const names = d.names || {}
  const who = (s) => names[s] || s
  const src = `/api/public/${token}/media?kind=${d.has_video ? 'video' : 'audio'}`
  const seek = (t) => { el.current.currentTime = t; el.current.play() }
  const active = d.transcript.reduce((a, l, i) => (l.t <= now ? i : a), -1)
  return (
    <main className="public">
      <header className="public-head">
        <p className="brand"><span className="brand-mark" aria-hidden="true" />Sounding</p>
        <p className="muted small">Shared by {d.owner} · {d.org}</p>
      </header>
      <h1>{d.kind === 'clip' ? d.clip.title : d.title}</h1>
      <p className="muted">{d.kind === 'clip' ? `Clip from ${d.title} · ${fmtClock(d.clip.duration)}` : `${fmtDate(d.date)} · ${fmtClock(d.duration)}`}</p>
      {d.kind === 'clip' && d.clip.note && <p className="clip-note">{d.clip.note}</p>}
      <div className="public-grid">
        <section>
          {d.has_video
            ? <video ref={el} src={src} controls className="public-media" onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)} />
            : <audio ref={el} src={src} controls className="public-audio" onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)} />}
          <ol className="transcript public-tr">
            {d.transcript.map((l, i) => (
              <li key={i} className={`line${i === active ? ' active' : ''}`}>
                <button className="stamp" onClick={() => seek(l.t)}>{fmtClock(l.t)}</button>
                <div><span className="who" style={{ color: speakerHue(d.speakers, l.s) }}>{who(l.s)}</span><p>{l.x}</p></div>
              </li>
            ))}
          </ol>
        </section>
        {d.kind === 'meeting' && d.summary && (
          <aside className="public-notes">
            <h2>Overview</h2>
            <p>{d.summary.overview}</p>
            {d.summary.sections?.filter((s) => s.bullets.length).map((s) => (
              <section key={s.title}>
                <h2>{s.title}</h2>
                <ul className="bullets">{s.bullets.map((b, i) => (
                  <li key={i}>{b.text} {b.t != null && <button className="link clock" onClick={() => seek(b.t)}>{fmtClock(b.t)}</button>}</li>
                ))}</ul>
              </section>
            ))}
            {d.actions.length > 0 && <><h2>Action items</h2><ul className="bullets">{d.actions.map((a, i) => <li key={i}>{a.text}</li>)}</ul></>}
          </aside>
        )}
      </div>
    </main>
  )
}
