// The meeting row used by every list (My calls, Team calls, folders, companies, search).
import React from 'react'
import { STAGES, busy } from './api.js'
import { Faces, Spinner, fmtDate, fmtMinutes } from './ui.jsx'

export function MeetingRow({ m, extra, actions }) {
  const working = busy(m.stage)
  return (
    <div className="mrow">
      <a className="mrow-link" href={`#/meeting/${m.id}`}>
        <div className="mrow-main">
          <h3>{m.title}</h3>
          <p className="mrow-meta">
            {fmtDate(m.date)}
            {m.duration ? ` · ${fmtMinutes(m.duration)}` : ''}
            {!m.mine && m.owner ? ` · ${m.owner.name}` : ''}
          </p>
          <div className="mrow-tags">
            {m.meeting_type && <span className="tag">{m.meeting_type.name}</span>}
            {m.companies?.map((c) => <span key={c} className="tag company">{c}</span>)}
            {m.visibility === 'private' && <span className="tag quiet">Private</span>}
            {m.visibility === 'team' && m.team && <span className="tag quiet">{m.team.name}</span>}
            {m.has_video && <span className="tag quiet">Video</span>}
          </div>
          {m.overview && !working && <p className="mrow-overview">{m.overview}</p>}
          {extra}
        </div>
        <div className="mrow-side">
          {working ? (
            <span className="pill work"><Spinner />{m.stage === 'queued' && m.queue ? `${m.queue} ahead in line` : STAGES[m.stage]}</span>
          ) : m.stage === 'failed' ? (
            <span className="pill bad">Failed</span>
          ) : (
            <>
              <Faces names={m.speakers || []} list={m.speakers || []} />
              <span className={m.open_actions ? 'pill open' : 'pill'}>{m.open_actions ? `${m.open_actions} open` : 'All done'}</span>
            </>
          )}
        </div>
      </a>
      {actions && <div className="mrow-actions">{actions}</div>}
    </div>
  )
}

export function MeetingRows({ rows, empty, rowActions }) {
  if (!rows.length) return empty || null
  return <div className="mrows">{rows.map((m) => <MeetingRow key={m.id} m={m} actions={rowActions?.(m)} />)}</div>
}
