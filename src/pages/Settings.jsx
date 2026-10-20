// Personal settings (profile, notes, API keys, webhooks) and organization admin.
import React, { useEffect, useState } from 'react'
import { del, patch, post, useApi } from '../api.js'
import {
  Avatar, Empty, ErrorNote, Field, Loading, Menu, Modal, PageHead,
  absLink, copyText, fmtAgo, fmtDay, useAction, useApp,
} from '../ui.jsx'
import './insights.css'

function Secret({ value, label }) {
  const { toast } = useApp()
  return (
    <div className="in-secret">
      <code aria-label={label}>{value}</code>
      <button type="button" className="secondary small-btn" onClick={async () => ((await copyText(value)) ? toast('Copied') : toast('Copy failed. Select the text and copy it.', 'error'))}>Copy</button>
    </div>
  )
}

/* ---------- personal settings ---------- */
function Profile() {
  const { me, refreshMe, toast } = useApp()
  const [name, setName] = useState(me.name)
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [busy, run] = useAction(toast)
  const saveName = (e) => {
    e.preventDefault()
    run('name', async () => { await patch('/me', { name }); await refreshMe() }, 'Name saved')
  }
  const savePw = (e) => {
    e.preventDefault()
    if (pw.next !== pw.confirm) return toast('The new passwords don’t match. Type the same password twice.', 'error')
    run('pw', async () => {
      await patch('/me', { current_password: pw.current, new_password: pw.next })
      setPw({ current: '', next: '', confirm: '' })
    }, 'Password changed')
  }
  return (
    <section className="in-section">
      <h2>Profile</h2>
      <p className="muted">Signed in as {me.email}.</p>
      <form className="stack-form" onSubmit={saveName}>
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" /></Field>
        <div><button className="secondary" disabled={busy === 'name' || name.trim() === me.name}>Save name</button></div>
      </form>
      <form className="stack-form" onSubmit={savePw} style={{ marginTop: 20 }}>
        <h3 className="field-label">Change password</h3>
        <Field label="Current password"><input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} required /></Field>
        <div className="grid2">
          <Field label="New password" hint="At least 10 characters."><input type="password" minLength={10} autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} required /></Field>
          <Field label="Confirm new password"><input type="password" minLength={10} autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} required /></Field>
        </div>
        <div><button className="secondary" disabled={busy === 'pw'}>Change password</button></div>
      </form>
    </section>
  )
}

function NotesPrefs() {
  const { me, refreshMe, toast } = useApp()
  const templates = useApi('/templates')
  const [tpl, setTpl] = useState(me.settings?.default_template || 'general')
  const [ins, setIns] = useState(me.settings?.summary_instructions || '')
  const [busy, run] = useAction(toast)
  const save = (e) => {
    e.preventDefault()
    run('notes', async () => { await patch('/me', { settings: { default_template: tpl, summary_instructions: ins } }); await refreshMe() }, 'Notes preferences saved')
  }
  return (
    <section className="in-section">
      <h2>Notes</h2>
      <p className="muted">Used for your meetings when no meeting type sets a template.</p>
      <form className="stack-form" onSubmit={save}>
        <Field label="Default template">
          <select value={tpl} onChange={(e) => setTpl(e.target.value)}>
            {templates.data?.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
          </select>
        </Field>
        <Field label="Custom instructions" hint="Added to every summary Sounding writes for your meetings.">
          <textarea value={ins} maxLength={1500} onChange={(e) => setIns(e.target.value)} placeholder="Keep bullets short. Call out any numbers or dates mentioned." />
        </Field>
        <div><button className="secondary" disabled={busy === 'notes'}>Save preferences</button></div>
      </form>
    </section>
  )
}

function ApiKeys() {
  const { toast } = useApp()
  const keys = useApi('/me/api-keys')
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [created, setCreated] = useState(null)
  const [busy, run] = useAction(toast)
  const create = (e) => {
    e.preventDefault()
    run('create', async () => { setCreated(await post('/me/api-keys', { name })); keys.reload() })
  }
  const close = () => { setOpen(false); setCreated(null); setName('') }
  const remove = (k) => {
    if (!confirm(`Delete the key “${k.name}”? Anything using it stops working.`)) return
    run(k.id, async () => { await del(`/me/api-keys/${k.id}`); keys.reload() }, 'Key deleted')
  }
  return (
    <section className="in-section">
      <h2>API keys</h2>
      <p className="muted">Keys read the meetings you can see through the Sounding API.</p>
      <ErrorNote error={keys.error} retry={keys.reload} />
      {keys.data?.length > 0 && (
        <div className="in-table-wrap">
          <table className="in-table">
            <thead><tr><th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th><span className="sr">Actions</span></th></tr></thead>
            <tbody>
              {keys.data.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td><td><code>{k.prefix}…</code></td><td>{fmtAgo(k.created)}</td><td>{k.last_used ? fmtAgo(k.last_used) : 'Never'}</td>
                  <td><button className="link danger" onClick={() => remove(k)} disabled={busy === k.id}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ marginTop: 12 }}><button className="secondary" onClick={() => setOpen(true)}>Create API key</button></div>
      {open && (
        <Modal title={created ? 'Your new API key' : 'Create API key'} onClose={close} footer={created
          ? <button className="primary" onClick={close}>Done</button>
          : <><button className="secondary" type="button" onClick={close}>Cancel</button><button className="primary" form="key-form" disabled={busy === 'create'}>Create key</button></>}>
          {created ? (
            <div className="stack-form">
              <p className="in-warn">Copy this key now. It won’t be shown again.</p>
              <Secret value={created.key} label="API key" />
            </div>
          ) : (
            <form id="key-form" className="stack-form" onSubmit={create}>
              <Field label="Name" hint="So you can tell your keys apart, for example “Zapier”."><input value={name} onChange={(e) => setName(e.target.value)} required /></Field>
            </form>
          )}
        </Modal>
      )}
    </section>
  )
}

const INCLUDES = [['summary', 'Summary'], ['action_items', 'Action items'], ['transcript', 'Transcript']]

function Webhooks() {
  const { toast } = useApp()
  const hooks = useApi('/me/webhooks')
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ url: '', include: ['summary', 'action_items', 'transcript'] })
  const [created, setCreated] = useState(null)
  const [error, setError] = useState(null)
  const [busy, run] = useAction(toast)
  const create = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      setCreated(await post('/me/webhooks', f)); hooks.reload()
    } catch (err) {
      setError(err.message)
    }
  }
  const close = () => { setOpen(false); setCreated(null); setError(null); setF({ url: '', include: ['summary', 'action_items', 'transcript'] }) }
  const toggle = (k) => setF({ ...f, include: f.include.includes(k) ? f.include.filter((x) => x !== k) : [...f.include, k] })
  const test = (h) => run(h.id, async () => {
    const r = await post(`/me/webhooks/${h.id}/test`)
    toast(`Test sent. Response: ${r.status}`)
    hooks.reload()
  })
  const remove = (h) => {
    if (!confirm('Delete this webhook? Sounding stops sending to that URL.')) return
    run(h.id, async () => { await del(`/me/webhooks/${h.id}`); hooks.reload() }, 'Webhook deleted')
  }
  return (
    <section className="in-section">
      <h2>Webhooks</h2>
      <p className="muted">
        Sounding posts JSON to your URL when notes are ready for a meeting you can see. Each request carries an
        X-Sounding-Signature header: sha256= followed by the HMAC-SHA256 of the body, signed with the webhook's secret.
      </p>
      <ErrorNote error={hooks.error} retry={hooks.reload} />
      {hooks.data?.length > 0 && (
        <div className="in-list">
          {hooks.data.map((h) => (
            <div key={h.id} className="in-item">
              <div className="in-item-main">
                <h3 style={{ wordBreak: 'break-all' }}>{h.url}</h3>
                <p className="in-item-meta">Sends {h.include.map((k) => INCLUDES.find((x) => x[0] === k)?.[1].toLowerCase()).join(', ')} · {h.last_status ? `Last delivery ${h.last_status}` : 'Nothing sent yet'}</p>
              </div>
              <div className="in-item-side">
                <button className="secondary small-btn" onClick={() => test(h)} disabled={busy === h.id}>{busy === h.id ? 'Sending' : 'Send test'}</button>
                <button className="link danger" onClick={() => remove(h)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 12 }}><button className="secondary" onClick={() => setOpen(true)}>Add webhook</button></div>
      {open && (
        <Modal title={created ? 'Webhook added' : 'Add webhook'} onClose={close} footer={created
          ? <button className="primary" onClick={close}>Done</button>
          : <><button className="secondary" type="button" onClick={close}>Cancel</button><button className="primary" form="hook-form" disabled={!f.include.length}>Add webhook</button></>}>
          {created ? (
            <div className="stack-form">
              <p className="in-warn">Copy the signing secret now. It won’t be shown again.</p>
              <Secret value={created.secret} label="Webhook signing secret" />
            </div>
          ) : (
            <form id="hook-form" className="stack-form" onSubmit={create}>
              <Field label="URL" hint="A public http or https address."><input type="url" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://example.com/hooks/sounding" required /></Field>
              <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="field-label">Include</legend>
                <div className="in-checks">
                  {INCLUDES.map(([k, label]) => (
                    <label key={k}><input type="checkbox" checked={f.include.includes(k)} onChange={() => toggle(k)} />{label}</label>
                  ))}
                </div>
              </fieldset>
              {error && <p className="form-error" role="alert">{error}</p>}
            </form>
          )}
        </Modal>
      )}
    </section>
  )
}

function ApiDocs() {
  const base = `${window.location.origin}/api/v1`
  return (
    <section className="in-section">
      <h2>Using the API</h2>
      <p className="muted">Send your key in the X-Api-Key header. Limit: 60 requests a minute. Results include only meetings you can see.</p>
      <pre className="in-code">{`curl -H "X-Api-Key: $SOUNDING_KEY" \\
  "${base}/meetings?include_transcript=true&limit=10"

# Other endpoints
GET ${base}/meetings/{id}
GET ${base}/teams
GET ${base}/team_members?team_id={id}`}</pre>
    </section>
  )
}

export function SettingsPage() {
  return (
    <main className="page">
      <PageHead title="Settings" />
      <div className="in-settings">
        <Profile />
        <NotesPrefs />
        <ApiKeys />
        <Webhooks />
        <ApiDocs />
      </div>
    </main>
  )
}

/* ---------- admin ---------- */
const SECTIONS = [
  ['members', 'Members'], ['teams', 'Teams'], ['meeting-types', 'Meeting types'], ['templates', 'Templates'],
  ['scorecards', 'Scorecards'], ['dictionary', 'Dictionary'], ['organization', 'Organization'],
]

function InviteModal({ onClose, onCreated }) {
  const { teams: myTeams, toast } = useApp()
  const teams = useApi('/org/teams')
  const [f, setF] = useState({ email: '', role: 'member', team_id: '' })
  const [link, setLink] = useState(null)
  const [busy, run] = useAction(toast)
  const submit = (e) => {
    e.preventDefault()
    run('invite', async () => {
      const r = await post('/org/invites', { email: f.email || null, role: f.role, team_id: f.team_id || null })
      setLink(absLink(`#/invite/${r.token}`))
      onCreated()
    })
  }
  const list = teams.data || myTeams
  return (
    <Modal title={link ? 'Invite link ready' : 'Invite people'} onClose={onClose} footer={link
      ? <button className="primary" onClick={onClose}>Done</button>
      : <><button className="secondary" type="button" onClick={onClose}>Cancel</button><button className="primary" form="invite-form" disabled={busy === 'invite'}>Create invite link</button></>}>
      {link ? (
        <div className="stack-form">
          <p>Send this link to {f.email || 'the person you’re inviting'}. It works once and expires in 14 days.</p>
          <Secret value={link} label="Invite link" />
          <p className="muted small">Sounding can’t send email yet, so share the link yourself, for example in Slack.</p>
        </div>
      ) : (
        <form id="invite-form" className="stack-form" onSubmit={submit}>
          <Field label="Email" hint="Optional. If set, only this address can use the link."><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <div className="grid2">
            <Field label="Role">
              <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </Field>
            <Field label="Team" hint="Optional.">
              <select value={f.team_id} onChange={(e) => setF({ ...f, team_id: e.target.value })}>
                <option value="">No team</option>
                {list.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </Field>
          </div>
        </form>
      )}
    </Modal>
  )
}

function Members() {
  const { me, toast, reloadMembers } = useApp()
  const members = useApi('/org/members')
  const invites = useApi('/org/invites')
  const [inviting, setInviting] = useState(false)
  const [busy, run] = useAction(toast)
  const change = (m, body, ok) => run(m.id, async () => { await patch(`/org/members/${m.id}`, body); members.reload(); reloadMembers() }, ok)
  const revoke = (i) => run(i.token, async () => { await del(`/org/invites/${i.token}`); invites.reload() }, 'Invite revoked')
  return (
    <>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div><h2 className="in-title">Members</h2><p className="muted">Admins manage settings and see every meeting. Members see their own meetings and what's shared with them.</p></div>
        <button className="primary" onClick={() => setInviting(true)}>Invite people</button>
      </div>
      <ErrorNote error={members.error} retry={members.reload} />
      {members.loading && !members.data && <Loading />}
      {members.data && (
        <div className="in-table-wrap">
          <table className="in-table">
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Teams</th><th>Status</th></tr></thead>
            <tbody>
              {members.data.map((m) => (
                <tr key={m.id} className={m.active ? '' : 'in-dim'}>
                  <td><span className="in-person"><Avatar name={m.name} id={m.id} size={24} />{m.name}{m.id === me.id && ' (you)'}</span></td>
                  <td>{m.email}</td>
                  <td>
                    <select aria-label={`Role for ${m.name}`} value={m.role} disabled={m.id === me.id || busy === m.id}
                      onChange={(e) => change(m, { role: e.target.value }, `${m.name} is now ${e.target.value === 'admin' ? 'an admin' : 'a member'}`)}>
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td>{m.teams.map((t) => t.name + (t.lead ? ' (lead)' : '')).join(', ') || <span className="in-dim">None</span>}</td>
                  <td>
                    {m.id === me.id ? 'Active' : (
                      <button className={m.active ? 'link danger' : 'link'} disabled={busy === m.id}
                        onClick={() => (m.active ? confirm(`Deactivate ${m.name}? They're logged out and can't sign in.`) : true) &&
                          change(m, { active: !m.active }, m.active ? `${m.name} deactivated` : `${m.name} reactivated`)}>
                        {m.active ? 'Deactivate' : 'Reactivate'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {invites.data?.length > 0 && (
        <>
          <h3 className="field-label" style={{ margin: '24px 0 8px' }}>Pending invites</h3>
          <div className="in-table-wrap">
            <table className="in-table">
              <thead><tr><th>For</th><th>Role</th><th>Created</th><th><span className="sr">Actions</span></th></tr></thead>
              <tbody>
                {invites.data.map((i) => (
                  <tr key={i.token}>
                    <td>{i.email || 'Anyone with the link'}</td>
                    <td>{i.role === 'admin' ? 'Admin' : 'Member'}</td>
                    <td>{fmtAgo(i.created)}</td>
                    <td><button className="link danger" onClick={() => revoke(i)} disabled={busy === i.token}>Revoke</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {inviting && <InviteModal onClose={() => setInviting(false)} onCreated={invites.reload} />}
    </>
  )
}

function TeamCard({ team, members, reload }) {
  const { toast, refreshMe, reloadMembers } = useApp()
  const [add, setAdd] = useState({ user_id: '', lead: false })
  const [busy, run] = useAction(toast)
  const done = () => { reload(); refreshMe(); reloadMembers() }
  const rename = () => {
    const name = prompt('New team name', team.name)
    if (name && name.trim()) run('rename', async () => { await patch(`/org/teams/${team.id}`, { name }); done() }, 'Team renamed')
  }
  const remove = () => {
    if (!confirm(`Delete the team “${team.name}”? Meetings shared with it become private to their owners.`)) return
    run('del', async () => { await del(`/org/teams/${team.id}`); done() }, 'Team deleted')
  }
  const addMember = (e) => {
    e.preventDefault()
    if (!add.user_id) return
    run('add', async () => { await post(`/org/teams/${team.id}/members`, add); setAdd({ user_id: '', lead: false }); done() }, 'Member added')
  }
  const setLead = (m, lead) => run(m.id, async () => { await post(`/org/teams/${team.id}/members`, { user_id: m.id, lead }); done() })
  const removeMember = (m) => run(m.id, async () => { await del(`/org/teams/${team.id}/members/${m.id}`); done() }, `${m.name} removed`)
  const inTeam = new Set(team.members.map((m) => m.id))
  return (
    <section className="in-team">
      <div className="in-team-head">
        <h3>{team.name} <span className="muted small">· {team.members.length} {team.members.length === 1 ? 'member' : 'members'}</span></h3>
        <Menu label={`More actions for ${team.name}`} items={[{ label: 'Rename', onClick: rename }, { label: 'Delete team', danger: true, onClick: remove }]} />
      </div>
      {team.members.length ? (
        <ul className="in-members">
          {team.members.map((m) => (
            <li key={m.id}>
              <span className="in-person"><Avatar name={m.name} id={m.id} size={24} />{m.name}</span>
              <span className="row-gap">
                <label><input type="checkbox" checked={!!m.lead} disabled={busy === m.id} onChange={(e) => setLead(m, e.target.checked)} />Team lead</label>
                <button className="link danger" onClick={() => removeMember(m)} disabled={busy === m.id}>Remove</button>
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="muted small">No members yet.</p>}
      <form className="in-add-member" onSubmit={addMember}>
        <select aria-label={`Add a member to ${team.name}`} value={add.user_id} onChange={(e) => setAdd({ ...add, user_id: e.target.value })}>
          <option value="">Add a member</option>
          {members.filter((m) => m.active && !inTeam.has(m.id)).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        <label className="muted small row-gap"><input type="checkbox" checked={add.lead} onChange={(e) => setAdd({ ...add, lead: e.target.checked })} />Team lead</label>
        <button className="secondary small-btn" disabled={!add.user_id || busy === 'add'}>Add</button>
      </form>
    </section>
  )
}

function Teams() {
  const { members, toast, refreshMe } = useApp()
  const teams = useApi('/org/teams')
  const [name, setName] = useState('')
  const [busy, run] = useAction(toast)
  const create = (e) => {
    e.preventDefault()
    run('create', async () => { await post('/org/teams', { name }); setName(''); teams.reload(); refreshMe() }, 'Team created')
  }
  return (
    <>
      <h2 className="in-title">Teams</h2>
      <p className="muted">Meetings can be shared with a team. Team leads see coaching metrics for their team.</p>
      <form className="in-add-member" onSubmit={create} style={{ marginBottom: 16 }}>
        <input aria-label="New team name" placeholder="New team name" value={name} onChange={(e) => setName(e.target.value)} style={{ maxWidth: 280 }} required />
        <button className="primary" disabled={busy === 'create'}>Create team</button>
      </form>
      <ErrorNote error={teams.error} retry={teams.reload} />
      {teams.loading && !teams.data && <Loading />}
      {teams.data && !teams.data.length && <Empty title="No teams yet">Create a team like Sales or Customer success to share meetings with it.</Empty>}
      {teams.data?.map((t) => <TeamCard key={t.id} team={t} members={members} reload={teams.reload} />)}
    </>
  )
}

function MeetingTypes() {
  const { toast } = useApp()
  const types = useApi('/org/meeting-types')
  const templates = useApi('/templates')
  const [editing, setEditing] = useState(null)
  const [busy, run] = useAction(toast)
  const tplName = (k) => templates.data?.find((t) => t.key === k)?.name || k
  const remove = (t) => {
    if (!confirm(`Delete the meeting type “${t.name}”? Its scorecards and their results are deleted too.`)) return
    run(t.id, async () => { await del(`/org/meeting-types/${t.id}`); types.reload() }, 'Meeting type deleted')
  }
  return (
    <>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div><h2 className="in-title">Meeting types</h2><p className="muted">Sounding reads the start of each meeting and picks the type whose description fits. The type decides the notes template and which scorecards apply.</p></div>
        <button className="primary" onClick={() => setEditing({ name: '', description: '', template: 'general' })}>Add meeting type</button>
      </div>
      <ErrorNote error={types.error} retry={types.reload} />
      {types.data && !types.data.length && <Empty title="No meeting types yet">Add types like Sales discovery or Weekly 1:1 so each gets the right notes.</Empty>}
      <div className="in-list">
        {types.data?.map((t) => (
          <div key={t.id} className="in-item">
            <div className="in-item-main">
              <h3>{t.name}</h3>
              <p className="in-item-meta">{t.description}</p>
              <p className="in-item-meta">Notes template: {tplName(t.template)}</p>
            </div>
            <div className="in-item-side">
              <button className="secondary small-btn" onClick={() => setEditing(t)}>Edit</button>
              <button className="link danger" onClick={() => remove(t)} disabled={busy === t.id}>Delete</button>
            </div>
          </div>
        ))}
      </div>
      {editing && (
        <EditModal title={editing.id ? 'Edit meeting type' : 'Add meeting type'} initial={editing} onClose={() => setEditing(null)}
          save={(v) => (editing.id ? patch(`/org/meeting-types/${editing.id}`, v) : post('/org/meeting-types', v))}
          onSaved={types.reload}
          fields={(v, set) => (
            <>
              <Field label="Name"><input value={v.name} onChange={set('name')} required /></Field>
              <Field label="Description" hint="Used to detect this type automatically. Say who is on the call and what it's for.">
                <textarea value={v.description} onChange={set('description')} required placeholder="A first call with a prospect to learn about their needs and budget." />
              </Field>
              <Field label="Notes template">
                <select value={v.template} onChange={set('template')}>{templates.data?.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}</select>
              </Field>
            </>
          )} />
      )}
    </>
  )
}

// A small form modal: fields(value, set) renders inputs; save(value) returns a promise.
function EditModal({ title, initial, fields, save, onSaved, onClose }) {
  const [v, setV] = useState(initial)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setV({ ...v, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      await save(v); onSaved(); onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={title} onClose={onClose} wide footer={
      <><button className="secondary" type="button" onClick={onClose}>Cancel</button><button className="primary" form="edit-form" disabled={busy}>{busy ? 'Saving' : 'Save'}</button></>
    }>
      <form id="edit-form" className="stack-form" onSubmit={submit}>
        {fields(v, set, setV)}
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}

function Templates() {
  const { toast } = useApp()
  const templates = useApi('/templates')
  const [editing, setEditing] = useState(null)
  const [busy, run] = useAction(toast)
  const builtins = templates.data?.filter((t) => t.builtin) || []
  const custom = templates.data?.filter((t) => !t.builtin) || []
  const remove = (t) => {
    if (!confirm(`Delete the template “${t.name}”? Meetings already summarized keep their notes.`)) return
    run(t.id, async () => { await del(`/org/templates/${t.id}`); templates.reload() }, 'Template deleted')
  }
  return (
    <>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div><h2 className="in-title">Templates</h2><p className="muted">A custom template starts from a built-in one and adds your instructions.</p></div>
        <button className="primary" onClick={() => setEditing({ name: '', base: 'general', instructions: '' })}>Add template</button>
      </div>
      <ErrorNote error={templates.error} retry={templates.reload} />
      {templates.loading && !templates.data && <Loading />}
      <h3 className="field-label" style={{ margin: '8px 0' }}>Your templates</h3>
      {templates.data && !custom.length && <p className="muted small">None yet.</p>}
      <div className="in-list">
        {custom.map((t) => (
          <div key={t.id} className="in-item">
            <div className="in-item-main">
              <h3>{t.name}</h3>
              <p className="in-item-meta">Based on {builtins.find((b) => b.key === t.base)?.name || t.base}</p>
              <p className="in-item-meta">{t.instructions}</p>
            </div>
            <div className="in-item-side">
              <button className="secondary small-btn" onClick={() => setEditing(t)}>Edit</button>
              <button className="link danger" onClick={() => remove(t)} disabled={busy === t.id}>Delete</button>
            </div>
          </div>
        ))}
      </div>
      <h3 className="field-label" style={{ margin: '24px 0 8px' }}>Built-in templates</h3>
      <div className="in-list">
        {builtins.map((t) => (
          <div key={t.key} className="in-item in-builtin"><div><b>{t.name}</b><p>{t.sections.join(' · ')}</p></div></div>
        ))}
      </div>
      {editing && (
        <EditModal title={editing.id ? 'Edit template' : 'Add template'} initial={editing} onClose={() => setEditing(null)}
          save={(v) => (editing.id ? patch(`/org/templates/${editing.id}`, v) : post('/org/templates', v))}
          onSaved={templates.reload}
          fields={(v, set) => (
            <>
              <Field label="Name"><input value={v.name} onChange={set('name')} required /></Field>
              <Field label="Start from">
                <select value={v.base} onChange={set('base')}>{builtins.map((b) => <option key={b.key} value={b.key}>{b.name}</option>)}</select>
              </Field>
              <Field label="Instructions" hint="What to add or emphasize. Sections come from the template you start from.">
                <textarea value={v.instructions} onChange={set('instructions')} maxLength={1500} required placeholder="Always note the budget, the decision maker and the next meeting date." />
              </Field>
            </>
          )} />
      )}
    </>
  )
}

function CriteriaEditor({ value, onChange }) {
  const set = (i, k, x) => onChange(value.map((c, j) => (j === i ? { ...c, [k]: x } : c)))
  return (
    <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="field-label">Criteria</legend>
      <div className="stack-form" style={{ gap: 8 }}>
        {value.map((c, i) => (
          <div key={i} className="in-crit-row">
            <input aria-label={`Criterion ${i + 1} name`} placeholder="Name" value={c.name} onChange={(e) => set(i, 'name', e.target.value)} required />
            <input aria-label={`Criterion ${i + 1} description`} placeholder="What good looks like" value={c.description} onChange={(e) => set(i, 'description', e.target.value)} />
            <button type="button" className="icon-btn" aria-label={`Remove criterion ${i + 1}`} disabled={value.length <= 1}
              onClick={() => onChange(value.filter((_, j) => j !== i))}>
              <svg viewBox="0 0 24 24" width="16" height="16"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" /></svg>
            </button>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8 }}>
        <button type="button" className="secondary small-btn" disabled={value.length >= 12} onClick={() => onChange([...value, { name: '', description: '' }])}>Add criterion</button>
        <span className="field-hint"> Between 1 and 12.</span>
      </div>
    </fieldset>
  )
}

function ScorecardAdmin() {
  const { toast } = useApp()
  const cards = useApi('/org/scorecards')
  const types = useApi('/org/meeting-types')
  const [editing, setEditing] = useState(null)
  const [busy, run] = useAction(toast)
  const typeName = (id) => types.data?.find((t) => t.id === id)?.name || 'Deleted type'
  const remove = (s) => {
    if (!confirm(`Delete the scorecard “${s.name}” and all its scores?`)) return
    run(s.id, async () => { await del(`/org/scorecards/${s.id}`); cards.reload() }, 'Scorecard deleted')
  }
  const start = () => setEditing({ name: '', meeting_type_id: types.data?.[0]?.id || '', criteria: [{ name: '', description: '' }] })
  return (
    <>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div><h2 className="in-title">Scorecards</h2><p className="muted">A scorecard grades every call of one meeting type, scoring the meeting owner on each criterion from 0 to 10. Saving rescores past calls of that type.</p></div>
        <button className="primary" onClick={start} disabled={!types.data?.length}>Add scorecard</button>
      </div>
      {types.data && !types.data.length && <p className="in-warn">Add a meeting type first. Scorecards apply to one meeting type. <a href="#/admin/meeting-types">Add a meeting type</a></p>}
      <ErrorNote error={cards.error} retry={cards.reload} />
      {cards.data && !cards.data.length && types.data?.length > 0 && <Empty title="No scorecards yet">Add one to grade calls consistently, for example discovery quality on sales calls.</Empty>}
      <div className="in-list">
        {cards.data?.map((s) => (
          <div key={s.id} className="in-item">
            <div className="in-item-main">
              <h3>{s.name}</h3>
              <p className="in-item-meta">For {typeName(s.meeting_type_id)} calls · {s.criteria.length} {s.criteria.length === 1 ? 'criterion' : 'criteria'}</p>
              <div className="in-kw">{s.criteria.map((c) => <span key={c.name} className="tag quiet" title={c.description}>{c.name}</span>)}</div>
            </div>
            <div className="in-item-side">
              <button className="secondary small-btn" onClick={() => setEditing(s)}>Edit</button>
              <button className="link danger" onClick={() => remove(s)} disabled={busy === s.id}>Delete</button>
            </div>
          </div>
        ))}
      </div>
      {editing && (
        <EditModal title={editing.id ? 'Edit scorecard' : 'Add scorecard'} initial={editing} onClose={() => setEditing(null)}
          save={(v) => (editing.id ? patch(`/org/scorecards/${editing.id}`, v) : post('/org/scorecards', v))}
          onSaved={() => { cards.reload(); toast('Saved. Scoring past calls in the background.') }}
          fields={(v, set, setV) => (
            <>
              <div className="grid2">
                <Field label="Name"><input value={v.name} onChange={set('name')} required /></Field>
                <Field label="Meeting type">
                  <select value={v.meeting_type_id} onChange={set('meeting_type_id')} required>
                    {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </Field>
              </div>
              <CriteriaEditor value={v.criteria} onChange={(criteria) => setV({ ...v, criteria })} />
            </>
          )} />
      )}
    </>
  )
}

const termProblem = (t) => (t.length > 100 ? 'longer than 100 characters' : t.split(/\s+/).filter(Boolean).length > 6 ? 'more than 6 words' : null)

function Dictionary() {
  const { toast } = useApp()
  const settings = useApi('/org/settings')
  const [terms, setTerms] = useState(null)
  const [draft, setDraft] = useState('')
  const [busy, run] = useAction(toast)
  useEffect(() => { if (settings.data && terms === null) setTerms(settings.data.dictionary) }, [settings.data, terms])
  if (!terms) return settings.error ? <ErrorNote error={settings.error} retry={settings.reload} /> : <Loading />
  const add = (e) => {
    e.preventDefault()
    const t = draft.trim()
    if (!t) return
    if (terms.some((x) => x.toLowerCase() === t.toLowerCase())) return toast('That term is already in the list.', 'error')
    setTerms([...terms, t]); setDraft('')
  }
  const problems = terms.map(termProblem)
  const invalid = problems.some(Boolean) || terms.length > 50
  const dirty = JSON.stringify(terms) !== JSON.stringify(settings.data.dictionary)
  const save = () => run('save', async () => { const r = await patch('/org/settings', { dictionary: terms }); setTerms(r.dictionary); settings.reload() }, 'Dictionary saved')
  return (
    <>
      <h2 className="in-title">Transcription dictionary</h2>
      <p className="muted">Names, products and jargon spelled the way your team writes them. Sounding fixes their spelling in new transcripts and uses them when writing notes.</p>
      <form className="in-add-member" onSubmit={add}>
        <input aria-label="New term" placeholder="Add a term, like Acme Cloud" value={draft} onChange={(e) => setDraft(e.target.value)} style={{ maxWidth: 320 }} maxLength={100} />
        <button className="secondary" disabled={!draft.trim() || terms.length >= 50}>Add term</button>
      </form>
      <div className="in-terms">
        {terms.map((t, i) => (
          <span key={t} className={problems[i] ? 'in-term bad' : 'in-term'} title={problems[i] ? `This term is ${problems[i]}.` : undefined}>
            {t}
            <button type="button" aria-label={`Remove ${t}`} onClick={() => setTerms(terms.filter((_, j) => j !== i))}>
              <svg viewBox="0 0 24 24" width="12" height="12"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.4" /></svg>
            </button>
          </span>
        ))}
        {!terms.length && <p className="muted small">No terms yet.</p>}
      </div>
      <p className="muted small">{terms.length} of 50 terms. Each term can be up to 6 words and 100 characters.</p>
      {invalid && <p className="form-error">Shorten or remove the terms marked in red, then save.</p>}
      <div style={{ marginTop: 12 }}><button className="primary" onClick={save} disabled={!dirty || invalid || busy === 'save'}>Save dictionary</button></div>
    </>
  )
}

function Organization() {
  const { org, refreshMe, toast } = useApp()
  const [name, setName] = useState(org.name)
  const [busy, run] = useAction(toast)
  const save = (e) => {
    e.preventDefault()
    run('save', async () => { await patch('/org/settings', { name }); await refreshMe() }, 'Organization renamed')
  }
  return (
    <>
      <h2 className="in-title">Organization</h2>
      <p className="muted">The name people see when they join and on share links.</p>
      <form className="stack-form" onSubmit={save} style={{ maxWidth: 420 }}>
        <Field label="Organization name"><input value={name} onChange={(e) => setName(e.target.value)} required /></Field>
        <div><button className="primary" disabled={busy === 'save' || !name.trim() || name.trim() === org.name}>Save name</button></div>
      </form>
    </>
  )
}

export function AdminPage({ section }) {
  const { isAdmin } = useApp()
  if (!isAdmin) {
    return <main className="page"><Empty title="Only admins can open organization settings">Ask an admin in your organization if you need something changed.</Empty></main>
  }
  const body = {
    members: <Members />, teams: <Teams />, 'meeting-types': <MeetingTypes />, templates: <Templates />,
    scorecards: <ScorecardAdmin />, dictionary: <Dictionary />, organization: <Organization />,
  }[section]
  return (
    <main className="page wide">
      <PageHead title="Organization settings" />
      <div className="in-admin">
        <nav className="in-subnav" aria-label="Organization settings">
          {SECTIONS.map(([k, label]) => (
            <a key={k} href={`#/admin/${k}`} className={section === k ? 'on' : ''} aria-current={section === k ? 'page' : undefined}>{label}</a>
          ))}
        </nav>
        <div className="in-admin-body">
          {body || <Empty title="That section doesn't exist" action={<a href="#/admin/members">Go to members</a>} />}
        </div>
      </div>
    </main>
  )
}
