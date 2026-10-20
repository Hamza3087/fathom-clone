// Folders (whole meetings), playlists (clips) and companies (attendee email domains).
import React, { useEffect, useRef, useState } from 'react'
import { post, patch, put, del, useApi, clipUrl } from '../api.js'
import { Modal, Menu, Field, Loading, ErrorNote, Empty, PageHead, Spinner, fmtClock, fmtDate, fmtDay, copyText, absLink, go, useApp } from '../ui.jsx'
import { MeetingRows } from '../rows.jsx'
import { AddToFolderModal, meetingMenu } from './Meetings.jsx'
import './lists.css'

/* ---------- Ask with citations ---------- */
const toSeconds = (s) => s.split(':').reduce((a, n) => a * 60 + Number(n), 0)

// Turns "[M3 12:34]" and "[M3]" in an answer into links to that meeting (and moment).
export function Cited({ text, refs }) {
  return text.split(/(\[M\d+(?:\s+\d+(?::\d{2}){1,2})?\])/g).map((p, i) => {
    const m = p.match(/^\[(M\d+)(?:\s+(\d+(?::\d{2}){1,2}))?\]$/)
    const ref = m && refs?.[m[1]]
    if (!ref) return <React.Fragment key={i}>{p}</React.Fragment>
    const t = m[2] ? toSeconds(m[2]) : null
    return (
      <a key={i} className="ls-cite" href={`#/meeting/${ref.id}${t != null ? `?t=${t}` : ''}`} title={`${ref.title}, ${fmtDay(ref.date)}`}>
        {ref.title.length > 28 ? `${ref.title.slice(0, 26)}…` : ref.title}{m[2] ? ` ${m[2]}` : ''}
      </a>
    )
  })
}

function AskBox({ title, body, placeholder, suggestions = [] }) {
  const [q, setQ] = useState('')
  const [thread, setThread] = useState([])
  const [pending, setPending] = useState(null)
  const ask = async (text) => {
    const question = (text ?? q).trim()
    if (!question || pending) return
    setQ(''); setPending(question)
    try {
      const r = await post('/ask', { question, ...body })
      setThread((t) => [...t, { question, ...r }])
    } catch (e) {
      setThread((t) => [...t, { question, error: e.message }])
    } finally {
      setPending(null)
    }
  }
  return (
    <section className="ls-ask" aria-label={title}>
      <h2>{title}</h2>
      {!thread.length && !pending && suggestions.length > 0 && (
        <div className="ls-suggest">
          {suggestions.map((s) => <button key={s} className="secondary small-btn" onClick={() => ask(s)}>{s}</button>)}
        </div>
      )}
      <div aria-live="polite">
        {thread.map((m, i) => (
          <div key={i} className="ls-qa">
            <p className="ls-q">{m.question}</p>
            <div className={`ls-a${m.error ? ' err' : ''}`}>
              {m.error ? m.error : <p><Cited text={m.answer} refs={m.refs} /></p>}
              {m.searched != null && <p className="muted small">Searched {m.searched} {m.searched === 1 ? 'meeting' : 'meetings'}.</p>}
            </div>
          </div>
        ))}
        {pending && (
          <div className="ls-qa">
            <p className="ls-q">{pending}</p>
            <div className="ls-a"><Spinner label="Reading the transcripts" /></div>
          </div>
        )}
      </div>
      <form className="ls-ask-form" onSubmit={(e) => { e.preventDefault(); ask() }}>
        <label className="sr" htmlFor={`ask-${title}`}>{title}</label>
        <input id={`ask-${title}`} value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} />
        <button className="primary" disabled={!!pending || !q.trim()}>Ask</button>
      </form>
    </section>
  )
}

/* ---------- shared create/rename dialog ---------- */
function CollectionModal({ kind, initial, onClose, onSaved }) {
  const { toast } = useApp()
  const [name, setName] = useState(initial?.name || '')
  const [shared, setShared] = useState(!!initial?.shared)
  const [busy, setBusy] = useState(false)
  const base = kind === 'folder' ? '/folders' : '/playlists'
  const save = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    try {
      const r = initial ? await patch(`${base}/${initial.id}`, { name: name.trim(), shared }) : await post(base, { name: name.trim(), shared })
      toast(initial ? 'Saved' : `${kind === 'folder' ? 'Folder' : 'Playlist'} created`)
      onSaved(initial ? initial.id : r.id)
    } catch (err) {
      toast(err.message, 'error'); setBusy(false)
    }
  }
  const noun = kind === 'folder' ? 'folder' : 'playlist'
  return (
    <Modal title={initial ? `Edit ${noun}` : `New ${noun}`} onClose={onClose} footer={
      <>
        <button className="secondary" type="button" onClick={onClose}>Cancel</button>
        <button className="primary" form="coll-form" disabled={busy || !name.trim()}>{initial ? 'Save' : `Create ${noun}`}</button>
      </>
    }>
      <form id="coll-form" className="stack-form" onSubmit={save}>
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={80} /></Field>
        <label className="ls-check">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          <span>
            Share with everyone in the organization
            <span className="field-hint">
              {kind === 'folder'
                ? 'Everyone in the organization can open every meeting in a shared folder, even ones that were private.'
                : 'Everyone in the organization can play the clips in a shared playlist.'}
            </span>
          </span>
        </label>
      </form>
    </Modal>
  )
}

function CollectionList({ kind }) {
  const { me, isAdmin, toast } = useApp()
  const base = kind === 'folder' ? '/folders' : '/playlists'
  const list = useApi(base)
  const [editing, setEditing] = useState(null) // null | 'new' | item
  const remove = async (it) => {
    if (!confirm(`Delete “${it.name}”? ${kind === 'folder' ? 'The meetings in it are not deleted.' : 'The clips in it are not deleted.'}`)) return
    try { await del(`${base}/${it.id}`); toast('Deleted'); list.reload() } catch (e) { toast(e.message, 'error') }
  }
  const noun = kind === 'folder' ? 'folder' : 'playlist'
  return (
    <main className="page">
      <PageHead title={kind === 'folder' ? 'Folders' : 'Playlists'}
        sub={kind === 'folder' ? 'Keep whole meetings together, like every call with one customer or one project.'
          : 'Collect clips from different meetings, like customer quotes or training moments.'}
        actions={<button className="primary" onClick={() => setEditing('new')}>New {noun}</button>} />
      <ErrorNote error={list.error} retry={list.reload} />
      {list.loading && !list.data && <Loading />}
      {list.data && !list.data.length && (
        <Empty title={`No ${noun}s yet`} action={<button className="secondary" onClick={() => setEditing('new')}>Create a {noun}</button>}>
          {kind === 'folder' ? 'Add meetings to a folder from any meeting list or meeting page.' : 'Add clips from the Highlights tab on a meeting.'}
        </Empty>
      )}
      {list.data?.length > 0 && (
        <ul className="ls-cards">
          {list.data.map((it) => {
            const canEdit = it.owner_id === me.id || isAdmin
            return (
              <li key={it.id} className="ls-card">
                <a href={`#/${kind === 'folder' ? 'folders' : 'playlists'}/${it.id}`} className="ls-card-link">
                  <b>{it.name}</b>
                  <span className="muted small">
                    {it.count} {kind === 'folder' ? (it.count === 1 ? 'meeting' : 'meetings') : (it.count === 1 ? 'clip' : 'clips')} · {it.owner_id === me.id ? 'yours' : it.owner_name}
                  </span>
                  {it.shared ? <span className="tag">Shared with everyone</span> : <span className="tag quiet">Private</span>}
                </a>
                {canEdit && (
                  <Menu label={`Actions for ${it.name}`} items={[
                    { label: 'Rename or change sharing', onClick: () => setEditing(it) },
                    { label: `Delete ${noun}`, danger: true, onClick: () => remove(it) },
                  ]} />
                )}
              </li>
            )
          })}
        </ul>
      )}
      {editing && (
        <CollectionModal kind={kind} initial={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
          onSaved={(id) => { setEditing(null); editing === 'new' ? go(`#/${kind === 'folder' ? 'folders' : 'playlists'}/${id}`) : list.reload() }} />
      )}
    </main>
  )
}

/* ---------- folders ---------- */
export function FoldersPage() {
  return <CollectionList kind="folder" />
}

export function FolderPage({ id }) {
  const { toast } = useApp()
  const f = useApi(`/folders/${id}`)
  const [editing, setEditing] = useState(false)
  const [folderFor, setFolderFor] = useState(null)
  if (f.loading && !f.data) return <Loading />
  if (f.error) return <main className="page"><a className="back" href="#/folders">Folders</a><ErrorNote error={f.error} /></main>
  const d = f.data
  const removeItem = async (m) => {
    try { await del(`/folders/${id}/items/${m.id}`); toast('Removed from folder'); f.reload() } catch (e) { toast(e.message, 'error') }
  }
  const remove = async () => {
    if (!confirm(`Delete “${d.name}”? The meetings in it are not deleted.`)) return
    try { await del(`/folders/${id}`); toast('Folder deleted'); go('#/folders') } catch (e) { toast(e.message, 'error') }
  }
  return (
    <main className="page">
      <a className="back" href="#/folders">Folders</a>
      <PageHead title={d.name}
        sub={`${d.meetings.length} ${d.meetings.length === 1 ? 'meeting' : 'meetings'} · ${d.shared ? 'shared with everyone in the organization' : 'private'}`}
        actions={d.can_edit && (
          <Menu label="Folder actions" className="secondary" items={[
            { label: 'Rename or change sharing', onClick: () => setEditing(true) },
            { label: 'Delete folder', danger: true, onClick: remove },
          ]}>Edit folder</Menu>
        )} />
      <div className="ls-split">
        <div className="ls-split-main">
          <MeetingRows rows={d.meetings}
            empty={<Empty title="This folder is empty">Open a meeting and choose Add to folder, or use the menu on any meeting list.</Empty>}
            rowActions={(m) => meetingMenu(m, {
              toast, onAddToFolder: setFolderFor,
              extra: d.can_edit ? [null, { label: 'Remove from folder', danger: true, onClick: () => removeItem(m) }] : [],
            })} />
        </div>
        {d.meetings.length > 0 && (
          <AskBox title="Ask about these meetings" body={{ folder_id: id, scope: 'all' }} placeholder="What do these meetings have in common?"
            suggestions={['What were the main decisions?', 'What open questions are still unresolved?']} />
        )}
      </div>
      {editing && <CollectionModal kind="folder" initial={d} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); f.reload() }} />}
      {folderFor && <AddToFolderModal meeting={folderFor} onClose={() => setFolderFor(null)} />}
    </main>
  )
}

/* ---------- playlists ---------- */
export function PlaylistsPage() {
  return <CollectionList kind="playlist" />
}

export function PlaylistPage({ id }) {
  const { toast } = useApp()
  const p = useApi(`/playlists/${id}`)
  const [current, setCurrent] = useState(0)
  const [autoplay, setAutoplay] = useState(false) // true while "Play all" runs
  const [editing, setEditing] = useState(false)
  const player = useRef()
  const items = p.data?.items || []
  const clip = items[current]

  useEffect(() => {
    if (autoplay && player.current) player.current.play().catch(() => {})
  }, [current, autoplay])

  if (p.loading && !p.data) return <Loading />
  if (p.error) return <main className="page"><a className="back" href="#/playlists">Playlists</a><ErrorNote error={p.error} /></main>
  const d = p.data

  const playAt = (i) => { setCurrent(i); setAutoplay(true) }
  const ended = () => {
    if (autoplay && current < items.length - 1) setCurrent(current + 1)
    else setAutoplay(false)
  }
  const move = async (i, dir) => {
    const order = items.map((it) => it.id)
    const j = i + dir
    if (j < 0 || j >= order.length) return
    ;[order[i], order[j]] = [order[j], order[i]]
    try {
      await put(`/playlists/${id}/order`, { order })
      if (current === i) setCurrent(j); else if (current === j) setCurrent(i)
      p.reload()
    } catch (e) { toast(e.message, 'error') }
  }
  const removeItem = async (it) => {
    try {
      await del(`/playlists/${id}/items/${it.id}`)
      setCurrent((c) => Math.max(0, Math.min(c, items.length - 2)))
      toast('Removed from playlist'); p.reload()
    } catch (e) { toast(e.message, 'error') }
  }
  const copyClip = async (it) => {
    try {
      const r = await post(`/meetings/${it.meeting_id}/links`, { access: 'org', highlight_id: it.id })
      const ok = await copyText(absLink(r.link))
      toast(ok ? 'Clip link copied. People need to log in to open it.' : 'Could not copy the link.', ok ? 'ok' : 'error')
    } catch (e) { toast(e.message, 'error') }
  }
  const toggleShare = async () => {
    try { await patch(`/playlists/${id}`, { name: d.name, shared: !d.shared }); toast(d.shared ? 'Playlist is private now' : 'Shared with everyone'); p.reload() }
    catch (e) { toast(e.message, 'error') }
  }
  const remove = async () => {
    if (!confirm(`Delete “${d.name}”? The clips in it are not deleted.`)) return
    try { await del(`/playlists/${id}`); toast('Playlist deleted'); go('#/playlists') } catch (e) { toast(e.message, 'error') }
  }
  const total = items.reduce((a, it) => a + (it.end - it.start), 0)

  return (
    <main className="page">
      <a className="back" href="#/playlists">Playlists</a>
      <PageHead title={d.name}
        sub={`${items.length} ${items.length === 1 ? 'clip' : 'clips'} · ${fmtClock(total)} · ${d.shared ? 'shared with everyone in the organization' : 'private'}`}
        actions={<>
          {items.length > 0 && <button className="primary" onClick={() => playAt(0)}>Play all</button>}
          {d.can_edit && (
            <Menu label="Playlist actions" className="secondary" items={[
              { label: 'Rename', onClick: () => setEditing(true) },
              { label: d.shared ? 'Make private' : 'Share with everyone', onClick: toggleShare },
              { label: 'Delete playlist', danger: true, onClick: remove },
            ]}>Edit playlist</Menu>
          )}
        </>} />
      {!items.length ? (
        <Empty title="No clips yet">Open a meeting, make a highlight, and choose Add to playlist.</Empty>
      ) : (
        <div className="ls-playlist">
          <section className="ls-player" aria-label="Clip player">
            {clip && (clip.has_video
              ? <video key={clip.id} ref={player} src={clipUrl(clip.id, 'video')} controls onEnded={ended} className="ls-media" />
              : <audio key={clip.id} ref={player} src={clipUrl(clip.id)} controls onEnded={ended} className="ls-audio" />)}
            {clip && (
              <div className="ls-now">
                <b>{clip.title}</b>
                <span className="muted small">
                  {clip.can_open_meeting
                    ? <a href={`#/meeting/${clip.meeting_id}?t=${Math.floor(clip.start)}`}>{clip.meeting_title}</a>
                    : clip.meeting_title} · at {fmtClock(clip.start)}
                </span>
                {clip.note && <p className="ls-note">{clip.note}</p>}
                {autoplay && <p className="muted small">Playing all · clip {current + 1} of {items.length}</p>}
              </div>
            )}
          </section>
          <ol className="ls-clips">
            {items.map((it, i) => (
              <li key={it.id} className={i === current ? 'on' : ''}>
                <button className="ls-clip-main" onClick={() => playAt(i)} aria-label={`Play ${it.title}`}>
                  <span className="ls-clip-num">{i + 1}</span>
                  <span className="ls-clip-text">
                    <b>{it.title}</b>
                    <span className="muted small">{it.meeting_title} · {fmtClock(it.end - it.start)} · {it.user_name}</span>
                  </span>
                </button>
                <Menu label={`Actions for ${it.title}`} items={[
                  ...(it.can_open_meeting ? [{ label: 'Open in meeting', onClick: () => go(`#/meeting/${it.meeting_id}?t=${Math.floor(it.start)}`) },
                    { label: 'Copy clip link', onClick: () => copyClip(it) }] : []),
                  ...(d.can_edit ? [
                    { label: 'Move up', disabled: i === 0, onClick: () => move(i, -1) },
                    { label: 'Move down', disabled: i === items.length - 1, onClick: () => move(i, 1) },
                    null,
                    { label: 'Remove from playlist', danger: true, onClick: () => removeItem(it) },
                  ] : []),
                ]} />
              </li>
            ))}
          </ol>
        </div>
      )}
      {editing && <CollectionModal kind="playlist" initial={d} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); p.reload() }} />}
    </main>
  )
}

/* ---------- companies ---------- */
export function CompaniesPage() {
  const list = useApi('/companies')
  return (
    <main className="page">
      <PageHead title="Companies" sub="Every outside company you've met with, grouped by the email domains of meeting attendees." />
      <ErrorNote error={list.error} retry={list.reload} />
      {list.loading && !list.data && <Loading />}
      {list.data && !list.data.length && (
        <Empty title="No companies yet">
          Add attendee emails when you upload a recording, or on the meeting page. Anyone outside your organization's email domains
          is grouped here by company. Personal addresses like gmail.com are left out.
        </Empty>
      )}
      {list.data?.length > 0 && (
        <ul className="ls-companies">
          {list.data.map((c) => (
            <li key={c.domain}>
              <a href={`#/companies/${encodeURIComponent(c.domain)}`} className="ls-company">
                <span className="ls-logo" aria-hidden="true">{c.domain[0].toUpperCase()}</span>
                <span className="ls-company-text">
                  <b>{c.domain}</b>
                  <span className="muted small">
                    {c.meetings} {c.meetings === 1 ? 'meeting' : 'meetings'} · {c.people.length} {c.people.length === 1 ? 'person' : 'people'} · last met {fmtDay(c.last)}
                  </span>
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}

export function CompanyPage({ domain }) {
  const { toast } = useApp()
  const c = useApi(`/companies/${encodeURIComponent(domain)}`)
  const [folderFor, setFolderFor] = useState(null)
  if (c.loading && !c.data) return <Loading />
  if (c.error) return <main className="page"><a className="back" href="#/companies">Companies</a><ErrorNote error={c.error} /></main>
  const d = c.data
  return (
    <main className="page">
      <a className="back" href="#/companies">Companies</a>
      <PageHead title={d.domain} sub={`${d.meetings.length} ${d.meetings.length === 1 ? 'meeting' : 'meetings'} you can see${d.meetings[0] ? ` · last met ${fmtDate(d.meetings[0].date)}` : ''}`} />
      <div className="ls-split">
        <div className="ls-split-main">
          <h2>Meetings</h2>
          <MeetingRows rows={d.meetings} empty={<Empty title="No meetings you can see">Meetings with this company may be private to someone else.</Empty>}
            rowActions={(m) => meetingMenu(m, { toast, onAddToFolder: setFolderFor })} />
        </div>
        <aside className="ls-side">
          <section>
            <h2>People</h2>
            <ul className="ls-people">
              {d.people.map((p) => (
                <li key={p.email}>
                  <span className="ls-person">{p.email}</span>
                  <span className="muted small">{p.meetings} {p.meetings === 1 ? 'meeting' : 'meetings'}</span>
                </li>
              ))}
            </ul>
          </section>
          {d.meetings.length > 0 && (
            <AskBox title={`Ask about ${d.domain}`} body={{ company: d.domain, scope: 'all' }} placeholder="What do they care about most?"
              suggestions={['What are their biggest concerns?', 'What did we promise them?']} />
          )}
        </aside>
      </div>
      {folderFor && <AddToFolderModal meeting={folderFor} onClose={() => setFolderFor(null)} />}
    </main>
  )
}
