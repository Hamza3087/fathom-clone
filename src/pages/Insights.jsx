// Trackers, coaching and account-wide Ask.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { del, post, patch, qs, useApi } from '../api.js'
import {
  Avatar, Empty, ErrorNote, Field, Loading, Menu, Modal, PageHead, Spinner, Tabs,
  fmtClock, fmtDay, parseHash, useAction, useApp,
} from '../ui.jsx'
import './insights.css'

function Seg({ value, options, onChange, label }) {
  return (
    <span className="in-seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>{text}</button>
      ))}
    </span>
  )
}

/* ---------- trackers ---------- */
function TrackerModal({ tracker, onClose, onSaved }) {
  const { toast } = useApp()
  const [f, setF] = useState({
    name: tracker?.name || '',
    kind: tracker?.kind || 'keyword',
    keywords: (tracker?.terms?.keywords || []).join(', '),
    description: tracker?.terms?.description || '',
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    const body = { name: f.name, kind: f.kind, keywords: f.keywords.split(',').map((k) => k.trim()).filter(Boolean), description: f.description }
    try {
      if (tracker) await patch(`/trackers/${tracker.id}`, body)
      else await post('/trackers', body)
      toast(tracker ? 'Tracker saved. Rescanning past meetings.' : 'Tracker created. Scanning past meetings.')
      onSaved()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={tracker ? 'Edit tracker' : 'New tracker'} onClose={onClose} footer={
      <>
        <button className="secondary" type="button" onClick={onClose}>Cancel</button>
        <button className="primary" form="tracker-form" disabled={busy}>{busy ? 'Saving' : tracker ? 'Save tracker' : 'Create tracker'}</button>
      </>
    }>
      <form id="tracker-form" className="stack-form" onSubmit={submit}>
        <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Competitor mentions" required /></Field>
        <div className="in-radio" role="radiogroup" aria-label="How to find moments">
          <label>
            <input type="radio" name="kind" checked={f.kind === 'keyword'} onChange={() => setF({ ...f, kind: 'keyword' })} />
            <span><b>Keywords</b><br /><span className="muted small">Finds the exact words you list.</span></span>
          </label>
          <label>
            <input type="radio" name="kind" checked={f.kind === 'ai'} onChange={() => setF({ ...f, kind: 'ai' })} />
            <span><b>AI</b><br /><span className="muted small">Finds moments by meaning, even when people use different words.</span></span>
          </label>
        </div>
        {f.kind === 'keyword' ? (
          <Field label="Keywords" hint="Separate with commas. Up to 25.">
            <input value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} placeholder="Gong, Otter, Fireflies" />
          </Field>
        ) : (
          <Field label="Moments to find" hint="Describe it in a sentence or two, the way you'd brief a colleague.">
            <textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })}
              placeholder="The customer says the product is too expensive or asks for a discount." />
          </Field>
        )}
        <p className="muted small">Everyone subscribed gets a notification when this comes up in a new meeting. You're subscribed automatically.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

export function TrackersPage() {
  const { toast } = useApp()
  const list = useApi('/trackers', { poll: (d) => (d?.some((t) => t.pending) ? 4000 : 0) })
  const [editing, setEditing] = useState(null) // null | 'new' | tracker
  const [, run] = useAction(toast)
  const toggleSub = (t) => run(t.id, async () => {
    await (t.subscribed ? del(`/trackers/${t.id}/subscribe`) : post(`/trackers/${t.id}/subscribe`))
    list.setData((d) => d.map((x) => (x.id === t.id ? { ...x, subscribed: !t.subscribed } : x)))
  }, t.subscribed ? 'Unsubscribed' : 'Subscribed. You will be notified when it comes up.')
  const remove = (t) => {
    if (!confirm(`Delete the tracker “${t.name}”? Its matches are removed too.`)) return
    run(t.id, async () => { await del(`/trackers/${t.id}`); list.reload() }, 'Tracker deleted')
  }
  return (
    <main className="page">
      <PageHead title="Trackers" sub="Follow topics across every meeting you can see, like competitors, pricing or churn risk."
        actions={<button className="primary" onClick={() => setEditing('new')}>New tracker</button>} />
      <ErrorNote error={list.error} retry={list.reload} />
      {list.loading && !list.data && <Loading />}
      {list.data && !list.data.length && (
        <Empty title="No trackers yet" action={<button className="primary" onClick={() => setEditing('new')}>Create your first tracker</button>}>
          A tracker finds moments across all your team's calls, by exact keywords or by meaning.
        </Empty>
      )}
      <div className="in-list">
        {list.data?.map((t) => (
          <div key={t.id} className="in-item">
            <div className="in-item-main">
              <h3><a href={`#/trackers/${t.id}`}>{t.name}</a> <span className="tag">{t.kind === 'ai' ? 'AI' : 'Keywords'}</span></h3>
              {t.kind === 'keyword'
                ? <div className="in-kw">{t.terms.keywords.map((k) => <span key={k} className="tag quiet">{k}</span>)}</div>
                : <p className="in-item-meta">{t.terms.description}</p>}
              <p className="in-item-meta">
                {t.pending ? <Spinner label="Scanning past meetings" /> : (
                  <><span className="in-count">{t.hits_30d}</span> {t.hits_30d === 1 ? 'mention' : 'mentions'} in <span className="in-count">{t.meetings_30d}</span> {t.meetings_30d === 1 ? 'meeting' : 'meetings'} in the last 30 days · made by {t.owner_name}</>
                )}
              </p>
            </div>
            <div className="in-item-side">
              <button className={t.subscribed ? 'secondary small-btn' : 'primary small-btn'} onClick={() => toggleSub(t)} aria-pressed={t.subscribed}>
                {t.subscribed ? 'Subscribed' : 'Subscribe'}
              </button>
              {t.can_edit && (
                <Menu label={`More actions for ${t.name}`} items={[
                  { label: 'Edit', onClick: () => setEditing(t) },
                  { label: 'Delete', danger: true, onClick: () => remove(t) },
                ]} />
              )}
            </div>
          </div>
        ))}
      </div>
      {editing && <TrackerModal tracker={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={list.reload} />}
    </main>
  )
}

function weekStart(iso) {
  const d = new Date(iso)
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  return d.getTime()
}

function WeeklyChart({ hits, days }) {
  const weeks = Math.max(2, Math.ceil(days / 7))
  const now = weekStart(new Date().toISOString())
  const buckets = Array.from({ length: weeks }, (_, i) => ({ start: now - (weeks - 1 - i) * 7 * 86400000, n: 0 }))
  hits.forEach((h) => {
    const w = weekStart(h.date)
    const b = buckets.find((x) => x.start === w)
    if (b) b.n += 1
  })
  const max = Math.max(1, ...buckets.map((b) => b.n))
  return (
    <div className="in-chart" role="img" aria-label={`Mentions per week: ${buckets.map((b) => `week of ${fmtDay(new Date(b.start).toISOString())}, ${b.n}`).join('; ')}`}>
      {buckets.map((b) => (
        <span key={b.start} className={b.n ? 'in-bar' : 'in-bar zero'} style={{ height: `${(b.n / max) * 100}%` }}
          title={`Week of ${fmtDay(new Date(b.start).toISOString())}: ${b.n}`} />
      ))}
    </div>
  )
}

export function TrackerPage({ id }) {
  const [days, setDays] = useState(90)
  const trackers = useApi('/trackers', { poll: (d) => (d?.find((t) => t.id === id)?.pending ? 4000 : 0) })
  const hits = useApi(`/trackers/${id}/hits${qs({ days })}`)
  const tracker = trackers.data?.find((t) => t.id === id)
  const pendingRef = useRef(tracker?.pending)
  useEffect(() => {
    // When the backfill finishes, load the new matches.
    if (pendingRef.current && tracker && !tracker.pending) hits.reload()
    pendingRef.current = tracker?.pending
  }, [tracker?.pending]) // eslint-disable-line react-hooks/exhaustive-deps
  const groups = useMemo(() => {
    const g = new Map()
    for (const h of hits.data || []) {
      if (!g.has(h.meeting_id)) g.set(h.meeting_id, { id: h.meeting_id, title: h.title, date: h.date, hits: [] })
      g.get(h.meeting_id).hits.push(h)
    }
    return [...g.values()]
  }, [hits.data])
  if (trackers.loading && !trackers.data) return <main className="page"><Loading /></main>
  if (trackers.data && !tracker) {
    return <main className="page"><Empty title="That tracker doesn't exist" action={<a href="#/trackers">Back to trackers</a>}>It may have been deleted.</Empty></main>
  }
  return (
    <main className="page">
      <a className="back" href="#/trackers">Trackers</a>
      <PageHead title={tracker?.name || 'Tracker'} sub={tracker && (tracker.kind === 'ai' ? tracker.terms.description : `Keywords: ${tracker.terms.keywords.join(', ')}`)} />
      <div className="in-filters">
        <Seg label="Time range" value={days} onChange={setDays} options={[[30, '30 days'], [90, '90 days'], [365, '1 year']]} />
        {tracker?.pending && <Spinner label="Scanning past meetings" />}
      </div>
      <ErrorNote error={hits.error} retry={hits.reload} />
      {hits.loading && !hits.data && <Loading />}
      {hits.data && (
        <>
          <WeeklyChart hits={hits.data} days={days} />
          <p className="in-note">{hits.data.length} {hits.data.length === 1 ? 'mention' : 'mentions'} in {groups.length} {groups.length === 1 ? 'meeting' : 'meetings'}.</p>
          {!groups.length && <Empty title="No mentions in this period">Try a longer time range, or check back after new meetings are processed.</Empty>}
          {groups.map((g) => (
            <section key={g.id} className="in-hit-group">
              <h3><a href={`#/meeting/${g.id}`}>{g.title}</a></h3>
              <p className="in-item-meta">{fmtDay(g.date)}</p>
              {g.hits.map((h, i) => (
                <div key={i} className="in-hit">
                  <a className="clock" href={`#/meeting/${g.id}?t=${Math.floor(h.t)}`} aria-label={`Play from ${fmtClock(h.t)}`}>{fmtClock(h.t)}</a>
                  <q>{h.quote}</q>
                </div>
              ))}
            </section>
          ))}
        </>
      )}
    </main>
  )
}

/* ---------- coaching ---------- */
const val = (v, fmt = (x) => x) => (v === null || v === undefined ? <span className="in-dim">–</span> : fmt(v))

function TalkMetrics() {
  const { isAdmin, isLead } = useApp()
  const [days, setDays] = useState(30)
  const [team, setTeam] = useState('')
  const teams = useApi(isAdmin || isLead ? '/org/teams' : null)
  const data = useApi(`/coaching${qs({ days, team_id: team })}`)
  return (
    <>
      <div className="in-filters">
        <Seg label="Time range" value={days} onChange={setDays} options={[[7, '7 days'], [30, '30 days'], [90, '90 days']]} />
        {(isAdmin || isLead) && teams.data?.length > 0 && (
          <select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Team">
            <option value="">All people I coach</option>
            {teams.data.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        )}
      </div>
      <p className="in-note">
        Metrics count only meetings where a speaker was linked to that teammate on the meeting page.
        On sales discovery calls, the rep usually talks about 40 to 60 percent of the time.
        {!isAdmin && !isLead && ' You see your own numbers; team leads and admins see their teams.'}
      </p>
      <ErrorNote error={data.error} retry={data.reload} />
      {data.loading && !data.data && <Loading />}
      {data.data && (
        <div className="in-table-wrap">
          <table className="in-table">
            <thead>
              <tr>
                <th>Person</th><th className="num">Meetings</th><th className="num">Talk %</th><th className="num">Words per minute</th>
                <th className="num">Questions per call</th><th className="num">Longest monologue</th><th className="num">Monologues</th>
              </tr>
            </thead>
            <tbody>
              {data.data.people.map((p) => (
                <tr key={p.user.id}>
                  <td><span className="in-person"><Avatar name={p.user.name} id={p.user.id} size={24} />{p.user.name}</span></td>
                  <td className="num">{p.meetings}</td>
                  <td className="num">{val(p.talk_pct, (v) => `${Math.round(v)}%`)}</td>
                  <td className="num">{val(p.wpm, Math.round)}</td>
                  <td className="num">{val(p.questions_per_call)}</td>
                  <td className="num">{val(p.longest_monologue, fmtClock)}</td>
                  <td className="num">{p.meetings ? p.monologues : val(null)}</td>
                </tr>
              ))}
              {!data.data.people.length && <tr><td colSpan={7} className="in-dim">No one to show.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function Scorecards() {
  const { isAdmin, isLead } = useApp()
  const data = useApi('/coaching/scorecards')
  if (data.loading && !data.data) return <Loading />
  return (
    <>
      <ErrorNote error={data.error} retry={data.reload} />
      {!isAdmin && !isLead && <p className="in-note">You see scores for your own meetings. Team leads and admins see their teams.</p>}
      {data.data && !data.data.length && (
        <Empty title="No scorecards yet" action={isAdmin && <a className="secondary as-btn" href="#/admin/scorecards">Set up a scorecard</a>}>
          Scorecards grade calls of a meeting type against criteria you choose.{!isAdmin && ' Ask an admin to set one up.'}
        </Empty>
      )}
      {data.data?.map((sc) => (
        <section key={sc.id} className="in-card">
          <div className="in-card-head">
            <div>
              <h3>{sc.name}</h3>
              <p className="muted small">Scores {sc.meeting_type} calls · last 90 days</p>
            </div>
            <span className="in-score">{sc.average ?? '–'}<small> / 100 average</small></span>
          </div>
          {sc.criteria.map((c) => {
            const v = sc.criteria_avg[c.name]
            return (
              <div key={c.name} className="in-crit">
                <span title={c.description}>{c.name}</span>
                <span className="in-track" role="img" aria-label={`${c.name}: ${v ?? 'no score'} out of 10`}><i style={{ width: `${(v || 0) * 10}%` }} /></span>
                <span className="num">{v ?? '–'}</span>
              </div>
            )
          })}
          <div className="in-cols">
            <div>
              <h4>Top performers</h4>
              {sc.top.length ? <ul>{sc.top.map((r) => <li key={r.user.id}><span>{r.user.name}</span><span>{r.avg} · {r.calls} {r.calls === 1 ? 'call' : 'calls'}</span></li>)}</ul> : <p className="muted small">No scores yet.</p>}
            </div>
            <div>
              <h4>Everyone else</h4>
              {sc.lower.length ? <ul>{sc.lower.map((r) => <li key={r.user.id}><span>{r.user.name}</span><span>{r.avg} · {r.calls} {r.calls === 1 ? 'call' : 'calls'}</span></li>)}</ul> : <p className="muted small">No one else yet.</p>}
            </div>
          </div>
          {sc.recent.length > 0 && (
            <div className="in-cols">
              <div style={{ gridColumn: '1 / -1' }}>
                <h4>Recent scored meetings</h4>
                <ul>
                  {sc.recent.map((r) => (
                    <li key={r.meeting_id}>
                      <a href={`#/meeting/${r.meeting_id}?tab=insights`}>{r.title}</a>
                      <span className="in-dim">{r.user} · {fmtDay(r.date)} · <b>{Math.round(r.score)}</b></span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>
      ))}
    </>
  )
}

export function CoachingPage() {
  const [tab, setTab] = useState('metrics')
  return (
    <main className="page">
      <PageHead title="Coaching" sub="How people run their calls: talk time, questions, monologues and scorecard results." />
      <Tabs tabs={[['metrics', 'Talk metrics'], ['scorecards', 'Scorecards']]} value={tab} onChange={setTab} />
      {tab === 'metrics' ? <TalkMetrics /> : <Scorecards />}
    </main>
  )
}

/* ---------- account-wide Ask ---------- */
const CITE = /\[(M\d+)(?:\s+((?:\d+:)?\d+:\d{2}))?\]/g
const toSeconds = (s) => s.split(':').reduce((a, x) => a * 60 + Number(x), 0)

function CitedAnswer({ text, refs }) {
  const out = []
  let last = 0
  for (const m of text.matchAll(CITE)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const ref = refs[m[1]]
    if (ref) {
      const t = m[2] ? toSeconds(m[2]) : null
      out.push(
        <a key={m.index} className="in-cite" href={`#/meeting/${ref.id}${t !== null ? `?t=${t}` : ''}`}
          title={`${ref.title}${m[2] ? ` at ${m[2]}` : ''}`} aria-label={`${ref.title}${m[2] ? ` at ${m[2]}` : ''}`}>
          {m[2] || ref.title}
        </a>,
      )
    } else {
      out.push(m[0])
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const SUGGEST = ['What objections came up this month?', 'Summarize what customers asked for', 'What did we promise to send, and to whom?']

export function AskPage() {
  const [scope, setScope] = useState('all')
  const [q, setQ] = useState('')
  const [thread, setThread] = useState([])
  const [pending, setPending] = useState(null)
  const asked = useRef(false)
  const ask = async (text) => {
    const question = text.trim()
    if (!question || pending) return
    setQ('')
    setPending(question)
    try {
      const r = await post('/ask', { question, scope })
      setThread((t) => [...t, { question, ...r }])
    } catch (e) {
      setThread((t) => [...t, { question, error: e.message }])
    } finally {
      setPending(null)
    }
  }
  useEffect(() => {
    const pre = parseHash().query.q
    if (pre && !asked.current) {
      asked.current = true
      ask(pre)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const refsFor = (item) => Object.values(item.refs || {})
  return (
    <main className="page">
      <PageHead title="Ask Sounding" sub="Ask questions across your meetings. Answers link to the moments they come from." />
      <div className="in-ask">
        <div className="in-ask-bar">
          <label htmlFor="ask-scope" className="muted">Search in</label>
          <select id="ask-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="all">All calls I can see</option>
            <option value="mine">My calls</option>
            <option value="team">Teammates' calls</option>
          </select>
        </div>
        {!thread.length && !pending && (
          <div className="in-chips">{SUGGEST.map((s) => <button key={s} onClick={() => ask(s)}>{s}</button>)}</div>
        )}
        <div aria-live="polite" className="stack-form">
          {thread.map((item, i) => (
            <div key={i} className="in-qa">
              <p className="q">{item.question}</p>
              {item.error ? <div className="a err">{item.error}</div> : (
                <div className="a">
                  <CitedAnswer text={item.answer} refs={item.refs || {}} />
                  <p className="src">
                    Searched {item.searched} {item.searched === 1 ? 'meeting' : 'meetings'}
                    {refsFor(item).length > 0 && <> · Sources: {refsFor(item).map((r, j) => <React.Fragment key={r.id}>{j > 0 && ', '}<a href={`#/meeting/${r.id}`}>{r.title}</a></React.Fragment>)}</>}
                  </p>
                </div>
              )}
            </div>
          ))}
          {pending && (
            <div className="in-qa">
              <p className="q">{pending}</p>
              <div className="a"><Spinner label="Reading your meetings" /></div>
            </div>
          )}
        </div>
        <form className="in-ask-form" onSubmit={(e) => { e.preventDefault(); ask(q) }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about your meetings" aria-label="Ask about your meetings" />
          <button className="primary" disabled={!!pending || !q.trim()}>Ask</button>
        </form>
      </div>
    </main>
  )
}
