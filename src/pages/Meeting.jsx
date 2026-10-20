// The meeting page: player, speaker strip, transcript, and the notes panel (summary, actions, Ask, clips, comments, insights).
import './meeting.css'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { get, post, patch, del, useApi, mediaUrl, downloadUrl, STAGES, busy } from '../api.js'
import {
  Modal, Menu, Tabs, Field, Spinner, Loading, ErrorNote, Empty, Avatar, fmtClock, fmtDate, fmtAgo, speakerHue,
  copyText, absLink, go, setQuery, useApp, useAction, VIS_LABEL,
} from '../ui.jsx'

const LANGS = ['Spanish', 'Portuguese', 'German', 'French', 'Italian', 'Dutch', 'Arabic', 'Urdu', 'Hindi', 'Chinese', 'Japanese']
const ORDER = ['queued', 'converting', 'speakers', 'transcribing', 'summarizing', 'finishing']
const TABS = ['summary', 'actions', 'ask', 'clips', 'comments', 'insights']
const nameOf = (m, label) => (m.names && m.names[label]) || label

function triggerDownload(url) {
  const a = document.createElement('a')
  a.href = url
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

export function MeetingPage({ id, query }) {
  const r = useApi(`/meetings/${id}`, { poll: (d) => (busy(d.stage) || d.pending?.length ? 2500 : 0) })
  const m = r.data
  if (!m && r.loading) return <Loading label="Opening meeting" />
  if (!m && r.error) {
    return (
      <main className="page">
        <a className="back" href="#/">My calls</a>
        <ErrorNote error={r.error} retry={r.reload} />
      </main>
    )
  }
  if (busy(m.stage) || m.stage === 'failed') return <Processing m={m} />
  return <Ready m={m} setM={r.setData} reload={r.reload} query={query} />
}

/* ---------- processing ---------- */
function Processing({ m }) {
  const { toast } = useApp()
  const at = ORDER.indexOf(m.stage)
  const remove = async () => {
    if (!window.confirm(`Delete “${m.title}”? The upload is removed from the server.`)) return
    try { await del(`/meetings/${m.id}`); toast('Meeting deleted'); go('#/') } catch (e) { toast(e.message, 'error') }
  }
  return (
    <main className="page">
      <a className="back" href="#/">My calls</a>
      <h1 className="mt-proc-title">{m.title}</h1>
      <p className="muted">{fmtDate(m.date)}</p>
      {m.stage === 'failed' ? (
        <div className="mt-proc">
          <p>This recording couldn’t be processed.</p>
          {m.error && <p className="muted">{m.error}</p>}
          {m.can_edit && <button className="primary" onClick={remove}>Delete and try another file</button>}
        </div>
      ) : (
        <ol className="mt-steps">
          {ORDER.map((s, i) => (
            <li key={s} className={i < at ? 'done' : i === at ? 'now' : ''}>
              {i === at && <Spinner />}
              {s === 'queued' && i === at && m.queue ? `${m.queue} ahead in line` : STAGES[s]}
            </li>
          ))}
        </ol>
      )}
    </main>
  )
}

/* ---------- player ---------- */
function usePlayer(m) {
  const el = useRef()
  const stopAt = useRef(null)
  const [now, setNow] = useState(0)
  const [dur, setDur] = useState(m.duration || 1)
  const [playing, setPlaying] = useState(false)
  const [rate, setRateState] = useState(1)
  const src = mediaUrl(m.id, m.has_video ? 'video' : 'audio')
  const props = {
    ref: el,
    src,
    preload: 'metadata',
    onTimeUpdate: (e) => {
      const t = e.currentTarget.currentTime
      setNow(t)
      if (stopAt.current != null && t >= stopAt.current) { stopAt.current = null; e.currentTarget.pause() }
    },
    onLoadedMetadata: (e) => isFinite(e.currentTarget.duration) && setDur(e.currentTarget.duration),
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
  }
  const seek = useCallback((t, { play = true, until = null } = {}) => {
    const a = el.current
    if (!a) return
    stopAt.current = until
    a.currentTime = Math.max(0, t)
    setNow(Math.max(0, t))
    if (play) a.play().catch(() => {})
  }, [])
  return {
    el, props, now, dur, playing, rate, hasVideo: !!m.has_video, seek,
    toggle: () => { const a = el.current; if (!a) return; stopAt.current = null; a.paused ? a.play().catch(() => {}) : a.pause() },
    skip: (d) => seek(Math.min(dur, Math.max(0, (el.current?.currentTime || 0) + d)), { play: !el.current?.paused }),
    setRate: (r) => { setRateState(r); if (el.current) el.current.playbackRate = r },
  }
}

const Svg = ({ d, size = 18 }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true"><path d={d} fill="currentColor" /></svg>
)
const PLAY = 'M8 5v14l11-7z'
const PAUSE = 'M7 5h4v14H7zM13 5h4v14h-4z'
const BACK = 'M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z'
const FWD = 'M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z'

/* ---------- ready view ---------- */
function Ready({ m, setM, reload, query }) {
  const app = useApp()
  const { toast } = app
  const player = usePlayer(m)
  const highlights = useApi(`/meetings/${m.id}/highlights`)
  const comments = useApi(`/meetings/${m.id}/comments`)
  const [tab, setTabState] = useState(TABS.includes(query.tab) ? query.tab : 'summary')
  const setTab = (t) => { setTabState(t); setQuery({ tab: t }) }
  const [thread, setThread] = useState([])
  const [commentAt, setCommentAt] = useState(null)
  const [editClip, setEditClip] = useState(null)
  const [modal, setModal] = useState(null) // 'share' | 'folder' | 'followup'
  const [busyKey, run] = useAction(toast)
  const seekedFromQuery = useRef(false)

  useEffect(() => {
    if (TABS.includes(query.tab) && query.tab !== tab) setTabState(query.tab)
  }, [query.tab]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Deep links like #/meeting/x?t=123 jump to that moment once the media is ready.
    if (seekedFromQuery.current || query.t == null) return
    const t = parseFloat(query.t)
    if (!isFinite(t)) return
    seekedFromQuery.current = true
    const a = player.el.current
    const go2 = () => player.seek(t, { play: false })
    if (a && a.readyState >= 1) go2()
    else a?.addEventListener('loadedmetadata', go2, { once: true })
  }, [query.t, player])

  const save = async (body, ok) => {
    const r = await run('save', () => patch(`/meetings/${m.id}`, body), ok)
    if (r) setM(r)
    return r
  }

  const makeClip = async (start, end, title = '') => {
    const h = await run('clip', () => post(`/meetings/${m.id}/highlights`, { start, end, title }))
    if (h) {
      highlights.setData((xs) => [...(xs || []), h].sort((a, b) => a.start - b.start))
      setEditClip(h.id)
      setTab('clips')
      toast('Clip saved')
    }
  }

  const trim = async (mode, start, end) => {
    const what = { range: `the part from ${fmtClock(start)} to ${fmtClock(end)}`, before: `everything before ${fmtClock(start)}`, after: `everything after ${fmtClock(start)}` }[mode]
    if (!window.confirm(`Trim ${what}? The removed part disappears from the recording, transcript, clips and comments. This can't be undone here.`)) return
    const r = await run('trim', () => post(`/meetings/${m.id}/trim`, { mode, start, end }), 'Recording trimmed')
    if (r) { setM(r); highlights.reload(); comments.reload() }
  }

  const remove = async () => {
    if (!window.confirm(`Delete “${m.title}”? The recording, transcript, notes, clips and comments are removed for everyone.`)) return
    const r = await run('delete', () => del(`/meetings/${m.id}`), 'Meeting deleted')
    if (r) go('#/')
  }

  const open = m.actions.filter((a) => !a.done).length
  return (
    <main className="page wide mt-page">
      <Header m={m} save={save} busyKey={busyKey} setModal={setModal} remove={remove} />
      <div className="mt-grid">
        <section className="mt-left" aria-label="Recording and transcript">
          <Player m={m} player={player} highlights={highlights.data || []} comments={comments.data || []}
            save={save} makeClip={makeClip} busyKey={busyKey} />
          <Transcript m={m} player={player} makeClip={makeClip} trim={trim}
            onComment={(t) => { setCommentAt(t); setTab('comments') }} />
        </section>
        <aside className="mt-right" aria-label="Meeting notes">
          <Tabs value={tab} onChange={setTab} className="mt-tabs" tabs={[
            ['summary', 'Summary'], ['actions', `Action items${open ? ` (${open})` : ''}`], ['ask', 'Ask'],
            ['clips', `Clips${highlights.data?.length ? ` (${highlights.data.length})` : ''}`],
            ['comments', `Comments${comments.data?.length ? ` (${comments.data.length})` : ''}`], ['insights', 'Insights'],
          ]} />
          <div className="mt-panel">
            {tab === 'summary' && <SummaryTab m={m} seek={player.seek} reload={reload} />}
            {tab === 'actions' && <ActionsTab m={m} save={save} seek={player.seek} />}
            {tab === 'ask' && <AskTab m={m} seek={player.seek} thread={thread} setThread={setThread} />}
            {tab === 'clips' && <ClipsTab m={m} hl={highlights} player={player} editId={editClip} setEditId={setEditClip} />}
            {tab === 'comments' && <CommentsTab m={m} cm={comments} player={player} presetAt={commentAt} clearPreset={() => setCommentAt(null)} />}
            {tab === 'insights' && <InsightsTab m={m} seek={player.seek} reload={reload} />}
          </div>
          {m.timings && (
            <p className="mt-provenance">
              Speakers found in {m.timings.speakers}s, transcribed in {m.timings.transcribe}s, notes written in {m.timings.summarize}s.
            </p>
          )}
        </aside>
      </div>
      {modal === 'share' && <ShareModal m={m} setM={setM} onClose={() => setModal(null)} />}
      {modal === 'folder' && <FolderModal m={m} setM={setM} onClose={() => setModal(null)} />}
      {modal === 'followup' && <FollowupModal m={m} onClose={() => setModal(null)} />}
    </main>
  )
}

/* ---------- header ---------- */
function Header({ m, save, busyKey, setModal, remove }) {
  const types = useApi(m.can_edit ? '/org/meeting-types' : null)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(m.title)
  useEffect(() => setTitle(m.title), [m.title])
  const commit = async () => {
    setEditing(false)
    if (title.trim() && title.trim() !== m.title) await save({ title: title.trim() }, 'Title saved')
    else setTitle(m.title)
  }
  const vis = m.visibility === 'team' && m.team ? `Team: ${m.team.name}` : VIS_LABEL[m.visibility]
  const downloads = [
    { label: 'Audio (mp3)', onClick: () => triggerDownload(downloadUrl(m.id, 'mp3')) },
    ...(m.has_video ? [{ label: 'Video (mp4)', onClick: () => triggerDownload(downloadUrl(m.id, 'mp4')) }] : []),
    null,
    { label: 'Transcript (txt)', onClick: () => triggerDownload(downloadUrl(m.id, 'txt')) },
    { label: 'Subtitles (srt)', onClick: () => triggerDownload(downloadUrl(m.id, 'srt')) },
    { label: 'Subtitles (vtt)', onClick: () => triggerDownload(downloadUrl(m.id, 'vtt')) },
    { label: 'Notes (Markdown)', onClick: () => triggerDownload(downloadUrl(m.id, 'md')) },
  ]
  return (
    <header className="mt-head">
      <a className="back" href="#/">My calls</a>
      <div className="mt-title-row">
        {editing ? (
          <form className="mt-title-form" onSubmit={(e) => { e.preventDefault(); commit() }}>
            <label className="sr" htmlFor="mt-title">Meeting title</label>
            <input id="mt-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onBlur={commit}
              onKeyDown={(e) => e.key === 'Escape' && (setTitle(m.title), setEditing(false))} />
          </form>
        ) : (
          <h1>
            {m.can_edit
              ? <button className="mt-title-btn" onClick={() => setEditing(true)} title="Rename meeting">{m.title}</button>
              : m.title}
          </h1>
        )}
        <div className="mt-head-actions">
          <button className="primary" onClick={() => setModal('share')}>Share</button>
          <button className="secondary" onClick={() => setModal('followup')}>Draft follow-up email</button>
          <Menu label="Download" className="secondary" items={downloads}>Download</Menu>
          <Menu label="More actions" items={[
            { label: 'Add to folder', onClick: () => setModal('folder') },
            ...(m.can_edit ? [null, { label: 'Delete meeting', danger: true, onClick: remove, disabled: busyKey === 'delete' }] : []),
          ]} />
        </div>
      </div>
      <p className="mt-meta">
        {fmtDate(m.date)} · {fmtClock(m.duration)} · Recorded by {m.owner?.name}
      </p>
      <div className="mt-tags">
        {m.can_edit ? (
          <label className="mt-type">
            <span className="sr">Meeting type</span>
            <select value={m.meeting_type_id || ''} onChange={(e) => save({ meeting_type_id: e.target.value }, 'Meeting type updated')}>
              <option value="">No meeting type</option>
              {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
        ) : m.meeting_type && <span className="tag">{m.meeting_type.name}</span>}
        {m.companies?.map((c) => <a key={c} className="tag company" href={`#/companies/${encodeURIComponent(c)}`}>{c}</a>)}
        <button className="tag mt-vis" onClick={() => setModal('share')}>
          {vis}{m.shares?.length ? ` + ${m.shares.length} ${m.shares.length === 1 ? 'person' : 'people'}` : ''}
        </button>
      </div>
    </header>
  )
}

/* ---------- player + strip + legend ---------- */
function Player({ m, player, highlights, comments, save, makeClip, busyKey }) {
  const { now, dur, playing, rate, seek } = player
  const segs = m.transcript.map((l) => ({ l, left: (l.t / dur) * 100, width: (Math.max(0, l.end - l.t) / dur) * 100 }))
  const highlight = () => makeClip(Math.max(0, now - 20), Math.min(dur, now + 10))
  return (
    <div className="mt-player">
      {player.hasVideo
        ? <video {...player.props} className="mt-video" onClick={player.toggle} playsInline />
        : <audio {...player.props} className="sr" />}
      <div className="mt-controls">
        <button className="mt-play" onClick={player.toggle} aria-label={playing ? 'Pause' : 'Play'}>
          <Svg d={playing ? PAUSE : PLAY} />
        </button>
        <button className="icon-btn" onClick={() => player.skip(-15)} aria-label="Back 15 seconds" title="Back 15 seconds"><Svg d={BACK} /></button>
        <button className="icon-btn" onClick={() => player.skip(15)} aria-label="Forward 15 seconds" title="Forward 15 seconds"><Svg d={FWD} /></button>
        <span className="mt-time">{fmtClock(now)} <span>/ {fmtClock(dur)}</span></span>
        <label className="mt-rate">
          <span className="sr">Playback speed</span>
          <select value={rate} onChange={(e) => player.setRate(parseFloat(e.target.value))}>
            {[1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}x</option>)}
          </select>
        </label>
        <button className="secondary small-btn mt-hl-btn" onClick={highlight} disabled={busyKey === 'clip'} title="Saves the last 20 seconds and the next 10 as a clip">
          Highlight
        </button>
      </div>
      <div
        className="mt-strip"
        role="slider"
        tabIndex={0}
        aria-label="Recording position"
        aria-valuemin={0}
        aria-valuemax={Math.round(dur)}
        aria-valuenow={Math.round(now)}
        aria-valuetext={fmtClock(now)}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          seek(((e.clientX - r.left) / r.width) * dur)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') { e.preventDefault(); seek(Math.min(dur, now + 5), { play: playing }) }
          if (e.key === 'ArrowLeft') { e.preventDefault(); seek(Math.max(0, now - 5), { play: playing }) }
          if (e.key === ' ') { e.preventDefault(); player.toggle() }
        }}
      >
        {segs.map((s, i) => (
          <span key={i} className="mt-seg" style={{ left: `${s.left}%`, width: `${s.width}%`, background: speakerHue(m.speakers, s.l.s) }} />
        ))}
        <span className="mt-playhead" style={{ left: `${Math.min(100, (now / dur) * 100)}%` }} />
      </div>
      <div className="mt-marks" aria-hidden="true">
        {highlights.map((h) => (
          <span key={h.id} className="mt-mark clip" title={`Clip: ${h.title}`} style={{ left: `${(h.start / dur) * 100}%`, width: `${Math.max(0.6, ((h.end - h.start) / dur) * 100)}%` }} />
        ))}
        {comments.filter((c) => c.t != null).map((c) => (
          <span key={c.id} className="mt-mark comment" title={`${c.user_name}: ${c.body}`} style={{ left: `${(c.t / dur) * 100}%` }} />
        ))}
      </div>
      <Legend m={m} save={save} />
    </div>
  )
}

function Legend({ m, save }) {
  const per = m.metrics?.per_speaker || {}
  const [editing, setEditing] = useState(null)
  return (
    <ul className="mt-legend">
      {m.speakers.map((s) => (
        <li key={s}>
          <i style={{ background: speakerHue(m.speakers, s) }} />
          {m.can_edit ? (
            <span className="menu-wrap">
              <button className="link" onClick={() => setEditing(editing === s ? null : s)} title="Name this speaker or link a teammate"
                aria-expanded={editing === s}>
                {nameOf(m, s)}
              </button>
              {editing === s && <SpeakerEditor m={m} label={s} save={save} onClose={() => setEditing(null)} />}
            </span>
          ) : <span>{nameOf(m, s)}</span>}
          {per[s] && <b>{Math.round(per[s].talk_pct)}%</b>}
        </li>
      ))}
    </ul>
  )
}

function SpeakerEditor({ m, label, save, onClose }) {
  const { members } = useApp()
  const [name, setName] = useState(m.names?.[label] || '')
  const [uid, setUid] = useState(m.speaker_users?.[label] || '')
  const ref = useRef()
  useEffect(() => {
    const out = (e) => !ref.current?.contains(e.target) && onClose()
    const esc = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('mousedown', out)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc) }
  }, [onClose])
  const submit = async (e) => {
    e.preventDefault()
    const linked = m.speaker_users?.[label] || ''
    if (uid !== linked) {
      const su = { ...(m.speaker_users || {}) }
      if (uid) su[label] = uid
      else delete su[label]
      const r = await save({ speaker_users: su }, uid ? 'Speaker linked to teammate' : 'Teammate unlinked')
      if (!r) return
      // Linking sets the teammate's name; only send a typed name if it differs from theirs.
      const member = members.find((x) => x.id === uid)
      if (!uid && name.trim() !== (r.names?.[label] || '')) await save({ names: { ...r.names, [label]: name.trim() } })
      else if (uid && name.trim() && member && name.trim() !== member.name && name.trim() !== (m.names?.[label] || '')) {
        await save({ names: { ...r.names, [label]: name.trim() } })
      }
    } else if (name.trim() !== (m.names?.[label] || '')) {
      const names = { ...(m.names || {}) }
      if (name.trim()) names[label] = name.trim()
      else delete names[label]
      await save({ names }, 'Speaker renamed')
    }
    onClose()
  }
  return (
    <form ref={ref} className="menu left mt-speaker-pop" onSubmit={submit} aria-label={`Edit ${label}`}>
      <p className="muted small">{label}</p>
      <Field label="Name"><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={label} /></Field>
      <Field label="Teammate" hint="Linking a teammate counts this speaker in their coaching metrics.">
        <select value={uid} onChange={(e) => setUid(e.target.value)}>
          <option value="">Not a teammate</option>
          {members.filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
      </Field>
      <div className="row-gap">
        <button className="primary small-btn">Save</button>
        <button type="button" className="secondary small-btn" onClick={onClose}>Cancel</button>
      </div>
    </form>
  )
}

/* ---------- transcript ---------- */
function Transcript({ m, player, makeClip, trim, onComment }) {
  const [find, setFind] = useState('')
  const [clipStart, setClipStart] = useState(null)
  const box = useRef()
  const refs = useRef([])
  const follow = useRef(true)
  const [following, setFollowing] = useState(true)
  const stopFollowing = () => { follow.current = false; setFollowing(false) }
  const resume = () => { follow.current = true; setFollowing(true) }
  const { now } = player
  const active = m.transcript.reduce((a, l, i) => (l.t <= now ? i : a), -1)
  const q = find.trim().toLowerCase()
  const matches = q ? m.transcript.filter((l) => l.x.toLowerCase().includes(q)).length : 0

  useEffect(() => {
    const el = refs.current[active]
    const b = box.current
    if (!follow.current || !el || !b) return
    if (el.offsetTop < b.scrollTop || el.offsetTop + el.offsetHeight > b.scrollTop + b.clientHeight) {
      const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
      b.scrollTo({ top: el.offsetTop - b.clientHeight / 3, behavior: smooth ? 'smooth' : 'auto' })
    }
  }, [active])

  const mark = (text) => {
    if (!q) return text
    const out = []
    let i = 0
    const low = text.toLowerCase()
    for (;;) {
      const j = low.indexOf(q, i)
      if (j < 0) { out.push(text.slice(i)); break }
      out.push(text.slice(i, j), <mark key={j}>{text.slice(j, j + q.length)}</mark>)
      i = j + q.length
    }
    return out
  }

  return (
    <div className="mt-transcript-wrap">
      <div className="mt-find">
        <label className="sr" htmlFor="mt-find">Find in transcript</label>
        <input id="mt-find" type="search" placeholder="Find in transcript" value={find} onChange={(e) => setFind(e.target.value)} />
        {q && <span className="muted small">{matches} {matches === 1 ? 'match' : 'matches'}</span>}
        {!following && <button className="link small" onClick={resume}>Follow playback</button>}
      </div>
      {clipStart != null && (
        <div className="mt-clip-banner" role="status">
          <span>Clip starts at {fmtClock(clipStart)}. Choose “End clip here” on the line where it ends.</span>
          <button className="link" onClick={() => setClipStart(null)}>Cancel</button>
        </div>
      )}
      {!m.transcript.length && <Empty title="No speech was found in this recording." />}
      <ol ref={box} className="transcript mt-transcript" onWheel={stopFollowing} onTouchMove={stopFollowing}>
        {m.transcript.map((l, i) => {
          const dim = q && !l.x.toLowerCase().includes(q)
          const items = []
          if (clipStart == null) items.push({ label: 'Clip from here', onClick: () => setClipStart(l.t) })
          else if (l.end > clipStart) {
            items.push({ label: 'End clip here', onClick: () => { makeClip(clipStart, l.end); setClipStart(null) } })
          }
          items.push({ label: 'Comment at this moment', onClick: () => onComment(l.t) })
          if (m.can_edit) {
            items.push(null,
              { label: 'Trim this part', danger: true, onClick: () => trim('range', l.t, l.end) },
              { label: 'Trim everything before', danger: true, onClick: () => trim('before', l.t) },
              { label: 'Trim everything after', danger: true, onClick: () => trim('after', l.end) })
          }
          return (
            <li key={i} ref={(el) => (refs.current[i] = el)}
              className={`line mt-line${i === active ? ' active' : ''}${dim ? ' mt-dim' : ''}${clipStart === l.t ? ' mt-clip-start' : ''}`}>
              <button className="stamp" onClick={() => { resume(); player.seek(l.t) }} aria-label={`Play from ${fmtClock(l.t)}`}>
                {fmtClock(l.t)}
              </button>
              <div>
                <span className="who" style={{ color: speakerHue(m.speakers, l.s) }}>{nameOf(m, l.s)}</span>
                <p>{mark(l.x)}</p>
              </div>
              <span className="mt-line-menu"><Menu label={`Actions for the line at ${fmtClock(l.t)}`} items={items} /></span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

/* ---------- summary ---------- */
const SEEK_RE = /(\[\d+:\d{2}(?::\d{2})?\])/g
const toSec = (s) => s.replace(/[[\]]/g, '').split(':').map(Number).reduce((a, b) => a * 60 + b, 0)

const IS_STAMP = /^\[\d+:\d{2}(?::\d{2})?\]$/
function Cited({ text, seek }) {
  return text.split(SEEK_RE).map((p, i) => (IS_STAMP.test(p)
    ? <button key={i} className="link clock" onClick={() => seek(toSec(p))}>{p.slice(1, -1)}</button>
    : <React.Fragment key={i}>{p}</React.Fragment>))
}

function SummaryTab({ m, seek, reload }) {
  const { toast } = useApp()
  const templates = useApi('/templates')
  const [lang, setLang] = useState('')
  const [trans, setTrans] = useState(null)
  const [busyKey, run] = useAction(toast)
  const pending = m.pending?.includes('resummarize')
  const summary = trans || m.summary || { overview: '', sections: [] }

  useEffect(() => { setLang(''); setTrans(null) }, [m.template, m.summary])

  const pickTemplate = async (key) => {
    if (!key || key === m.template) return
    const r = await run('tpl', () => post(`/meetings/${m.id}/resummarize`, { template: key }), 'Rewriting the notes. This takes about half a minute.')
    if (r) reload()
  }
  const pickLang = async (l) => {
    setLang(l)
    if (!l) return setTrans(null)
    const r = await run('lang', () => post(`/meetings/${m.id}/translate`, { language: l }))
    if (r) setTrans(r)
    else setLang('')
  }
  const copy = async (withTimes) => {
    const lines = [m.title, '', summary.overview, '']
    summary.sections.filter((s) => s.bullets.length).forEach((s) => {
      lines.push(s.title)
      s.bullets.forEach((b) => lines.push(`- ${b.text}${withTimes && b.t != null ? ` (${fmtClock(b.t)})` : ''}`))
      lines.push('')
    })
    if (m.actions.length) {
      lines.push('Action items')
      m.actions.forEach((a) => lines.push(`- ${a.done ? '[x]' : '[ ]'} ${a.text}`))
    }
    toast((await copyText(lines.join('\n').trim())) ? 'Notes copied' : 'Copy failed. Select the text and copy it instead.', 'ok')
  }
  const tplName = templates.data?.find((t) => t.key === m.template)?.name

  return (
    <div className="mt-stack">
      <div className="mt-summary-bar">
        {m.can_edit ? (
          <label className="mt-inline-select">
            <span className="muted small">Template</span>
            <select value={m.template || 'general'} disabled={pending || busyKey === 'tpl'} onChange={(e) => pickTemplate(e.target.value)}>
              {templates.data?.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
              {!templates.data && <option value={m.template || 'general'}>{m.template || 'general'}</option>}
            </select>
          </label>
        ) : tplName && <span className="tag">{tplName}</span>}
        <label className="mt-inline-select">
          <span className="muted small">Language</span>
          <select value={lang} disabled={busyKey === 'lang' || pending} onChange={(e) => pickLang(e.target.value)}>
            <option value="">Original</option>
            {LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        <Menu label="Copy notes" className="secondary small-btn" items={[
          { label: 'Copy with timestamps', onClick: () => copy(true) },
          { label: 'Copy without timestamps', onClick: () => copy(false) },
        ]}>Copy notes</Menu>
      </div>
      {pending && <p className="mt-note"><Spinner label="Rewriting the notes with the new template" /></p>}
      {busyKey === 'lang' && <p className="mt-note"><Spinner label={`Translating into ${lang}`} /></p>}
      <section>
        <h2>Overview</h2>
        <p className="mt-lead">{summary.overview || 'No overview was written for this meeting.'}</p>
      </section>
      {summary.sections.map((s) => (
        <section key={s.title}>
          <h2>{s.title}</h2>
          {s.bullets.length ? (
            <ul className="bullets">
              {s.bullets.map((b, i) => (
                <li key={i}>
                  {b.text}{' '}
                  {b.t != null && <button className="link clock" onClick={() => seek(b.t)} aria-label={`Play from ${fmtClock(b.t)}`}>{fmtClock(b.t)}</button>}
                </li>
              ))}
            </ul>
          ) : <p className="muted small">Not discussed.</p>}
        </section>
      ))}
    </div>
  )
}

/* ---------- action items ---------- */
function ActionsTab({ m, save, seek }) {
  const { members } = useApp()
  const [text, setText] = useState('')
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState('')
  const memberName = (id) => members.find((x) => x.id === id)?.name
  const toggle = (a) => save({ actions: [{ id: a.id, done: !a.done }] })
  const add = async (e) => {
    e.preventDefault()
    if (!text.trim()) return
    const r = await save({ actions: [{ new: true, text: text.trim() }] }, 'Action item added')
    if (r) setText('')
  }
  const commitEdit = async (a) => {
    setEditing(null)
    if (draft.trim() && draft.trim() !== a.text) await save({ actions: [{ id: a.id, text: draft.trim() }] })
  }
  return (
    <div className="mt-stack">
      {!m.actions.length && <p className="muted">No action items were found in this meeting.</p>}
      <ul className="mt-actions">
        {m.actions.map((a) => (
          <li key={a.id} className={a.done ? 'done' : ''}>
            <label className="mt-check">
              <input type="checkbox" checked={!!a.done} onChange={() => toggle(a)} aria-label={a.done ? `Mark “${a.text}” not done` : `Mark “${a.text}” done`} />
              <span className="mt-box" aria-hidden="true" />
            </label>
            <div className="mt-action-body">
              {editing === a.id ? (
                <form onSubmit={(e) => { e.preventDefault(); commitEdit(a) }}>
                  <label className="sr" htmlFor={`act-${a.id}`}>Action item text</label>
                  <input id={`act-${a.id}`} autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={() => commitEdit(a)}
                    onKeyDown={(e) => e.key === 'Escape' && setEditing(null)} />
                </form>
              ) : (
                <span className="mt-atext">
                  {a.text}
                  {m.can_edit && (
                    <button className="link small mt-edit" onClick={() => { setEditing(a.id); setDraft(a.text) }} aria-label={`Edit “${a.text}”`}>Edit</button>
                  )}
                </span>
              )}
              <span className="mt-ameta">
                {m.can_edit ? (
                  <label>
                    <span className="sr">Assignee</span>
                    <select value={a.assignee || ''} onChange={(e) => save({ actions: [{ id: a.id, assignee: e.target.value || null }] }, 'Assignee updated')}>
                      <option value="">{a.speaker ? `Unassigned (said by ${nameOf(m, a.speaker)})` : 'Unassigned'}</option>
                      {members.filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                    </select>
                  </label>
                ) : (
                  <span>{a.assignee ? memberName(a.assignee) || 'Former member' : a.speaker ? nameOf(m, a.speaker) : 'Unassigned'}</span>
                )}
                {a.t != null && <button className="link clock" onClick={() => seek(a.t)} aria-label={`Play from ${fmtClock(a.t)}`}>{fmtClock(a.t)}</button>}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {m.can_edit && (
        <form className="mt-add-row" onSubmit={add}>
          <label className="sr" htmlFor="mt-new-action">New action item</label>
          <input id="mt-new-action" value={text} onChange={(e) => setText(e.target.value)} placeholder="Add an action item" />
          <button className="secondary" disabled={!text.trim()}>Add</button>
        </form>
      )}
    </div>
  )
}

/* ---------- ask ---------- */
function AskTab({ m, seek, thread, setThread }) {
  const [q, setQ] = useState('')
  const [pending, setPending] = useState(null)
  const end = useRef()
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [thread, pending])
  const ask = async (text) => {
    const question = text.trim()
    if (!question || pending) return
    setQ('')
    setPending(question)
    try {
      const { answer } = await post(`/meetings/${m.id}/ask`, { question })
      setThread((t) => [...t, { question, answer }])
    } catch (e) {
      setThread((t) => [...t, { question, answer: e.message, error: true }])
    } finally {
      setPending(null)
    }
  }
  return (
    <div className="mt-ask">
      {!thread.length && !pending && (
        <div className="mt-suggest">
          <p className="muted">Ask anything about this meeting. Answers link to the moment in the recording.</p>
          {['What did we decide?', 'What are the open questions?', 'Summarize what each speaker wanted', 'Draft a follow-up email from me'].map((s) => (
            <button key={s} className="secondary small-btn" onClick={() => ask(s)}>{s}</button>
          ))}
        </div>
      )}
      <div className="mt-thread" aria-live="polite">
        {thread.map((x, i) => (
          <div key={i} className="mt-qa">
            <p className="mt-q">{x.question}</p>
            <div className={`mt-a${x.error ? ' err' : ''}`}>{x.error ? x.answer : <Cited text={x.answer} seek={seek} />}</div>
          </div>
        ))}
        {pending && (
          <div className="mt-qa">
            <p className="mt-q">{pending}</p>
            <div className="mt-a"><Spinner label="Reading the transcript" /></div>
          </div>
        )}
        <span ref={end} />
      </div>
      <form className="mt-add-row mt-ask-form" onSubmit={(e) => { e.preventDefault(); ask(q) }}>
        <label className="sr" htmlFor="mt-ask">Ask about this meeting</label>
        <input id="mt-ask" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about this meeting" />
        <button className="primary" disabled={!!pending || !q.trim()}>Ask</button>
      </form>
    </div>
  )
}

/* ---------- clips ---------- */
function ClipsTab({ m, hl, player, editId, setEditId }) {
  const { me, toast } = useApp()
  const [busyKey, run] = useAction(toast)
  const [playlistFor, setPlaylistFor] = useState(null)
  const list = hl.data || []
  const shareLink = async (h, access) => {
    const r = await run(`link-${h.id}`, () => post(`/meetings/${m.id}/links`, { access, highlight_id: h.id }))
    if (r) toast((await copyText(absLink(`#/s/${r.token}`))) ? `Link copied. ${access === 'public' ? 'Anyone with it can play this clip.' : 'People in your organization can play this clip.'}` : 'Copy failed.')
  }
  const remove = async (h) => {
    if (!window.confirm(`Delete the clip “${h.title}”? It is also removed from playlists and share links.`)) return
    const r = await run(`del-${h.id}`, () => del(`/highlights/${h.id}`), 'Clip deleted')
    if (r) hl.setData((xs) => xs.filter((x) => x.id !== h.id))
  }
  if (hl.loading && !hl.data) return <Loading />
  return (
    <div className="mt-stack">
      <ErrorNote error={hl.error} retry={hl.reload} />
      {!list.length && (
        <p className="muted">No clips yet. Press Highlight under the player, or choose “Clip from here” on a transcript line.</p>
      )}
      <ul className="mt-clips">
        {list.map((h) => (
          <li key={h.id}>
            {editId === h.id ? (
              <ClipEditor h={h} onDone={(nh) => { if (nh) hl.setData((xs) => xs.map((x) => (x.id === nh.id ? nh : x))); setEditId(null) }} />
            ) : (
              <>
                <div className="mt-clip-head">
                  <button className="mt-clip-play" onClick={() => player.seek(h.start, { until: h.end })} aria-label={`Play clip ${h.title}`}>
                    <Svg d={PLAY} size={14} />
                  </button>
                  <div className="mt-clip-body">
                    <b>{h.title}</b>
                    <span className="muted small">
                      {fmtClock(h.start)} to {fmtClock(h.end)} · {h.user_name} · {fmtAgo(h.created)}
                    </span>
                    {h.note && <p className="mt-clip-note">{h.note}</p>}
                  </div>
                  <Menu label={`Clip actions for ${h.title}`} items={[
                    { label: 'Copy link for your organization', onClick: () => shareLink(h, 'org') },
                    { label: 'Copy public link', onClick: () => shareLink(h, 'public') },
                    { label: 'Add to playlist', onClick: () => setPlaylistFor(h) },
                    ...((h.user_id === me.id || m.can_edit) ? [
                      { label: 'Edit title and note', onClick: () => setEditId(h.id) },
                      null,
                      { label: 'Delete clip', danger: true, onClick: () => remove(h), disabled: busyKey === `del-${h.id}` },
                    ] : []),
                  ]} />
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
      {playlistFor && <PlaylistModal h={playlistFor} onClose={() => setPlaylistFor(null)} />}
    </div>
  )
}

function ClipEditor({ h, onDone }) {
  const { toast } = useApp()
  const [f, setF] = useState({ title: h.title, note: h.note || '' })
  const [busyKey, run] = useAction(toast)
  const submit = async (e) => {
    e.preventDefault()
    const r = await run('save', () => patch(`/highlights/${h.id}`, { start: h.start, end: h.end, title: f.title, note: f.note }), 'Clip saved')
    if (r) onDone(r)
  }
  return (
    <form className="stack-form mt-clip-edit" onSubmit={submit}>
      <Field label="Clip title"><input autoFocus value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <Field label="Note" hint="Shown to anyone you share the clip with."><textarea value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      <div className="row-gap">
        <button className="primary small-btn" disabled={busyKey === 'save'}>Save clip</button>
        <button type="button" className="secondary small-btn" onClick={() => onDone(null)}>Cancel</button>
      </div>
    </form>
  )
}

function PlaylistModal({ h, onClose }) {
  const { toast } = useApp()
  const lists = useApi('/playlists')
  const [name, setName] = useState('')
  const [busyKey, run] = useAction(toast)
  const add = async (pid, pname) => {
    const r = await run(pid, () => post(`/playlists/${pid}/items`, { highlight_id: h.id }), `Added to ${pname}`)
    if (r) onClose()
  }
  const create = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
    const r = await run('new', () => post('/playlists', { name: name.trim(), shared: false }))
    if (r) await add(r.id, name.trim())
  }
  return (
    <Modal title="Add clip to a playlist" onClose={onClose}>
      <p className="muted small mt-modal-sub">{h.title}</p>
      {lists.loading && <Loading />}
      <ErrorNote error={lists.error} retry={lists.reload} />
      <ul className="mt-pick">
        {lists.data?.map((p) => (
          <li key={p.id}>
            <span><b>{p.name}</b> <span className="muted small">{p.count} {p.count === 1 ? 'clip' : 'clips'}{p.shared ? ' · shared' : ''}</span></span>
            <button className="secondary small-btn" onClick={() => add(p.id, p.name)} disabled={!!busyKey}>Add</button>
          </li>
        ))}
      </ul>
      {lists.data && !lists.data.length && <p className="muted">You don't have any playlists yet.</p>}
      <form className="mt-add-row" onSubmit={create}>
        <label className="sr" htmlFor="mt-new-pl">New playlist name</label>
        <input id="mt-new-pl" value={name} onChange={(e) => setName(e.target.value)} placeholder="New playlist name" />
        <button className="primary" disabled={!name.trim() || !!busyKey}>Create and add</button>
      </form>
    </Modal>
  )
}

/* ---------- comments ---------- */
function CommentsTab({ m, cm, player, presetAt, clearPreset }) {
  const { me, members, toast } = useApp()
  const [body, setBody] = useState('')
  const [atTime, setAtTime] = useState(presetAt != null)
  const [t, setT] = useState(presetAt)
  const [mentions, setMentions] = useState([])
  const [suggest, setSuggest] = useState(null) // {query, start}
  const [busyKey, run] = useAction(toast)
  const ta = useRef()

  useEffect(() => {
    if (presetAt != null) { setAtTime(true); setT(presetAt); ta.current?.focus() }
  }, [presetAt])

  const onChange = (e) => {
    const v = e.target.value
    setBody(v)
    const caret = e.target.selectionStart
    const before = v.slice(0, caret)
    const hit = before.match(/(^|\s)@([\w.-]{0,30})$/)
    setSuggest(hit ? { query: hit[2].toLowerCase(), start: caret - hit[2].length - 1 } : null)
  }
  const options = suggest
    ? members.filter((x) => x.active && x.id !== me.id && x.name.toLowerCase().includes(suggest.query)).slice(0, 6)
    : []
  const pick = (x) => {
    const caret = ta.current.selectionStart
    const next = `${body.slice(0, suggest.start)}@${x.name} ${body.slice(caret)}`
    setBody(next)
    setMentions((ms) => [...new Set([...ms, x.id])])
    setSuggest(null)
    ta.current.focus()
  }
  const submit = async (e) => {
    e.preventDefault()
    if (!body.trim()) return
    const ids = mentions.filter((id) => body.includes(`@${members.find((x) => x.id === id)?.name}`))
    const when = atTime ? (t != null ? t : player.now) : null
    const r = await run('post', () => post(`/meetings/${m.id}/comments`, { body: body.trim(), t: when, mentions: ids }), ids.length ? 'Comment posted and people notified' : 'Comment posted')
    if (r) {
      cm.setData((xs) => [...(xs || []), r])
      setBody(''); setMentions([]); setT(null); setAtTime(false); clearPreset()
    }
  }
  const remove = async (c) => {
    if (!window.confirm('Delete this comment?')) return
    const r = await run(`del-${c.id}`, () => del(`/comments/${c.id}`), 'Comment deleted')
    if (r) cm.setData((xs) => xs.filter((x) => x.id !== c.id))
  }
  const renderBody = (text) => text.split(/(@[A-Z][\w.-]*(?: [A-Z][\w.-]*)?)/g).map((p, i) => (p.startsWith('@') ? <b key={i} className="mt-mention">{p}</b> : p))

  return (
    <div className="mt-stack">
      <ErrorNote error={cm.error} retry={cm.reload} />
      {cm.data && !cm.data.length && <p className="muted">No comments yet. Leave one for your team, or mention someone with @ to notify them.</p>}
      <ul className="mt-comments">
        {cm.data?.map((c) => (
          <li key={c.id}>
            <Avatar name={c.user_name} id={c.user_id} size={28} />
            <div className="mt-comment-body">
              <span className="mt-comment-meta">
                <b>{c.user_name}</b>
                <span className="muted small">{fmtAgo(c.created)}</span>
                {c.t != null && <button className="link clock small" onClick={() => player.seek(c.t)}>{fmtClock(c.t)}</button>}
              </span>
              <p>{renderBody(c.body)}</p>
            </div>
            {(c.user_id === me.id || m.can_edit) && (
              <button className="icon-btn" onClick={() => remove(c)} aria-label="Delete comment" disabled={busyKey === `del-${c.id}`}>
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" /></svg>
              </button>
            )}
          </li>
        ))}
      </ul>
      <form className="mt-composer" onSubmit={submit}>
        <label className="sr" htmlFor="mt-comment">Write a comment</label>
        <div className="menu-wrap mt-composer-box">
          <textarea id="mt-comment" ref={ta} value={body} onChange={onChange} placeholder="Write a comment. Type @ to mention a teammate."
            onKeyDown={(e) => {
              if (suggest && options.length && (e.key === 'Enter' || e.key === 'Tab')) { e.preventDefault(); pick(options[0]) }
              if (e.key === 'Escape') setSuggest(null)
            }} />
          {options.length > 0 && (
            <div className="menu left mt-mentions" role="listbox" aria-label="Mention a teammate">
              {options.map((x) => (
                <button type="button" key={x.id} role="option" onMouseDown={(e) => { e.preventDefault(); pick(x) }}>
                  {x.name} <span className="muted small">{x.email}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="mt-composer-foot">
          <label className="mt-inline-check">
            <input type="checkbox" checked={atTime} onChange={(e) => { setAtTime(e.target.checked); if (!e.target.checked) setT(null) }} />
            At {fmtClock(t != null ? t : player.now)}
          </label>
          <button className="primary small-btn" disabled={!body.trim() || busyKey === 'post'}>Post comment</button>
        </div>
      </form>
    </div>
  )
}

/* ---------- insights ---------- */
function InsightsTab({ m, seek, reload }) {
  const { toast } = useApp()
  const [busyKey, run] = useAction(toast)
  const per = m.metrics?.per_speaker || {}
  const byTracker = useMemo(() => {
    const g = {}
    for (const h of m.tracker_hits || []) (g[h.name] = g[h.name] || []).push(h)
    return Object.entries(g)
  }, [m.tracker_hits])
  const rescoring = m.pending?.includes('rescore')
  const rescore = async () => {
    const r = await run('rescore', () => post(`/meetings/${m.id}/rescore`), 'Scoring again. This takes about half a minute.')
    if (r) reload()
  }
  return (
    <div className="mt-stack">
      <section>
        <h2>Talk time</h2>
        {Object.keys(per).length ? (
          <div className="mt-table-wrap">
            <table className="mt-table">
              <thead>
                <tr><th scope="col">Speaker</th><th scope="col">Talk</th><th scope="col">Words per min</th><th scope="col">Questions</th><th scope="col">Longest stretch</th></tr>
              </thead>
              <tbody>
                {m.speakers.filter((s) => per[s]).map((s) => (
                  <tr key={s}>
                    <th scope="row"><i className="mt-dot" style={{ background: speakerHue(m.speakers, s) }} />{nameOf(m, s)}</th>
                    <td>{Math.round(per[s].talk_pct)}% <span className="muted small">({fmtClock(per[s].talk)})</span></td>
                    <td>{per[s].wpm || '–'}</td>
                    <td>{per[s].questions}</td>
                    <td>{fmtClock(per[s].longest)}{per[s].monologues ? <span className="muted small"> · {per[s].monologues} over 90s</span> : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="muted">No talk-time data for this meeting.</p>}
      </section>

      <section>
        <h2>Trackers</h2>
        {byTracker.length ? byTracker.map(([name, hits]) => (
          <div key={name} className="mt-tracker">
            <p className="mt-tracker-name">{name} <span className="muted small">{hits.length} {hits.length === 1 ? 'moment' : 'moments'}</span></p>
            <ul className="mt-hits">
              {hits.map((h, i) => (
                <li key={i}>
                  <button className="link clock" onClick={() => seek(h.t)}>{fmtClock(h.t)}</button>
                  <span>“{h.quote}”</span>
                </li>
              ))}
            </ul>
          </div>
        )) : <p className="muted">None of your organization's trackers came up in this meeting. <a href="#/trackers">Manage trackers</a></p>}
      </section>

      <section>
        <div className="mt-section-head">
          <h2>Scorecards</h2>
          {m.can_edit && m.meeting_type_id && (
            <button className="secondary small-btn" onClick={rescore} disabled={rescoring || busyKey === 'rescore'}>
              {rescoring ? 'Scoring' : 'Score again'}
            </button>
          )}
        </div>
        {rescoring && <p className="mt-note"><Spinner label="Scoring this meeting" /></p>}
        {!m.meeting_type_id && <p className="muted">Set a meeting type above to score this meeting against that type's scorecards.</p>}
        {m.meeting_type_id && !m.scorecards?.length && !rescoring && (
          <p className="muted">No scorecard applies to the {m.meeting_type?.name || 'selected'} meeting type yet. Admins can add one in organization settings.</p>
        )}
        {m.scorecards?.map((sc) => (
          <div key={sc.scorecard_id} className="mt-scorecard">
            <div className="mt-score-head">
              <b>{sc.name}</b>
              <span className={`mt-score ${sc.score >= 75 ? 'good' : sc.score >= 50 ? 'ok' : 'low'}`}>{Math.round(sc.score)}<span>/100</span></span>
            </div>
            {sc.details?.feedback && <p className="mt-feedback">{sc.details.feedback}</p>}
            <ul className="mt-criteria">
              {sc.details?.criteria?.map((c) => (
                <li key={c.name}>
                  <span className="mt-crit-score">{c.score}<span>/10</span></span>
                  <div>
                    <b>{c.name}</b>
                    <p className="muted small">
                      {c.reason}{' '}
                      {c.t != null && <button className="link clock" onClick={() => seek(c.t)}>{fmtClock(c.t)}</button>}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
    </div>
  )
}

/* ---------- modals ---------- */
function ShareModal({ m, setM, onClose }) {
  const { members, teams, isAdmin, toast } = useApp()
  const allTeams = useApi(isAdmin ? '/org/teams' : null)
  const teamOptions = isAdmin ? (allTeams.data || []) : teams
  const links = useApi(`/meetings/${m.id}/links`)
  const [vis, setVis] = useState(m.visibility)
  const [teamId, setTeamId] = useState(m.team_id || teamOptions[0]?.id || '')
  const [person, setPerson] = useState('')
  const [busyKey, run] = useAction(toast)
  useEffect(() => { if (!teamId && teamOptions[0]) setTeamId(teamOptions[0].id) }, [teamOptions, teamId])

  const saveVis = async () => {
    const r = await run('vis', () => patch(`/meetings/${m.id}`, { visibility: vis, team_id: vis === 'team' ? teamId : null }), 'Access updated')
    if (r) setM(r)
  }
  const addPerson = async () => {
    if (!person) return
    const r = await run('add', () => post(`/meetings/${m.id}/shares`, { user_ids: [person] }), 'Shared')
    if (r) { setM((d) => ({ ...d, shares: r })); setPerson('') }
  }
  const removePerson = async (uid) => {
    const r = await run(`rm-${uid}`, () => del(`/meetings/${m.id}/shares/${uid}`))
    if (r) setM((d) => ({ ...d, shares: d.shares.filter((s) => s.id !== uid) }))
  }
  const makeLink = async (access) => {
    const r = await run(`link-${access}`, () => post(`/meetings/${m.id}/links`, { access }))
    if (r) {
      links.reload()
      toast((await copyText(absLink(`#/s/${r.token}`))) ? 'Link created and copied' : 'Link created')
    }
  }
  const revoke = async (tok) => {
    const r = await run(`rv-${tok}`, () => del(`/links/${tok}`), 'Link turned off')
    if (r) links.setData((xs) => xs.filter((x) => x.token !== tok))
  }
  const shared = new Set((m.shares || []).map((s) => s.id))
  const candidates = members.filter((x) => x.active && x.id !== m.owner_id && !shared.has(x.id))
  const meetingLinks = (links.data || []).filter((l) => !l.highlight_id)
  const visChanged = vis !== m.visibility || (vis === 'team' && teamId !== m.team_id)

  return (
    <Modal title="Share this meeting" onClose={onClose} wide>
      <div className="mt-share">
        <section>
          <h2>Who can see it</h2>
          {m.can_edit ? (
            <div className="mt-share-vis">
              <select value={vis} onChange={(e) => setVis(e.target.value)} aria-label="Who can see it">
                <option value="private">Only the owner and people added below</option>
                <option value="team" disabled={!teamOptions.length}>A team{!teamOptions.length ? ' (join a team first)' : ''}</option>
                <option value="org">Everyone in the organization</option>
              </select>
              {vis === 'team' && (
                <select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Team">
                  {teamOptions.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              )}
              <button className="secondary" onClick={saveVis} disabled={!visChanged || busyKey === 'vis' || (vis === 'team' && !teamId)}>Save access</button>
            </div>
          ) : (
            <p className="muted">{m.visibility === 'team' && m.team ? `Team: ${m.team.name}` : VIS_LABEL[m.visibility]}. Only the owner or an admin can change this.</p>
          )}
        </section>

        <section>
          <h2>People</h2>
          <ul className="mt-pick">
            <li><span><b>{m.owner?.name}</b> <span className="muted small">Owner</span></span></li>
            {(m.shares || []).map((s) => (
              <li key={s.id}>
                <span><b>{s.name}</b> <span className="muted small">{s.email}</span></span>
                {m.can_edit && <button className="link danger" onClick={() => removePerson(s.id)} disabled={busyKey === `rm-${s.id}`}>Remove</button>}
              </li>
            ))}
          </ul>
          {m.can_edit && (
            <div className="mt-add-row">
              <label className="sr" htmlFor="mt-share-person">Add a teammate</label>
              <select id="mt-share-person" value={person} onChange={(e) => setPerson(e.target.value)}>
                <option value="">Add a teammate</option>
                {candidates.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.email})</option>)}
              </select>
              <button className="primary" onClick={addPerson} disabled={!person || busyKey === 'add'}>Share</button>
            </div>
          )}
        </section>

        <section>
          <h2>Links</h2>
          {m.can_edit ? (
            <>
              <p className="muted small">Anyone with a public link can watch the recording and read the transcript and notes without logging in.</p>
              <div className="row-gap">
                <button className="secondary" onClick={() => makeLink('org')} disabled={!!busyKey}>Create link for your organization</button>
                <button className="secondary" onClick={() => makeLink('public')} disabled={!!busyKey}>Create public link</button>
              </div>
            </>
          ) : (
            <p className="muted">Only the owner or an admin can share the whole recording by link. You can share a clip from the Clips tab instead.</p>
          )}
          {meetingLinks.length > 0 && (
            <ul className="mt-pick">
              {meetingLinks.map((l) => (
                <li key={l.token}>
                  <span><b>{l.access === 'public' ? 'Public link' : 'Organization link'}</b> <span className="muted small">created {fmtAgo(l.created)}</span></span>
                  <span className="row-gap">
                    <button className="link" onClick={async () => toast((await copyText(absLink(`#/s/${l.token}`))) ? 'Link copied' : 'Copy failed')}>Copy</button>
                    <button className="link danger" onClick={() => revoke(l.token)} disabled={busyKey === `rv-${l.token}`}>Turn off</button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Modal>
  )
}

function FolderModal({ m, setM, onClose }) {
  const { toast } = useApp()
  const folders = useApi('/folders')
  const [name, setName] = useState('')
  const [busyKey, run] = useAction(toast)
  const add = async (fid, fname) => {
    const r = await run(fid, () => post(`/folders/${fid}/items`, { meeting_id: m.id }), `Added to ${fname}`)
    if (r) { setM((d) => ({ ...d, folders: [...new Set([...(d.folders || []), fid])] })); onClose() }
  }
  const create = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
    const r = await run('new', () => post('/folders', { name: name.trim(), shared: false }))
    if (r) await add(r.id, name.trim())
  }
  return (
    <Modal title="Add to folder" onClose={onClose}>
      {folders.loading && <Loading />}
      <ErrorNote error={folders.error} retry={folders.reload} />
      <ul className="mt-pick">
        {folders.data?.map((f) => {
          const inIt = m.folders?.includes(f.id)
          return (
            <li key={f.id}>
              <span><b>{f.name}</b> <span className="muted small">{f.count} {f.count === 1 ? 'meeting' : 'meetings'}{f.shared ? ' · shared with the organization' : ''}</span></span>
              {inIt ? <span className="muted small">Already in this folder</span>
                : <button className="secondary small-btn" onClick={() => add(f.id, f.name)} disabled={!!busyKey}>Add</button>}
            </li>
          )
        })}
      </ul>
      {folders.data && !folders.data.length && <p className="muted">You don't have any folders yet.</p>}
      <p className="muted small">Adding a meeting to a shared folder lets everyone in the organization see it.</p>
      <form className="mt-add-row" onSubmit={create}>
        <label className="sr" htmlFor="mt-new-folder">New folder name</label>
        <input id="mt-new-folder" value={name} onChange={(e) => setName(e.target.value)} placeholder="New private folder" />
        <button className="primary" disabled={!name.trim() || !!busyKey}>Create and add</button>
      </form>
    </Modal>
  )
}

function FollowupModal({ m, onClose }) {
  const { toast } = useApp()
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(async () => {
    setError(null); setDraft(null)
    try { setDraft(await post(`/meetings/${m.id}/followup`)) } catch (e) { setError(e) }
  }, [m.id])
  useEffect(() => { load() }, [load])
  const copy = async () => toast((await copyText(`Subject: ${draft.subject}\n\n${draft.body}`)) ? 'Email copied' : 'Copy failed. Select the text and copy it instead.')
  const mailto = draft && `mailto:${encodeURIComponent((m.attendees || []).join(','))}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`
  return (
    <Modal title="Follow-up email" onClose={onClose} wide footer={draft && (
      <>
        <button className="secondary" onClick={load}>Write another draft</button>
        <a className="secondary as-btn" href={mailto}>Open in email app</a>
        <button className="primary" onClick={copy}>Copy email</button>
      </>
    )}>
      {!draft && !error && <Loading label="Drafting from the meeting notes" />}
      <ErrorNote error={error} retry={load} />
      {draft && (
        <div className="stack-form">
          <Field label="Subject"><input value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} /></Field>
          <Field label="Message"><textarea className="mt-email" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></Field>
        </div>
      )}
    </Modal>
  )
}
