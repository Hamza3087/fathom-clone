// Records the walkthrough paced to a recorded narration. A real Chrome window runs on a virtual display
// (Xvfb) and ffmpeg captures it at a steady 30 fps, so motion is smooth. Each section stays on screen for
// as long as its narration block (plan.json from narration_plan.py); AI waits are marked so the build
// step can fast-forward them. build_paced.py then lays the voice over the result at natural speed.
//
//   node record_paced.mjs        env: BASE, OUT, SAMPLE (mp3 to upload), PLAN (plan.json), CHROME
import { chromium } from 'playwright-core'
import { spawn } from 'node:child_process'
import fs from 'node:fs'

const BASE = process.env.BASE || 'https://static.80.117.9.5.clients.your-server.de/'
const DRY = !!process.env.DRY  // run the whole flow without capturing or pacing, to find slow steps
const OUT = process.env.OUT
const SAMPLE = process.env.SAMPLE
const BIG = 'Research Progress and Meeting Overlap Analysis'
fs.mkdirSync(OUT, { recursive: true })
const PLAN = JSON.parse(fs.readFileSync(process.env.PLAN))
const FF = 5  // speed-up applied to AI wait time in the build step
const focus = []  // highlight rings over time: {t, rect|null, tag}

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
      #__ov .cap{position:absolute;left:50%;bottom:28px;transform:translate(-50%,6px);width:min(1240px,90vw);background:rgba(10,24,28,.92);color:#fff;border-radius:16px;padding:18px 26px 20px;box-shadow:0 10px 40px rgba(0,0,0,.35);opacity:0;transition:opacity .22s ease,transform .22s ease}
      #__ov .flash{position:absolute;inset:0;background:#ff00ff}
      #__ov .cap.on{opacity:1;transform:translate(-50%,0)}
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
      #__ov .card li{margin:6px 0;opacity:0;animation:__in .5s ease-out forwards}
      #__ov .card li:nth-child(2){animation-delay:.25s}#__ov .card li:nth-child(3){animation-delay:.5s}#__ov .card li:nth-child(4){animation-delay:.75s}
      @keyframes __in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
      #__ov.carding .cur{opacity:0}`
    document.head.appendChild(st)
    const root = document.createElement('div'); root.id = '__ov'
    root.innerHTML = `<div class="ring"><span class="tag"></span></div><div class="cap"><div class="k"><span class="n"></span><span class="h"></span><span class="ff">Sped up</span></div><div class="t"></div></div><div class="card"><div class="in"></div></div>
      <svg class="cur" viewBox="0 0 24 24"><path d="M4 2l15 11.2-6.6 1 3.9 7.3-2.9 1.5-3.8-7.4L4 20z" fill="#fff" stroke="#0a181c" stroke-width="1.4" stroke-linejoin="round"/></svg>`
    document.documentElement.appendChild(root)
    const cur = root.querySelector('.cur')
    const s = window.__ovState || { x: 960, y: 540 }
    cur.style.left = s.x + 'px'; cur.style.top = s.y + 'px'
    addEventListener('hashchange', () => {
      const r = document.querySelector('#__ov .ring')
      if (r && r.classList.contains('on')) { r.classList.remove('on'); window.__ovLog && window.__ovLog('nav-clear') }
    })
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
      const set = () => {
        c.querySelector('.n').textContent = n; c.querySelector('.h').textContent = h; c.querySelector('.t').textContent = text
        c.classList.toggle('fast', !!fast); c.classList.add('on')
      }
      if (c.classList.contains('on')) { c.classList.remove('on'); setTimeout(set, 230) } else set()
    },
    flash(on) {
      install(); let f = document.querySelector('#__ov .flash')
      if (on && !f) { f = document.createElement('div'); f.className = 'flash'; document.getElementById('__ov').appendChild(f) }
      if (!on && f) f.remove()
    },
    card(html) {
      install(); const c = document.querySelector('#__ov .card')
      document.getElementById('__ov').classList.toggle('carding', !!html)
      if (!html) { c.classList.remove('on'); return }
      c.querySelector('.in').innerHTML = html; c.classList.add('on')
    },
  }
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', install); else install()
}

// ---------------------------------------------------------------- browser on a virtual display, captured by ffmpeg
const DISPLAY = process.env.DISPLAY_NUM || ':97'
const xvfb = spawn('Xvfb', [DISPLAY, '-screen', '0', '1920x1080x24', '-nolisten', 'tcp', '-extension', 'GLX'], { stdio: 'ignore' })
await new Promise((r) => setTimeout(r, 1500))
const browser = await chromium.launch({
  executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: false, env: { ...process.env, DISPLAY },
  ignoreDefaultArgs: ['--enable-automation'],
  args: ['--no-sandbox', '--kiosk', '--window-position=0,0', '--window-size=1920,1080', '--force-device-scale-factor=1',
         '--autoplay-policy=no-user-gesture-required', '--hide-scrollbars', '--disable-features=Translate,InfiniteSessionRestore',
         '--no-first-run', '--no-default-browser-check', '--password-store=basic'],
})
const ctx = await browser.newContext({ viewport: null })
await ctx.addInitScript(OVERLAY)
const page = ctx.pages()[0] || await ctx.newPage()
{
  // true fullscreen: no tab strip or address bar in the recording
  const s = await ctx.newCDPSession(page)
  const { windowId } = await s.send('Browser.getWindowForTarget')
  await s.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'fullscreen' } })
  await new Promise((r) => setTimeout(r, 800))
}
await page.exposeFunction('__ovLog', () => { focus.push({ t: Date.now() / 1000, rect: null, tag: null }) })
let capture = null
function startCapture() {
  if (DRY) return
  capture = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '30', '-video_size', '1920x1080',
    '-i', `${DISPLAY}.0`, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', `${OUT}/capture.mp4`],
    { stdio: ['pipe', 'ignore', 'inherit'] })
}
async function stopCapture() {
  if (DRY) return
  capture.stdin.write('q'); await new Promise((r) => capture.on('close', r))
}
const fastRanges = []
const marks = []  // when each caption or card appears, for timing the narration
const log = (s) => console.log(new Date().toISOString().slice(11, 19), s)

// ---------------------------------------------------------------- helpers
const hold = (ms) => page.waitForTimeout(ms)
let mouse = { x: 960, y: 540 }
async function glide(x, y) {
  // ease-in-out, ~60 moves per second, duration grows with distance, like a hand on a trackpad
  const from = { ...mouse }, d = Math.hypot(x - from.x, y - from.y)
  const ms = Math.min(1100, 320 + d * 0.5), steps = Math.max(10, Math.round(ms / 16))
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
    await page.mouse.move(from.x + (x - from.x) * e, from.y + (y - from.y) * e)
    await page.waitForTimeout(16)
  }
  mouse = { x, y }
}
async function rectOf(loc) {
  // the element plus its visible children (avatar stacks use negative margins), clipped to the viewport
  return loc.evaluate((el) => {
    const rs = [el.getBoundingClientRect(), ...[...el.children].map((c) => c.getBoundingClientRect())].filter((r) => r.width && r.height)
    const x0 = Math.max(4, Math.min(...rs.map((r) => r.left))), y0 = Math.max(4, Math.min(...rs.map((r) => r.top)))
    const x1 = Math.min(innerWidth - 4, Math.max(...rs.map((r) => r.right))), y1 = Math.min(innerHeight - 4, Math.max(...rs.map((r) => r.bottom)))
    return { x: x0, y: y0, width: Math.max(0, x1 - x0), height: Math.max(0, y1 - y0) }
  }, null, { timeout: 2500 })
}
async function stableRect(loc) {
  // wait until layout stops moving (answers streaming in, dialogs opening) before drawing the ring
  let a = await rectOf(loc)
  for (let i = 0; i < 10; i++) {
    await hold(120)
    const b = await rectOf(loc)
    if (Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.width - b.width) + Math.abs(a.height - b.height) < 2) return b
    a = b
  }
  return a
}
async function box(loc) {
  // scroll smoothly only when the target is hidden or under the caption bar
  const visible = await loc.evaluate((el) => { const r = el.getBoundingClientRect(); return r.top >= 64 && r.bottom <= innerHeight - 170 }, null, { timeout: 2500 }).catch(() => true)
  if (!visible) {
    await loc.evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' }), null, { timeout: 2500 }).catch(() => {})
    await hold(800)
  }
  return stableRect(loc)
}
async function ring(loc, tag) {
  const b = loc && await box(loc)
  focus.push({ t: Date.now() / 1000, rect: b, tag: tag || null })
  await page.evaluate(([r, t]) => window.__ov.ring(r, t), [b, tag])
  return b
}
async function point(loc, tag) {
  const b = await ring(loc, tag)
  if (b) await glide(b.x + b.width / 2, b.y + b.height / 2)
  return b
}
async function moveTo(loc) {
  const r = await rectOf(loc).catch(() => null)
  if (r) await glide(r.x + r.width / 2, r.y + r.height / 2)
}
async function click(loc, tag, after = 900) {
  const b = await point(loc, tag)
  await hold(350)
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2)
  await hold(Math.min(after, 450))
  const still = await loc.evaluate((el) => {
    const r = el.getBoundingClientRect(); if (!r.width) return false
    const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return !!e && (el === e || el.contains(e))
  }, null, { timeout: 800 }).catch(() => false)
  if (!still) await unring()
  if (after > 450) await hold(after - 450)
}
const unring = () => (focus.push({ t: Date.now() / 1000, rect: null, tag: null }), page.evaluate(() => window.__ov.ring(null)))
let chapter = ''
const TOTAL = 14
const SHOW_CAPTIONS = process.env.CAPTIONS !== 'off'
const cap = (step, text, fast = false) => (marks.push({ t: Date.now() / 1000, kind: 'caption', step, chapter, text, fast }), !SHOW_CAPTIONS ? Promise.resolve() : page.evaluate(([n, h, t, f]) => window.__ov.cap(n, h, t, f), [`${step} / ${TOTAL}`, chapter, text, fast]))
const card = (html) => (marks.push({ t: Date.now() / 1000, kind: html ? 'card' : 'card-off', text: html ? html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() : '' }), page.evaluate((h) => window.__ov.card(h), html))
async function fast(fn, k = FF) {
  const a = Date.now() / 1000
  try { await fn() } finally { const z = Date.now() / 1000; fastRanges.push([a, z, k]); if (cur) cur.ff += (z - a) * (1 - 1 / k) }
}
const blocks = []
let cur = null
const LEAD = (n) => (n === 1 ? 1.0 : 0.45)
let blockT0 = Date.now()
async function settle() {
  if (DRY) return
  // keep the current section on screen until its narration (plus a breath) would have finished
  const need = PLAN[cur.n - 1].dur + LEAD(cur.n) + 0.7
  for (;;) {
    const eff = (Date.now() / 1000 - cur.t) - cur.ff
    if (eff >= need) break
    await hold(Math.min(250, (need - eff) * 1000))
  }
}
async function block(n) {
  if (cur) await settle()
  if (DRY && cur) log(`  block ${cur.n} took ${((Date.now() - blockT0) / 1000).toFixed(1)}s (voice ${PLAN[cur.n - 1].dur}s)`)
  blockT0 = Date.now()
  cur = { n, t: Date.now() / 1000, ff: 0 }
  blocks.push(cur)
  log(`block ${n}`)
}
async function ready() {
  await page.waitForFunction(() => !document.querySelector('.mt-right .spin, .in-ask .spin, main .spin'), null, { timeout: 120000 }).catch(() => {})
}
const api = (p, opts) => page.evaluate(async ([p, o]) => (await fetch('/api' + p, o)).json(), [p, opts])

// ---------------------------------------------------------------- the walkthrough
await page.goto(BASE)
await page.waitForSelector('.mrow h3', { timeout: 30000 })
log(`window ${await page.evaluate(() => innerWidth + 'x' + innerHeight)}`)
startCapture(); await hold(1500)
await page.evaluate(() => window.__ov.flash(true)); const flashT = Date.now() / 1000; await hold(500)
await page.evaluate(() => window.__ov.flash(false)); await hold(400)
if (process.env.SMOKE) { await hold(1500); await stopCapture(); await browser.close(); xvfb.kill(); fs.writeFileSync(`${OUT}/timeline.json`, JSON.stringify({ flashT })); process.exit(0) }

// 0 · title card
await block(1)
await card(`<div class="eyebrow">Product walkthrough</div><h1>Sounding: an AI meeting notetaker, built like Fathom</h1>
  <p>Record → speaker-labeled transcript → notes, action items, clips and search. For one person, and for a team.</p>
  <ul><li>Open models on one GPU: Parakeet (speech), Sortformer + TitaNet (who spoke), Qwen (notes)</li>
  <li>The capture layer is stubbed on purpose: you upload the recording instead of a bot joining the call</li>
  <li>Seeded with real recorded meetings, including hour-long calls with 8–9 people</li></ul>`)
await hold(9000)
await card(null); await hold(600)

// 1 · reviewer entry
await block(2)
chapter = 'Getting in'
await cap(1, 'Anyone can open the live app: it goes straight into a demo workspace, no sign-up, no login.')
await hold(800)
await ring(page.locator('.demo-banner'), 'No sign-up, no login'); await hold(2600)
await unring(); await hold(500)

// 2 · meetings list
await block(3)
chapter = 'My calls'
await cap(2, 'The meetings list. Every title, meeting type, summary line and action-item count was produced by the pipeline from real recordings.')
await ring(page.locator('.mrow').first(), 'Named and summarised automatically'); await hold(5000)
await ring(page.locator('.mrow .faces').first(), 'Who spoke'); await hold(2500)
await ring(page.locator('.mrow .pill').first(), 'Open action items'); await hold(2500)
await ring(page.locator('.rail'), 'My calls, Team calls, Shared, Ask, Folders, Playlists…'); await hold(3500)
await unring()

// 3 · the 8-person meeting
await block(4)
chapter = 'The hard case: 1 hour, 8 people'
await cap(3, 'The case that matters: a real hour-long research meeting with eight people (ICSI corpus).')
await click(page.locator('.mrow h3', { hasText: BIG }), null, 400)
await page.waitForSelector('.mt-line'); await hold(1500)
await ring(page.locator('.mt-legend'), '7 voices found');
await cap(3, 'The first speaker model could only track 4 people. I measured against human labels and added voice-embedding clustering: speaker mix-ups fell from ~20% to ~6%.')
await hold(7000)
await ring(page.locator('.mt-strip'), 'Who talked when, across the hour'); await hold(4000)
await block(5)
await cap(3, 'Press play and the transcript follows. Click any line to jump the recording to that moment.')
await click(page.locator('.mt-play'), 'Play', 1500)
const far = page.locator('.mt-line .stamp').nth(90)
const farT = (await far.textContent()).trim()
await click(far, `Jump to ${farT}`, 1200)
await ring(page.locator('.mt-time'), `Player jumped to ${farT}`); await hold(2600)
await ring(page.locator('.mt-transcript-wrap'), 'Transcript follows playback'); await hold(3000)
await unring()

// 4 · notes with citations
await block(6)
chapter = 'Notes you can check'
await cap(4, 'The notes. An hour is too long to read, so every point links to the moment it came from. Invented timestamps are thrown away.')
await ring(page.locator('.mt-panel'), 'AI notes (Project sync template)'); await hold(4500)
const stamp = page.locator('.mt-panel button').filter({ hasText: /^\d+:\d{2}$/ }).nth(2)
await click(stamp, 'Click → plays that moment', 3500)
await unring()

// 5 · templates + action items
await block(7)
chapter = 'Templates and action items'
await cap(5, 'Switch the template and the notes are rewritten for that kind of meeting: here, a sales discovery (SPICED) layout.')
const tpl = page.locator('select').filter({ has: page.locator('option', { hasText: 'Sales discovery (SPICED)' }) }).first()
await point(tpl, 'Notes template'); await hold(600)
await tpl.selectOption({ label: 'Sales discovery (SPICED)' }); await hold(900)
await fast(async () => {
  await page.waitForFunction(() => !document.querySelector('.mt-right .spin'), null, { timeout: 120000 }).catch(() => {})
  await page.waitForFunction(() => /Situation|Pain/.test(document.querySelector('.mt-panel')?.innerText || ''), null, { timeout: 120000 }).catch(() => {})
}, 12)
await ring(page.locator('.mt-panel'), 'Rewritten as a sales call: Situation, Pain, Impact…'); await hold(2600)
await cap(5, 'Action items: each one has the speaker, the moment, and an owner you can assign.')
await click(page.getByRole('tab', { name: /Action items/ }), 'Action items', 1200)
const assignee = page.locator('.mt-panel select').first()
await point(assignee, 'Assign a teammate'); await hold(500)
const opts = await assignee.locator('option').allTextContents()
const who = opts.find((o) => /Hamza/.test(o)) || opts[1]
if (who) await assignee.selectOption({ label: who })
await cap(5, 'Assigning someone sends them a notification with a link to the moment.'); await hold(3500)
await unring()

// 6 · ask this meeting
await block(8)
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
await ring(page.locator('.mt-qa').last(), 'Answer with timestamps'); await hold(4200)
await unring()

// 7 · highlight + clip
await block(9)
chapter = 'Highlight a moment'
await cap(7, 'Hear something worth keeping? Highlight saves the last 20 seconds and the next 10, because by the time you react, the moment has passed.')
await click(page.getByRole('button', { name: 'Highlight' }), 'Highlight', 800)
const clipTitle = page.getByLabel('Clip title')
await click(clipTitle, null, 200)
await clipTitle.fill(''); await page.keyboard.type('Plan for the next round of data collection', { delay: 12 }); await hold(300)
await click(page.getByRole('button', { name: 'Save clip' }), 'Save clip', 900)
await cap(7, 'You can also clip any range from the transcript: line menu → "Clip from here" → "End clip here".')
const vis = await page.evaluate(() => {
  const wrap = document.querySelector('.mt-transcript-wrap') || document.querySelector('.transcript')
  const box = wrap.getBoundingClientRect()
  return [...document.querySelectorAll('.mt-line')].map((l, i) => [i, l.getBoundingClientRect()])
    .filter(([, r]) => r.top > box.top + 30 && r.bottom < Math.min(box.bottom, innerHeight - 180)).map(([i]) => i)
})
if (vis.length >= 4) {
  const lineMenu = (i) => page.locator('.mt-line').nth(i).locator('.mt-line-menu button')
  await moveTo(page.locator('.mt-line').nth(vis[1])); await hold(300)
  await click(lineMenu(vis[1]), 'Line menu', 350)
  await click(page.getByRole('menuitem', { name: 'Clip from here' }), 'Clip from here', 600)
  await moveTo(page.locator('.mt-line').nth(vis[3])); await hold(300)
  await click(lineMenu(vis[3]), null, 350)
  await click(page.getByRole('menuitem', { name: 'End clip here' }), 'End clip here', 700)
  const t2 = page.getByLabel('Clip title')
  if (await t2.count()) {
    await t2.fill(''); await page.keyboard.type('How often people overlap', { delay: 12 }); await hold(250)
    await click(page.getByRole('button', { name: 'Save clip' }), 'Save clip', 700)
  }
}
await unring()
await click(page.getByRole('tab', { name: /Clips/ }), 'Clips', 800)
await ring(page.locator('.mt-panel'), 'Clips from this meeting'); await hold(1500)
await cap(7, 'Share the clip with someone who was not on the call: one public link, for that clip only.')
await click(page.getByRole('button', { name: /Clip actions for Plan for the next round/ }).first(), 'Clip menu', 600)
await click(page.getByRole('menuitem', { name: 'Copy public link' }), 'Copy public link', 1000)
await unring()
const mid = await page.evaluate(() => location.hash.split('/')[2].split('?')[0])
const links = await api(`/meetings/${mid}/links`)
const token = (links.find((l) => l.highlight_id && l.access === 'public') || links[0]).token

// 8 · open the clip logged out
await block(10)
chapter = 'Shared clip, no account needed'
await cap(8, 'The link opened with no session at all: the clip plays with its own transcript; the rest of the recording is not exposed.')
await ctx.clearCookies()
await page.evaluate((t) => { location.hash = '#/s/' + t }, token)
await page.waitForSelector('.public h1'); await hold(800)
await ring(page.locator('.public'), 'No session: public clip page')
await page.evaluate(() => document.querySelector('audio,video')?.play()); await hold(4200)
await page.evaluate(() => document.querySelector('audio,video')?.pause())
await unring()
await unring()
await page.goto(BASE)
await page.waitForSelector('.mrow h3', { timeout: 30000 }); await hold(700)

// 9 · across meetings
await block(11)
chapter = 'Across all meetings'
await cap(9, 'Search runs across every transcript and every set of notes you can see, and results open at the exact moment.')
const gs = page.locator('#gsearch')
await click(gs, 'Search everything', 200)
await page.keyboard.type('battery', { delay: 60 }); await page.keyboard.press('Enter')
await page.waitForSelector('main mark'); await hold(1200)
await ring(page.locator('main'), '3 meetings mention "battery"'); await hold(4200)
await page.fill('#gsearch', '')
await cap(9, 'Ask Sounding answers across all your calls, citing the meeting and the moment.')
await click(page.getByRole('link', { name: 'Ask Sounding', exact: true }), 'Ask Sounding', 900)
const q = page.locator('main textarea, main input[type=text], main input:not([type])').first()
await click(q, null, 200)
await page.keyboard.type('What decisions were made across these meetings, and who owns the next steps?', { delay: 22 }); await page.keyboard.press('Enter')
await cap(9, 'Keyword retrieval (BM25) picks the relevant excerpts from 9 meetings, then Qwen answers…', true)
await fast(async () => { await hold(1500); await ready(); await hold(500) })
await cap(9, 'Each citation links to the meeting and the moment.')
await ring(page.locator('main .in-qa, main [class*=qa]').last(), 'Answer citing 9 meetings'); await hold(4500)
await unring()

// 10 · team intelligence
await block(12)
chapter = 'Team intelligence'
await cap(10, 'Trackers flag moments across every call. Keyword trackers match words; AI trackers match meaning, so "we need it by Friday" counts as a deadline.')
await click(page.getByRole('link', { name: 'Trackers', exact: true }), 'Trackers', 1500)
await ring(page.locator('main'), 'Keyword and AI trackers'); await hold(3500)
await click(page.getByText('Deadlines and dates').first(), 'AI tracker', 1800)
await ring(page.locator('main'), 'Mentions per week, each linked to its moment'); await hold(5000)
await block(13)
await cap(10, 'Coaching: talk time, questions and monologues per person, plus AI scorecards for each meeting type, with evidence.')
await click(page.getByRole('link', { name: 'Coaching', exact: true }), 'Coaching', 1200)
await page.waitForSelector('main table', { timeout: 20000 }).catch(() => {})
await hold(600)
await ring(page.locator('main table').first(), 'Talk time, questions, monologues per person'); await hold(3200)
await click(page.getByRole('tab', { name: 'Scorecards' }), 'Scorecards', 1800)
await ring(page.locator('main'), 'AI scorecards: averages, per-criterion scores, top performers'); await hold(3200)
await block(14)
await cap(10, 'Playlists collect clips across meetings. "Play all" runs them back to back, which is useful for onboarding or a decision review.')
await click(page.getByRole('link', { name: 'Playlists', exact: true }), 'Playlists', 1200)
await click(page.getByText('Decisions worth rewatching').first(), null, 1500)
const playAll = page.getByRole('button', { name: 'Play all' })
if (await playAll.count()) await click(playAll, 'Play all', 2600)
await cap(10, 'Companies are grouped from attendee email domains. Folders and sharing controls (private, team, organization, links) complete the team side.')
await click(page.getByRole('link', { name: 'Companies', exact: true }), 'Companies', 1500)
await ring(page.locator('main'), 'Calls grouped by company'); await hold(2000)
await unring()
await page.evaluate((id) => { location.hash = '#/meeting/' + id }, mid)
await page.waitForSelector('.mt-line'); await hold(700)
await click(page.getByRole('button', { name: 'Share', exact: true }), 'Share', 900)
await ring(page.locator('.modal'), 'Private, a team or the whole organization, plus links'); await hold(2400)
await page.keyboard.press('Escape'); await hold(500)
await unring()

// 11 · upload + admin while it processes
await block(15)
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
await block(16)
chapter = 'Admin, while it processes'
await cap(12, 'Meanwhile, the admin side: teams and roles, invite links, meeting types (detected automatically), custom templates, scorecards and a transcription dictionary.')
await page.evaluate(() => { location.hash = '#/admin/teams' })
await page.waitForSelector('.in-subnav'); await hold(900)
for (const [name, tag] of [['Teams', 'Teams and leads'], ['Meeting types', 'Auto-detected meeting types'], ['Scorecards', 'Scorecard criteria'], ['Dictionary', 'Vocabulary the transcripts should get right']]) {
  const l = page.locator('.in-subnav a', { hasText: name }).first()
  if (await l.count()) { await click(l, tag, 2600) }
}
await unring()
await cap(12, 'Back to the upload…', true)
await fast(async () => {
  await page.evaluate((id) => { location.hash = '#/meeting/' + id }, upId)
  await page.waitForSelector('.mt-line', { timeout: 240000 })
  await hold(1500)
}, 12)
await block(17)
chapter = 'Upload ready'
await cap(13, 'Done. The meeting was titled, split by speaker, transcribed and summarised in about a minute.')
await ring(page.locator('.mt-panel'), 'Fresh notes with timestamps'); await hold(6000)
await unring()

// 12 · close
await block(18)
chapter = 'Wrap-up'
await page.evaluate(() => window.__ov.cap())
await card(`<div class="eyebrow">What's built, and what I left out</div><h1>Built for the hour-long, eight&#8209;person call</h1>
  <ul><li>Built: transcripts, cited notes, templates, action items, clips and playlists, sharing and links, comments, search, Ask across calls, trackers, coaching, scorecards, teams and admin, API and webhooks</li>
  <li>Stubbed: calendar sync and the meeting bot (uploads go through the same pipeline)</li>
  <li>Left out: email, CRM sync, SSO. Next: a Google Meet bot, then live notes during the call</li>
  <li>The live demo, the code and the agent logs are linked in the submission</li></ul>`)
await settle()
const endT = Date.now() / 1000
await hold(1200)
await stopCapture()
fs.writeFileSync(`${OUT}/timeline.json`, JSON.stringify({ flashT, blocks, fastRanges, marks, focus, endT, FF }, null, 1))
log(`blocks ${blocks.length}, fast ranges ${fastRanges.length}`)
await browser.close()
xvfb.kill()
