// Client for the Sounding API (FastAPI on the GPU box; proxied at /api in dev, served by Apache on :3000).
import { useCallback, useEffect, useRef, useState } from 'react'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

export async function http(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const r = await fetch(`/api${path}`, opts)
  const data = await r.json().catch(() => ({}))
  if (r.status === 401 && !path.startsWith('/auth/') && !path.startsWith('/public/')) {
    window.dispatchEvent(new Event('sounding:logged-out'))
  }
  if (!r.ok) throw new ApiError(data.detail || `Request failed (${r.status})`, r.status)
  return data
}

export const get = (p) => http('GET', p)
export const post = (p, b = {}) => http('POST', p, b)
export const patch = (p, b) => http('PATCH', p, b)
export const put = (p, b) => http('PUT', p, b)
export const del = (p) => http('DELETE', p)

export const qs = (o) => {
  const s = new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== ''))
  return s.toString() ? `?${s}` : ''
}

// Media URLs are same-origin, so the session cookie authorizes them.
export const mediaUrl = (id, kind = 'audio') => `/api/meetings/${id}/media?kind=${kind}`
export const clipUrl = (id, kind = 'audio') => `/api/highlights/${id}/clip?kind=${kind}`
export const downloadUrl = (id, format) => `/api/meetings/${id}/download?format=${format}`

// XHR rather than fetch so the upload can report progress.
export function upload(fields, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest()
    const form = new FormData()
    Object.entries(fields).forEach(([k, v]) => v !== undefined && v !== null && form.append(k, v))
    x.open('POST', '/api/meetings')
    x.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
    x.onload = () => {
      let body = {}
      try { body = JSON.parse(x.responseText) } catch { /* not JSON */ }
      if (x.status === 401) window.dispatchEvent(new Event('sounding:logged-out'))
      x.status < 300 ? resolve(body) : reject(new ApiError(body.detail || `Upload failed (${x.status})`, x.status))
    }
    x.onerror = () => reject(new ApiError('Upload failed. Check your connection and try again.', 0))
    x.send(form)
  })
}

export const STAGES = {
  queued: 'Waiting to start',
  converting: 'Preparing recording',
  speakers: 'Finding speakers',
  transcribing: 'Transcribing',
  summarizing: 'Writing notes',
  finishing: 'Running trackers',
  done: 'Ready',
  failed: 'Failed',
}
export const busy = (stage) => !!stage && stage !== 'done' && stage !== 'failed'

// Loads `path` and reloads when it changes. `poll(data)` returns ms until the next refresh, or 0 to stop.
export function useApi(path, { poll } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  const timer = useRef()
  const pollRef = useRef(poll)
  pollRef.current = poll
  const load = useCallback(async (quiet) => {
    clearTimeout(timer.current)
    if (!path) return
    if (!quiet) setState((s) => ({ ...s, loading: true }))
    try {
      const data = await get(path)
      setState({ data, error: null, loading: false })
      const next = pollRef.current?.(data)
      if (next) timer.current = setTimeout(() => load(true), next)
    } catch (e) {
      setState((s) => ({ data: s.data, error: e, loading: false }))
    }
  }, [path])
  useEffect(() => {
    setState({ data: null, error: null, loading: !!path })
    load()
    return () => clearTimeout(timer.current)
  }, [load, path])
  const setData = useCallback((fn) => setState((s) => ({ ...s, data: typeof fn === 'function' ? fn(s.data) : fn })), [])
  return { ...state, reload: () => load(true), setData }
}
