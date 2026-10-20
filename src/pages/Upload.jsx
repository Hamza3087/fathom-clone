import React, { useRef, useState } from 'react'
import { upload, useApi } from '../api.js'
import { Field, Modal, go, useApp } from '../ui.jsx'

const ACCEPT = '.wav,.mp3,.m4a,.mp4,.webm,.ogg,.opus,.flac,.aac,.mov,.mkv,audio/*,video/*'
const localNow = () => {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

export function UploadModal({ onClose, initialFile }) {
  const { teams, toast } = useApp()
  const types = useApi('/org/meeting-types')
  const templates = useApi('/templates')
  const input = useRef()
  const [file, setFile] = useState(initialFile || null)
  const [f, setF] = useState({ title: '', date: localNow(), visibility: teams.length ? 'team' : 'private', team_id: teams[0]?.id || '',
    attendees: '', meeting_type_id: '', template: '' })
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState(null)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    if (!file) return setError('Choose a recording to upload.')
    setError(null); setProgress(0)
    try {
      const r = await upload({ file, ...f, team_id: f.visibility === 'team' ? f.team_id : '' }, setProgress)
      toast('Uploaded. Notes will be ready in a minute or two.')
      onClose()
      go(`#/meeting/${r.id}`)
    } catch (err) {
      setError(err.message); setProgress(null)
    }
  }
  return (
    <Modal title="Upload a recording" onClose={onClose} footer={
      <>
        <button className="secondary" onClick={onClose} type="button">Cancel</button>
        <button className="primary" form="upload-form" disabled={progress !== null}>
          {progress === null ? 'Upload and transcribe' : `Uploading ${Math.round(progress * 100)}%`}
        </button>
      </>
    }>
      <form id="upload-form" onSubmit={submit} className="stack-form">
        <div className={`drop ${file ? 'has' : ''}`} onClick={() => input.current.click()}
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); setFile(e.dataTransfer.files[0]) }}>
          {file ? <><b>{file.name}</b><span className="muted">{(file.size / 1e6).toFixed(1)} MB · click to change</span></>
            : <><b>Choose an audio or video file</b><span className="muted">or drop it here. Up to 2 GB.</span></>}
          <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => setFile(e.target.files[0])} />
        </div>
        {progress !== null && <span className="meter"><i style={{ width: `${Math.round(progress * 100)}%` }} /></span>}
        <div className="grid2">
          <Field label="Title" hint="Leave empty and Sounding will name it."><input value={f.title} onChange={set('title')} placeholder="Optional" /></Field>
          <Field label="When it happened"><input type="datetime-local" value={f.date} onChange={set('date')} /></Field>
        </div>
        <Field label="Attendee emails" hint="Teammates listed here get access when notes are ready. Other domains show up under Companies.">
          <input value={f.attendees} onChange={set('attendees')} placeholder="jane@acme.com, sam@yourcompany.com" />
        </Field>
        <div className="grid2">
          <Field label="Who can see it">
            <select value={f.visibility} onChange={set('visibility')}>
              <option value="private">Only me and people I add</option>
              {teams.length > 0 && <option value="team">A team</option>}
              <option value="org">Everyone in the organization</option>
            </select>
          </Field>
          {f.visibility === 'team' && (
            <Field label="Team">
              <select value={f.team_id} onChange={set('team_id')}>{teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
            </Field>
          )}
        </div>
        <div className="grid2">
          <Field label="Meeting type" hint="Picked automatically when left on Detect.">
            <select value={f.meeting_type_id} onChange={set('meeting_type_id')}>
              <option value="">Detect automatically</option>
              {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          <Field label="Notes template">
            <select value={f.template} onChange={set('template')}>
              <option value="">Default for this meeting type</option>
              {templates.data?.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
          </Field>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Modal>
  )
}
