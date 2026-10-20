// Records the product walkthrough against the live app, with on-screen captions, a visible cursor,
// highlight rings and chapter cards. Frames come from Chrome's screencast (sharper than Playwright's
// built-in video); build_video.py turns them into an MP4 and fast-forwards the AI wait times.
//
//   node record_walkthrough.mjs        env: BASE, OUT (frames dir), SAMPLE (mp3 to upload), CHROME
import { chromium } from 'playwright-core'
import fs from 'node:fs'

const BASE = process.env.BASE || 'http://5.9.117.80:3000/'
const OUT = process.env.OUT
const SAMPLE = process.env.SAMPLE
const BIG = 'Research Progress and Meeting Overlap Analysis'
fs.mkdirSync(`${OUT}/frames`, { recursive: true })

// ---------------------------------------------------------------- overlay (runs inside the page)
const OVERLAY = () => {
  if (window.__ov) return
  const install = () => {
    if (document.getElementById('__ov')) return
    const st = document.createElement('style')
    st.textContent = `
      #__ov{position:fixed;inset:0;pointer-events:none;z-index:2147483647;font-family:'Schibsted Grotesk',system-ui,sans-serif}
      #__ov .cur{position:absolute;left:0;top:0;width:28px;height:28px;transform:translate(-4px,-3px);transition:none;filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))}
      #__ov .rip{position:absolute;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;border:3px solid #ff7a1a;animation:__r .6s ease-out forwards}
      @keyframes __r{from{transform:scale(.4);opacity:1}to{transform:scale(3.2);opacity:0}}
      @keyframes __d{from{background-position:0% 0%}to{background-position:100% 100%}}
      #__ov .ring{position:absolute;border:3px solid #ff7a1a;border-radius:12px;box-shadow:0 0 0 6px rgba(255,122,26,.18),0 0 24px rgba(255,122,26,.35);transition:all .35s ease;opacity:0}
      #__ov .ring.on{opacity:1}
      #__ov .ring .tag{position:absolute;left:-3px;top:-34px;background:#ff7a1a;color:#fff;font-weight:700;font-size:16px;padding:4px 10px;border-radius:8px;white-space:nowrap}
      #__ov .cap{position:absolute;left:50%;bottom:28px;transform:translateX(-50%);width:min(1240px,90vw);background:rgba(10,24,28,.92);color:#fff;border-radius:16px;padding:18px 26px 20px;box-shadow:0 10px 40px rgba(0,0,0,.35);opacity:0;transition:opacity .3s}
      #__ov .cap.on{opacity:1}
      #__ov .cap .k{display:flex;gap:12px;align-items:center;font-size:15px;color:#8fd3d8;font-weight:700;margin-bottom:6px}
      #__ov .cap .k .n{background:#0f6b73;color:#fff;border-radius:999px;padding:2px 10px}
      #__ov .cap .k .ff{margin-left:auto;background:#ff7a1a;color:#fff;border-radius:999px;padding:2px 10px;display:none}
      #__ov .cap.fast .k .ff{display:inline-block}
      #__ov .cap .t{font-size:25px;line-height:1.35;font-weight:500}
      #__ov .card{position:absolute;inset:0;background:radial-gradient(circle at 30% 20%,#123b42,#0a181c 70%);background-size:140% 140%;animation:__d 6s ease-in-out infinite alternate;color:#e9f4f2;display:grid;place-items:center;opacity:0;transition:opacity .45s}
      #__ov .card.on{opacity:1}
      #__ov .card .in{max-width:1180px;padding:0 60px}
      #__ov .card .eyebrow{color:#57c2c9;font-weight:700;font-size:22px;margin-bottom:14px}
      #__ov .card h1{font-size:64px;line-height:1.08;margin:0 0 18px;letter-spacing:-.02em}
      #__ov .card p{font-size:27px;line-height:1.45;color:#b9d3d0;margin:0 0 10px}
      #__ov .card ul{font-size:25px;line-height:1.5;color:#dcebe9;margin:22px 0 0;padding-left:28px}
      #__ov .card li{margin:6px 0}`
    document.head.appendChild(st)
    const root = document.createElement('div'); root.id = '__ov'
    root.innerHTML = `<div class="ring"><span class="tag"></span></div><div class="cap"><div class="k"><span class="n"></span><span class="h"></span><span class="ff">Sped up</span></div><div class="t"></div></div><div class="card"><div class="in"></div></div>
      <svg class="cur" viewBox="0 0 24 24"><path d="M4 2l15 11.2-6.6 1 3.9 7.3-2.9 1.5-3.8-7.4L4 20z" fill="#fff" stroke="#0a181c" stroke-width="1.4" stroke-linejoin="round"/></svg>`
    document.documentElement.appendChild(root)
    const cur = root.querySelector('.cur')
    const s = window.__ovState || { x: 960, y: 540 }
    cur.style.left = s.x + 'px'; cur.style.top = s.y + 'px'
    addEventListener('mousemove', (e) => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; window.__ovState = { x: e.clientX, y: e.clientY } }, true)
    addEventListener('mousedown', (e) => {
      const r = document.createElement('div'); r.className = 'rip'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'
      root.appendChild(r); setTimeout(() => r.remove(), 700)
    }, true)
  }
  window.__ov = {
    ring(rect, tag) {
      install(); const r = document.querySelector('#__ov .ring')
      if (!rect) { r.classList.remove('on'); return }
      const p = 8
      Object.assign(r.style, { left: rect.x - p + 'px', top: rect.y - p + 'px', width: rect.width + 2 * p + 'px', height: rect.height + 2 * p + 'px' })
      const t = r.querySelector('.tag'); t.textContent = tag || ''; t.style.display = tag ? 'block' : 'none'
      if (rect.y < 60) t.style.top = rect.height + 2 * p + 6 + 'px'; else t.style.top = '-34px'
      if (rect.x + 260 > innerWidth) { t.style.left = 'auto'; t.style.right = '-3px' } else { t.style.left = '-3px'; t.style.right = 'auto' }
      r.classList.add('on')
    },
    cap(n, h, text, fast) {
      install(); const c = document.querySelector('#__ov .cap')
      if (!text) { c.classList.remove('on'); return }
      c.querySelector('.n').textContent = n; c.querySelector('.h').textContent = h; c.querySelector('.t').textContent = text
      c.classList.toggle('fast', !!fast); c.classList.add('on')
    },
    card(html) {
      install(); const c = document.querySelector('#__ov .card')
      if (!html) { c.classList.remove('on'); return }
      c.querySelector('.in').innerHTML = html; c.classList.add('on')
    },
  }
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', install); else install()
}

// ---------------------------------------------------------------- browser + screencast
const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required', '--hide-scrollbars'] })
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 })
await ctx.addInitScript(OVERLAY)
const page = await ctx.newPage()
const cdp = await ctx.newCDPSession(page)
const frames = []
let n = 0
cdp.on('Page.screencastFrame', async ({ data, sessionId }) => {
  const f = `${OUT}/frames/${String(n++).padStart(6, '0')}.jpg`
  fs.writeFileSync(f, Buffer.from(data, 'base64'))
  frames.push({ f, t: Date.now() / 1000 })
  cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
})
const fastRanges = []
const marks = []  // when each caption or card appears, for timing the narration
const log = (s) => console.log(new Date().toISOString().slice(11, 19), s)

// ---------------------------------------------------------------- helpers
const hold = (ms) => page.waitForTimeout(ms)
let mouse = { x: 960, y: 540 }
async function glide(x, y, steps = 28) {
  await page.mouse.move(x, y, { steps }); mouse = { x, y }
}
async function box(loc) {
  await loc.scrollIntoViewIfNeeded().catch(() => {})
  await hold(250)
  return loc.boundingBox()
}
async function ring(loc, tag) {
  const b = loc && await box(loc)
  await page.evaluate(([r, t]) => window.__ov.ring(r, t), [b, tag])
  return b
}
async function point(loc, tag) {
  const b = await ring(loc, tag)
  if (b) await glide(b.x + b.width / 2, b.y + b.height / 2)
  return b
}
async function click(loc, tag, after = 900) {
  const b = await point(loc, tag)
  await hold(350)
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2)
  await hold(after)
}
const unring = () => page.evaluate(() => window.__ov.ring(null))
let chapter = ''
const TOTAL = 12
const cap = (step, text, fast = false) => (marks.push({ t: Date.now() / 1000, kind: 'caption', step, chapter, text, fast }), page.evaluate(([n, h, t, f]) => window.__ov.cap(n, h, t, f), [`${step} / ${TOTAL}`, chapter, text, fast]))
const card = (html) => (marks.push({ t: Date.now() / 1000, kind: html ? 'card' : 'card-off', text: html ? html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() : '' }), page.evaluate((h) => window.__ov.card(h), html))
async function fast(fn) {
  const a = Date.now() / 1000
  try { await fn() } finally { fastRanges.push([a, Date.now() / 1000]) }
}
async function ready() {
  await page.waitForFunction(() => !document.querySelector('.mt-right .spin, .in-ask .spin, main .spin'), null, { timeout: 120000 }).catch(() => {})
}
const api = (p, opts) => page.evaluate(async ([p, o]) => (await fetch('/api' + p, o)).json(), [p, opts])

// ---------------------------------------------------------------- the walkthrough
await page.goto(BASE)
await page.waitForSelector('text=Explore the demo workspace')
await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 })
for (let i = 0; i < 50 && !frames.length; i++) { await page.mouse.move(900 + i, 500); await hold(100) }
await hold(300)

// 0 · title card
await card(`<div class="eyebrow">Product walkthrough</div><h1>Sounding: an AI meeting notetaker, built like Fathom</h1>
  <p>Record → speaker-labelled transcript → notes, action items, clips and search. For one person, and for a team.</p>
  <ul><li>Open models on one GPU: Parakeet (speech), Sortformer + TitaNet (who spoke), Qwen (notes)</li>
  <li>The capture layer is stubbed on purpose: you upload the recording instead of a bot joining the call</li>
  <li>Seeded with real recorded meetings, including hour-long calls with 8–9 people</li></ul>`)
await hold(9000)
await card(null); await hold(600)

// 1 · reviewer entry
chapter = 'Getting in'
await cap(1, 'Anyone can open the live app. Reviewers get in with one click, with no account to create.')
await hold(1500)
await click(page.getByRole('button', { name: 'Explore the demo workspace' }), 'One-click demo', 400)
await page.waitForSelector('.mrow h3'); await unring(); await hold(1200)

// 2 · meetings list
chapter = 'My calls'
await cap(2, 'The meetings list. Every title, meeting type, summary line and action-item count was produced by the pipeline from real recordings.')
await ring(page.locator('.mrow').first(), 'Named and summarised automatically'); await hold(5000)
await ring(page.locator('.mrow .faces').first(), 'Who spoke'); await hold(2500)
await ring(page.locator('.mrow .pill').first(), 'Open action items'); await hold(2500)
await ring(page.locator('.rail'), 'My calls, Team calls, Shared, Ask, Folders, Playlists…'); await hold(3500)
await unring()

// 3 · the 8-person meeting
chapter = 'The hard case: 1 hour, 8 people'
await cap(3, 'The case that matters: a real hour-long research meeting with eight people (ICSI corpus).')
await click(page.locator('.mrow h3', { hasText: BIG }), null, 400)
await page.waitForSelector('.mt-line'); await hold(1500)
await ring(page.locator('.mt-legend'), '7 voices found');
await cap(3, 'The first speaker model could only track 4 people. I measured against human labels and added voice-embedding clustering: speaker mix-ups fell from ~20% to ~6%.')
await hold(7000)
await ring(page.locator('.mt-strip'), 'Who talked when, across the hour'); await hold(4000)
await cap(3, 'Press play and the transcript follows. Click any line to jump the recording to that moment.')
await click(page.locator('.mt-play'), 'Play', 1500)
await click(page.locator('.mt-line .stamp').nth(14), 'Jump to this line', 3500)
await ring(page.locator('.mt-transcript-wrap'), 'Transcript follows playback'); await hold(3000)
await unring()

// 4 · notes with citations
chapter = 'Notes you can check'
await cap(4, 'The notes. An hour is too long to read, so every point links to the moment it came from. Invented timestamps are thrown away.')
await ring(page.locator('.mt-panel'), 'AI notes (Project sync template)'); await hold(4500)
const stamp = page.locator('.mt-panel button').filter({ hasText: /^\d+:\d{2}$/ }).nth(2)
await click(stamp, 'Click → plays that moment', 3500)
await unring()

// 5 · templates + action items
chapter = 'Templates and action items'
await cap(5, 'Switch the template and the notes are rewritten for that kind of meeting: here, a sales discovery (SPICED) layout.')
const tpl = page.locator('select').filter({ has: page.locator('option', { hasText: 'Sales discovery (SPICED)' }) }).first()
await point(tpl, 'Notes template'); await hold(600)
await tpl.selectOption({ label: 'Sales discovery (SPICED)' }); await hold(1800)
await cap(5, 'While Qwen rewrites the notes in the background, the action items: each one has the speaker, the moment, and an owner you can assign.')
await click(page.getByRole('tab', { name: /Action items/ }), 'Action items', 1200)
const assignee = page.locator('.mt-panel select').first()
await point(assignee, 'Assign a teammate'); await hold(500)
const opts = await assignee.locator('option').allTextContents()
const who = opts.find((o) => /Hamza/.test(o)) || opts[1]
if (who) await assignee.selectOption({ label: who })
await cap(5, 'Assigning someone sends them a notification with a link to the moment.'); await hold(3500)
await unring()

// 6 · ask this meeting
chapter = 'Ask about this meeting'
await cap(6, 'Ask a question about this meeting. The answer cites the moments it used.')
await click(page.getByRole('tab', { name: 'Ask', exact: true }), 'Ask', 800)
const askBox = page.getByPlaceholder('Ask about this meeting')
await click(askBox, null, 300)
await page.keyboard.type('What did they decide about collecting more data?', { delay: 28 }); await hold(500)
await page.keyboard.press('Enter')
await cap(6, 'Qwen reads the whole hour-long transcript (32k-token context)…', true)
await fast(async () => { await hold(1500); await ready() })
await cap(6, 'Timestamps in the answer are buttons that jump the recording there.')
await ring(page.locator('.mt-qa').last(), 'Answer with citations'); await hold(5500)
await unring()

// 7 · highlight + clip
chapter = 'Highlight a moment'
await cap(7, 'Hear something worth keeping? Highlight saves the last 20 seconds and the next 10, because by the time you react, the moment has passed.')
await click(page.getByRole('button', { name: 'Highlight' }), 'Highlight', 1200)
const clipTitle = page.getByLabel('Clip title')
await click(clipTitle, null, 200)
await clipTitle.fill(''); await page.keyboard.type('Plan for the next round of data collection', { delay: 22 }); await hold(600)
await click(page.getByRole('button', { name: 'Save clip' }), 'Save clip', 1500)
await cap(7, 'You can also clip any range from the transcript: line menu → "Clip from here" → "End clip here".')
await click(page.getByRole('tab', { name: /Clips/ }), 'Clips', 1500)
await ring(page.locator('.mt-panel'), 'Clips from this meeting'); await hold(3500)
await cap(7, 'Share the clip with someone who was not on the call: one public link, for that clip only.')
await click(page.getByRole('button', { name: /Clip actions for Plan for the next round/ }).first(), 'Clip menu', 900)
await click(page.getByRole('menuitem', { name: 'Copy public link' }), 'Copy public link', 1500)
await unring()
const mid = await page.evaluate(() => location.hash.split('/')[2].split('?')[0])
const links = await api(`/meetings/${mid}/links`)
const token = (links.find((l) => l.highlight_id && l.access === 'public') || links[0]).token

// 8 · open the clip logged out
chapter = 'Shared clip, no account needed'
await cap(8, 'To prove it, log out completely…')
await click(page.getByRole('button', { name: 'Account' }), 'Account menu', 700)
await click(page.getByRole('menuitem', { name: 'Log out' }), 'Log out', 1500)
await cap(8, '…and open the link. The clip plays with its own transcript. The rest of the recording is not exposed.')
await page.evaluate((t) => { location.hash = '#/s/' + t }, token)
await page.waitForSelector('.public h1'); await hold(800)
await ring(page.locator('.public'), 'Logged out: public clip page')
await page.evaluate(() => document.querySelector('audio,video')?.play()); await hold(6500)
await page.evaluate(() => document.querySelector('audio,video')?.pause())
await unring()
await page.evaluate(() => { location.hash = '#/' })
await page.waitForSelector('text=Explore the demo workspace'); await hold(600)
await click(page.getByRole('button', { name: 'Explore the demo workspace' }), 'Back in', 400)
await page.waitForSelector('.mrow h3'); await unring(); await hold(800)

// 9 · across meetings
chapter = 'Across all meetings'
await cap(9, 'Search runs across every transcript and every set of notes you can see, and results open at the exact moment.')
const gs = page.locator('#gsearch')
await click(gs, 'Search everything', 200)
await page.keyboard.type('battery', { delay: 60 }); await page.keyboard.press('Enter')
await page.waitForSelector('main mark'); await hold(1200)
await ring(page.locator('main'), '3 meetings mention "battery"'); await hold(5000)
await cap(9, 'Ask Sounding answers across all your calls, citing the meeting and the moment.')
await click(page.getByRole('link', { name: 'Ask Sounding', exact: true }), 'Ask Sounding', 900)
const q = page.locator('main textarea, main input[type=text], main input:not([type])').first()
await click(q, null, 200)
await page.keyboard.type('What deadlines or dates came up across these meetings?', { delay: 24 }); await page.keyboard.press('Enter')
await cap(9, 'Keyword retrieval (BM25) picks the relevant excerpts from 9 meetings, then Qwen answers…', true)
await fast(async () => { await hold(1500); await ready(); await hold(500) })
await cap(9, 'Each citation links to the meeting and the moment.')
await ring(page.locator('main .in-qa, main [class*=qa]').last(), 'Answer from 9 meetings'); await hold(6000)
await unring()

// 10 · team intelligence
chapter = 'Team intelligence'
await cap(10, 'Trackers flag moments across every call. Keyword trackers match words; AI trackers match meaning, so "we need it by Friday" counts as a deadline.')
await click(page.getByRole('link', { name: 'Trackers', exact: true }), 'Trackers', 1500)
await ring(page.locator('main'), 'Keyword and AI trackers'); await hold(3500)
await click(page.getByText('Deadlines and dates').first(), 'AI tracker', 1800)
await ring(page.locator('main'), 'Mentions per week, each linked to its moment'); await hold(5000)
await cap(10, 'Coaching: talk time, questions and monologues per person, plus AI scorecards for each meeting type, with evidence.')
await click(page.getByRole('link', { name: 'Coaching', exact: true }), 'Coaching', 1200)
await click(page.getByRole('tab', { name: 'Scorecards' }), 'Scorecards', 1800)
await ring(page.locator('main'), 'AI scorecards: averages, per-criterion scores, top performers'); await hold(5500)
await cap(10, 'Playlists collect clips across meetings. "Play all" runs them back to back, which is useful for onboarding or a decision review.')
await click(page.getByRole('link', { name: 'Playlists', exact: true }), 'Playlists', 1200)
await click(page.getByText('Decisions worth rewatching').first(), null, 1500)
const playAll = page.getByRole('button', { name: 'Play all' })
if (await playAll.count()) await click(playAll, 'Play all', 4500)
await cap(10, 'Companies are grouped from attendee email domains. Folders and sharing controls (private, team, organization, links) complete the team side.')
await click(page.getByRole('link', { name: 'Companies', exact: true }), 'Companies', 1500)
await ring(page.locator('main'), 'Calls grouped by company'); await hold(4000)
await unring()

// 11 · upload + admin while it processes
chapter = 'Upload a recording'
await cap(11, 'New recordings come in by upload. This is the stub where Fathom\'s bot would join the call. The pipeline after it is the same.')
await click(page.getByRole('button', { name: 'Upload recording' }), 'Upload recording', 1000)
await page.locator('input[type=file]').setInputFiles(SAMPLE); await hold(800)
await ring(page.locator('.drop'), '2-minute meeting recording'); await hold(2500)
await ring(page.locator('.modal'), 'Visibility, meeting type and template (auto-detected)'); await hold(3500)
await click(page.getByRole('button', { name: 'Upload and transcribe' }), 'Upload and transcribe', 2500)
await cap(11, 'Processing: preparing audio → finding speakers → transcribing → writing notes → running trackers.')
await ring(page.locator('main'), 'Live processing status'); await hold(4000)
await unring()
const upId = await page.evaluate(() => location.hash.split('/')[2])
chapter = 'Admin, while it processes'
await cap(11, 'Meanwhile, the admin side: teams and roles, invite links, meeting types (detected automatically), custom templates, scorecards and a transcription dictionary.')
await click(page.getByRole('button', { name: 'Account' }), 'Account menu', 600)
await click(page.getByRole('menuitem', { name: 'Organization settings' }), 'Organization settings', 1500)
for (const [name, tag] of [['Teams', 'Teams and leads'], ['Meeting types', 'Auto-detected meeting types'], ['Scorecards', 'Scorecard criteria'], ['Dictionary', 'Vocabulary the transcripts should get right']]) {
  const l = page.locator('.in-subnav a', { hasText: name }).first()
  if (await l.count()) { await click(l, tag, 2600) }
}
await unring()
await cap(11, 'Back to the upload…', true)
await fast(async () => {
  await page.evaluate((id) => { location.hash = '#/meeting/' + id }, upId)
  await page.waitForSelector('.mt-line', { timeout: 240000 })
  await hold(1500)
})
chapter = 'Upload a recording'
await cap(11, 'Done. The meeting was titled, split by speaker, transcribed and summarised in about a minute.')
await ring(page.locator('.mt-panel'), 'Fresh notes with timestamps'); await hold(6000)
await unring()

// 12 · close
chapter = 'Wrap-up'
await page.evaluate(() => window.__ov.cap())
await card(`<div class="eyebrow">What's built, and what I left out</div><h1>Built for the hour-long, eight-person call</h1>
  <ul><li>Built: transcripts, cited notes, templates, action items, clips and playlists, sharing and links, comments, search, Ask across calls, trackers, coaching, scorecards, teams and admin, API and webhooks</li>
  <li>Stubbed: calendar sync and the meeting bot (uploads go through the same pipeline)</li>
  <li>Left out: email, CRM sync, SSO. Next: a Google Meet bot, then live notes during the call</li>
  <li>Code and agent logs are in the repo · live at http://5.9.117.80:3000</li></ul>`)
await hold(10000)

await cdp.send('Page.stopScreencast')
marks.push({ t: Date.now() / 1000, kind: 'end' })
fs.writeFileSync(`${OUT}/timeline.json`, JSON.stringify({ frames, fastRanges, marks }, null, 0))
log(`frames ${frames.length}, fast ranges ${fastRanges.length}`)
await browser.close()
