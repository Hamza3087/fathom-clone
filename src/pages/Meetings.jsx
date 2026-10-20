// Meeting lists (My calls, Team calls, Shared with me) and global search results.
import React, { useMemo, useState } from 'react'
import { post, useApi, qs, busy } from '../api.js'
import { Modal, Menu, Field, Loading, ErrorNote, Empty, PageHead, fmtClock, fmtDate, copyText, absLink, go, useApp } from '../ui.jsx'
import { MeetingRows } from '../rows.jsx'
import './lists.css'

const TITLES = { mine: 'My calls', team: 'Team calls', shared: 'Shared with me' }
const SUBS = {
  mine: 'Recordings you uploaded, newest first.',
  team: 'Meetings your teammates shared with a team you are on or with the whole organization.',
  shared: 'Meetings other people shared with you directly, or that you attended.',
}

const dayKey = (iso) => {
  const d = new Date(iso)
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}
function dayLabel(iso) {
  const d = new Date(iso)
  const today = new Date()
  const yest = new Date(); yest.setDate(today.getDate() - 1)
  if (dayKey(d) === dayKey(today)) return 'Today'
  if (dayKey(d) === dayKey(yest)) return 'Yesterday'
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) })
}

export function groupByDay(rows) {
  const groups = []
  rows.forEach((m) => {
    const k = dayKey(m.date)
    if (!groups.length || groups[groups.length - 1].key !== k) groups.push({ key: k, label: dayLabel(m.date), rows: [] })
    groups[groups.length - 1].rows.push(m)
  })
  return groups
}

// Adds a meeting to one of your folders, or to a new one.
export function AddToFolderModal({ meeting, onClose }) {
  const { toast } = useApp()
  const folders = useApi('/folders')
  const [name, setName] = useState('')
  const [busyId, setBusyId] = useState(null)
  const add = async (fid, fname) => {
    setBusyId(fid)
    try {
      await post(`/folders/${fid}/items`, { meeting_id: meeting.id })
      toast(`Added to ${fname}`)
      onClose()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusyId(null)
    }
  }
  const create = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
    setBusyId('new')
    try {
      const r = await post('/folders', { name: name.trim(), shared: false })
      await add(r.id, name.trim())
    } catch (err) {
      toast(err.message, 'error'); setBusyId(null)
    }
  }
  return (
    <Modal title={`Add “${meeting.title}” to a folder`} onClose={onClose}>
      {folders.loading && <Loading />}
      <ErrorNote error={folders.error} retry={folders.reload} />
      {folders.data && (
        <ul className="ls-pick">
          {folders.data.length === 0 && <li className="muted">You don't have any folders yet. Create one below.</li>}
          {folders.data.map((f) => (
            <li key={f.id}>
              <span>
                <b>{f.name}</b>
                <span className="muted small"> · {f.count} {f.count === 1 ? 'meeting' : 'meetings'}{f.shared ? ' · shared with everyone' : ''}</span>
              </span>
              <button className="secondary small-btn" disabled={!!busyId} onClick={() => add(f.id, f.name)}>
                {busyId === f.id ? 'Adding' : 'Add'}
              </button>
            </li>
          ))}
        </ul>
      )}
      <form className="ls-inline-form" onSubmit={create}>
        <Field label="New folder">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Folder name" />
        </Field>
        <button className="primary" disabled={!name.trim() || !!busyId}>{busyId === 'new' ? 'Creating' : 'Create and add'}</button>
      </form>
    </Modal>
  )
}

export function meetingMenu(m, { toast, onAddToFolder, extra = [] }) {
  return (
    <Menu label={`Actions for ${m.title}`} items={[
      { label: 'Open', onClick: () => go(`#/meeting/${m.id}`) },
      { label: 'Add to folder', onClick: () => onAddToFolder(m) },
      { label: 'Copy link', onClick: async () => {
        const ok = await copyText(absLink(`#/meeting/${m.id}`))
        toast(ok ? 'Link copied' : 'Could not copy the link. Copy it from the address bar instead.', ok ? 'ok' : 'error')
      } },
      ...extra,
    ]} />
  )
}

export function MeetingsPage({ scope }) {
  const { teams, toast, openUpload } = useApp()
  const [team, setTeam] = useState('')
  const [type, setType] = useState('')
  const [filter, setFilter] = useState('')
  const [folderFor, setFolderFor] = useState(null)
  const types = useApi('/org/meeting-types')
  const list = useApi(`/meetings${qs({ scope, team_id: scope === 'team' ? team : '', type_id: type })}`, {
    poll: (rows) => (rows?.some((m) => busy(m.stage)) ? 3000 : 20000),
  })
  const rows = useMemo(() => {
    const f = filter.trim().toLowerCase()
    const all = list.data || []
    if (!f) return all
    return all.filter((m) => [m.title, m.overview, ...(m.speakers || []), m.owner?.name].filter(Boolean).join(' ').toLowerCase().includes(f))
  }, [list.data, filter])
  const groups = groupByDay(rows)
  const filtered = !!(team || type || filter)

  const empty = filtered ? (
    <Empty title="No meetings match these filters">
      <button className="link" onClick={() => { setTeam(''); setType(''); setFilter('') }}>Clear filters</button>
    </Empty>
  ) : scope === 'mine' ? (
    <Empty title="Upload your first recording" action={<button className="primary" onClick={() => openUpload()}>Upload recording</button>}>
      Sounding transcribes it, works out who spoke, and writes notes and action items. You can also drop an audio or video file anywhere on this page.
    </Empty>
  ) : scope === 'team' ? (
    <Empty title="No team calls yet">
      When a teammate shares a meeting with a team you're on, or with everyone in the organization, it shows up here.
    </Empty>
  ) : (
    <Empty title="Nothing shared with you yet">
      Meetings appear here when someone shares one with you, mentions you in a comment, or lists your email as an attendee.
    </Empty>
  )

  return (
    <main className="page">
      <PageHead title={TITLES[scope]} sub={SUBS[scope]} />
      <div className="ls-filters">
        <label className="ls-filter-box">
          <span className="sr">Filter these meetings</span>
          <input type="search" placeholder="Filter by title, speaker or notes" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
        {scope === 'team' && (
          <label>
            <span className="sr">Team</span>
            <select value={team} onChange={(e) => setTeam(e.target.value)}>
              <option value="">All teams</option>
              {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
        )}
        {types.data?.length > 0 && (
          <label>
            <span className="sr">Meeting type</span>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All meeting types</option>
              {types.data.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
        )}
      </div>
      <ErrorNote error={list.error} retry={list.reload} />
      {list.loading && !list.data ? <Loading /> : !rows.length ? empty : groups.map((g) => (
        <section key={g.key} className="ls-day">
          <h2 className="ls-day-head">{g.label}</h2>
          <MeetingRows rows={g.rows} rowActions={(m) => meetingMenu(m, { toast, onAddToFolder: setFolderFor })} />
        </section>
      ))}
      {folderFor && <AddToFolderModal meeting={folderFor} onClose={() => setFolderFor(null)} />}
    </main>
  )
}

/* ---------- search ---------- */
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

export function Highlighted({ text, q }) {
  const terms = [...new Set([q.trim(), ...q.toLowerCase().split(/\W+/)].filter((w) => w && w.length > 1))]
  if (!terms.length) return text
  const re = new RegExp(`(${terms.sort((a, b) => b.length - a.length).map(escapeRe).join('|')})`, 'gi')
  return text.split(re).map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : p))
}

export function SearchPage({ q }) {
  const res = useApi(q.trim().length >= 2 ? `/search${qs({ q: q.trim() })}` : null)
  return (
    <main className="page">
      <PageHead title={q ? `Results for “${q}”` : 'Search'}
        sub={res.data ? `${res.data.length} ${res.data.length === 1 ? 'meeting mentions' : 'meetings mention'} this.` : undefined}
        actions={q && <a className="secondary" href={`#/ask?q=${encodeURIComponent(q)}`}>Ask Sounding instead</a>} />
      {q.trim().length < 2 && <Empty title="Type at least two characters">Search looks through titles, notes and every transcript you can see.</Empty>}
      <ErrorNote error={res.error} retry={res.reload} />
      {res.loading && <Loading label="Searching" />}
      {res.data && !res.data.length && (
        <Empty title={`No meeting mentions “${q}”`}>
          Try a different word, or <a href={`#/ask?q=${encodeURIComponent(q)}`}>ask Sounding</a>, which finds related moments even when the exact words differ.
        </Empty>
      )}
      {res.data?.length > 0 && (
        <ol className="ls-results">
          {res.data.map((r) => (
            <li key={r.id} className="ls-result">
              <a className="ls-result-title" href={`#/meeting/${r.id}`}><Highlighted text={r.title} q={q} /></a>
              <p className="muted small">{fmtDate(r.date)}</p>
              {r.moments.length > 0 && (
                <ul className="ls-moments">
                  {r.moments.map((mo, i) => (
                    <li key={i}>
                      <a className="clock" href={`#/meeting/${r.id}?t=${Math.floor(mo.t)}`} aria-label={`Play from ${fmtClock(mo.t)}`}>{fmtClock(mo.t)}</a>
                      <span><b>{mo.speaker}:</b> <Highlighted text={mo.text} q={q} /></span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </main>
  )
}
