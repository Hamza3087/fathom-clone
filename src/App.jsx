import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { get, post, useApi } from './api.js'
import { AppCtx, Loading, Menu, fmtAgo, go, useRoute, useToasts, Avatar } from './ui.jsx'
import { LoginPage, InvitePage } from './pages/Auth.jsx'
import { UploadModal } from './pages/Upload.jsx'
import { PublicShare } from './pages/Shared.jsx'
import { MeetingsPage, SearchPage } from './pages/Meetings.jsx'
import { MeetingPage } from './pages/Meeting.jsx'
import { FoldersPage, FolderPage, PlaylistsPage, PlaylistPage, CompaniesPage, CompanyPage } from './pages/Collections.jsx'
import { TrackersPage, TrackerPage, CoachingPage, AskPage } from './pages/Insights.jsx'
import { SettingsPage, AdminPage } from './pages/Settings.jsx'

const Icon = ({ d }) => <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
const I = {
  mine: 'M4 6h16M4 12h16M4 18h10',
  team: 'M16 19v-1a4 4 0 0 0-8 0v1M12 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM20 19v-1a3 3 0 0 0-2-2.8M4 19v-1a3 3 0 0 1 2-2.8',
  shared: 'M8 12h8M13 8l4 4-4 4M4 5v14',
  ask: 'M12 3a8 8 0 0 0-7 12l-1 5 5-1a8 8 0 1 0 3-16z',
  folders: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  playlists: 'M4 6h12M4 12h12M4 18h8M17 15v6l4-3z',
  companies: 'M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16M16 9h2a2 2 0 0 1 2 2v10M8 7h4M8 11h4M8 15h4',
  trackers: 'M12 3v3M12 18v3M3 12h3M18 12h3M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  coaching: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 21a2 2 0 0 0 4 0',
}

const NAV = [
  ['', 'My calls', 'mine'], ['team', 'Team calls', 'team'], ['shared', 'Shared with me', 'shared'], ['ask', 'Ask Sounding', 'ask'],
  null,
  ['folders', 'Folders', 'folders'], ['playlists', 'Playlists', 'playlists'], ['companies', 'Companies', 'companies'],
  ['trackers', 'Trackers', 'trackers'], ['coaching', 'Coaching', 'coaching'],
]

function Notifications() {
  const n = useApi('/notifications', { poll: () => 20000 })
  const [open, setOpen] = useState(false)
  const unread = n.data?.unread || 0
  const openItem = async (it) => {
    setOpen(false)
    if (!it.read) post('/notifications/read', { ids: [it.id] }).then(n.reload)
    if (it.link) go(it.link)
  }
  return (
    <span className="menu-wrap">
      <button className="icon-btn bell" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`} onClick={() => setOpen((o) => !o)}>
        <Icon d={I.bell} />
        {unread > 0 && <span className="badge">{unread > 9 ? '9+' : unread}</span>}
      </button>
      {open && (
        <>
          <div className="scrim" onClick={() => setOpen(false)} />
          <div className="menu right notif" role="dialog" aria-label="Notifications">
            <div className="notif-head">
              <b>Notifications</b>
              {unread > 0 && <button className="link" onClick={() => post('/notifications/read', { all: true }).then(n.reload)}>Mark all read</button>}
            </div>
            {!n.data?.items.length && <p className="muted notif-empty">Nothing yet. You'll hear about mentions, shares, assignments and trackers here.</p>}
            {n.data?.items.map((it) => (
              <button key={it.id} className={`notif-item${it.read ? '' : ' unread'}`} onClick={() => openItem(it)}>
                <span>{it.body}</span>
                <span className="muted small">{fmtAgo(it.created)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </span>
  )
}

function TopBar({ onUpload }) {
  const { me, isAdmin, logout } = useAppInternal()
  const [q, setQ] = useState('')
  return (
    <div className="topbar">
      <form className="global-search" role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) go(`#/search?q=${encodeURIComponent(q.trim())}`) }}>
        <label className="sr" htmlFor="gsearch">Search all meetings</label>
        <input id="gsearch" type="search" placeholder="Search meetings, transcripts and notes" value={q} onChange={(e) => setQ(e.target.value)} />
      </form>
      <button className="primary" onClick={onUpload}>Upload recording</button>
      <Notifications />
      <Menu label="Account" className="avatar-btn" items={[
        { label: 'Settings', onClick: () => go('#/settings') },
        ...(isAdmin ? [{ label: 'Organization settings', onClick: () => go('#/admin') }] : []),
        null,
        me.demo ? { label: 'Admin sign-in', onClick: () => go('#/login') } : { label: 'Log out', onClick: logout },
      ]}>
        <Avatar name={me.name} id={me.id} size={32} />
      </Menu>
    </div>
  )
}

const InternalCtx = React.createContext(null)
const useAppInternal = () => React.useContext(InternalCtx)

function Shell({ session, setSession, route }) {
  const { toast, view: toasts } = useToasts()
  const members = useApi('/org/members')
  const [upload, setUpload] = useState(null) // null | {file}
  const refreshMe = useCallback(async () => setSession(await get('/auth/me')), [setSession])
  const logout = useCallback(async () => {
    await post('/auth/logout').catch(() => {})
    go('#/')
    window.dispatchEvent(new Event('sounding:logged-out'))  // back into the shared demo workspace
  }, [])
  const ctx = useMemo(() => ({
    me: session.user, org: session.org, teams: session.teams, isAdmin: session.user.role === 'admin', isLead: session.is_lead,
    members: members.data || [], reloadMembers: members.reload, refreshMe, toast, openUpload: (file) => setUpload({ file }),
  }), [session, members.data, members.reload, refreshMe, toast])

  useEffect(() => {
    // Dropping a file anywhere opens the upload dialog with it.
    const over = (e) => e.dataTransfer?.types?.includes('Files') && e.preventDefault()
    const drop = (e) => { if (e.dataTransfer?.files?.length && !e.defaultPrevented) { e.preventDefault(); setUpload({ file: e.dataTransfer.files[0] }) } }
    window.addEventListener('dragover', over); window.addEventListener('drop', drop)
    return () => { window.removeEventListener('dragover', over); window.removeEventListener('drop', drop) }
  }, [])

  const [p0, p1] = route.parts
  let page
  switch (p0 || '') {
    case '': page = <MeetingsPage scope="mine" />; break
    case 'team': page = <MeetingsPage scope="team" />; break
    case 'shared': page = <MeetingsPage scope="shared" />; break
    case 'search': page = <SearchPage q={route.query.q || ''} />; break
    case 'meeting': page = <MeetingPage key={p1} id={p1} query={route.query} />; break
    case 'folders': page = p1 ? <FolderPage key={p1} id={p1} /> : <FoldersPage />; break
    case 'playlists': page = p1 ? <PlaylistPage key={p1} id={p1} /> : <PlaylistsPage />; break
    case 'companies': page = p1 ? <CompanyPage key={p1} domain={p1} /> : <CompaniesPage />; break
    case 'trackers': page = p1 ? <TrackerPage key={p1} id={p1} /> : <TrackersPage />; break
    case 'coaching': page = <CoachingPage />; break
    case 'ask': page = <AskPage />; break
    case 'settings': page = <SettingsPage />; break
    case 'admin': page = <AdminPage section={p1 || 'members'} />; break
    default: page = <main className="page"><p className="empty-title">That page doesn't exist.</p><a href="#/">Go to My calls</a></main>
  }
  const active = p0 === 'meeting' || p0 === 'search' ? null : p0 || ''
  return (
    <AppCtx.Provider value={ctx}>
      <InternalCtx.Provider value={{ ...ctx, logout }}>
        <div className="shell">
          <nav className="rail" aria-label="Main">
            <a className="brand" href="#/"><span className="brand-mark" aria-hidden="true" />Sounding</a>
            <p className="org-name">{session.org.name}</p>
            {NAV.map((n, i) => n === null ? <hr key={i} /> : (
              <a key={n[0]} href={`#/${n[0]}`} className={active === n[0] ? 'rail-link on' : 'rail-link'} aria-current={active === n[0] ? 'page' : undefined}>
                <Icon d={I[n[2]]} />{n[1]}
              </a>
            ))}
          </nav>
          <div className="main-col">
            <TopBar onUpload={() => setUpload({})} />
            {session.user.demo && (
              <p className="demo-banner">
                You're in the shared demo workspace, no sign-in needed. Everything works except changing people and account settings, and it resets every night.
              </p>
            )}
            {page}
          </div>
        </div>
        {upload && <UploadModal initialFile={upload.file} onClose={() => setUpload(null)} />}
        {toasts}
      </InternalCtx.Provider>
    </AppCtx.Provider>
  )
}

export default function App() {
  const route = useRoute()
  const [session, setSession] = useState(null) // null = checking, false = logged out
  // No sign-in wall: visitors go straight into the shared demo workspace. Admins sign in at #/login.
  const enter = useCallback(async () => {
    try { setSession(await get('/auth/me')) } catch {
      try { setSession(await post('/auth/demo')) } catch { setSession(false) }
    }
  }, [])
  useEffect(() => {
    enter()
    window.addEventListener('sounding:logged-out', enter)
    return () => window.removeEventListener('sounding:logged-out', enter)
  }, [enter])
  if (route.parts[0] === 's' && route.parts[1]) return <PublicShare token={route.parts[1]} />
  if (route.parts[0] === 'invite' && route.parts[1]) return <InvitePage token={route.parts[1]} onDone={setSession} />
  if (route.parts[0] === 'login' && !(session && !session.user.demo)) {
    return <LoginPage onDone={(s) => { setSession(s); go('#/') }} />
  }
  if (session === null) return <Loading label="Opening Sounding" />
  if (session === false) return <LoginPage onDone={setSession} />
  return <Shell session={session} setSession={setSession} route={route} />
}
