# Sounding

A Fathom-style AI meeting notetaker: recordings in; speaker-labelled transcript, notes, action items, clips,
search and team features out. Runs on one GPU server with open models only (no third-party AI APIs).

**Live:** https://static.80.117.9.5.clients.your-server.de. It opens straight into a shared demo workspace, with no sign-up or login.

**Walkthrough video:** https://static.80.117.9.5.clients.your-server.de/sounding-walkthrough-final.mp4 (4:42, narrated). Videos are hosted on the demo server, not committed.

## What to try

1. Open the demo workspace. The meetings list is seeded with real recordings, including hour-long calls with 8–9 people.
2. Open a meeting. Play it and watch the transcript follow. Click any timestamp in the notes and the player jumps to it.
3. Switch the notes template (General, Sales/SPICED, 1:1, Interview and others). Translate the notes. Draft a follow-up email.
4. Press **Highlight** during playback, or clip a range from the transcript. Share the clip with a public link, then open it in a private window.
5. Search across every meeting from the top bar. Ask a question across all calls under **Ask Sounding**. Answers cite the meeting and the moment.
6. Look at **Trackers** (keyword and AI-matched moments), **Coaching** (talk time and AI scorecards), **Companies**, **Folders** and **Playlists**.
7. Upload your own recording (audio or video). Notes are usually ready in about a minute.

## What's built

| Area | Built |
|---|---|
| Capture | Upload audio or video (stands in for the meeting bot; see below). |
| Transcript | Speaker-labelled with word timing; follows playback; find-in-transcript; rename speakers or link them to teammates. |
| Notes | Templates with sections whose every point links to its moment; meeting types detected automatically; per-user instructions; translation; follow-up drafts. |
| Action items | Extracted with owner and moment; assignable to teammates (notifies them); tick off; add your own. |
| Clips | Highlight button, transcript-range clips, playlists with Play all, clip share links (public or org-only). |
| Sharing | Private / team / org visibility, share with people, public or org-only meeting links, auto-share with attendee teammates. |
| Collaboration | Comments at moments with @mentions, folders, in-app notifications. |
| Search & Ask | Keyword search across transcripts and notes; Ask one meeting; Ask across all meetings, a folder or a company, with citations. |
| Team intelligence | Company view from attendee domains, keyword and AI trackers with subscriptions, talk-time coaching metrics, AI scorecards per meeting type. |
| Admin | Organization, teams, roles, invite links, meeting types, custom templates, scorecards, transcription dictionary. |
| Developer | API keys, REST API (`/api/v1/meetings`), signed webhooks. |
| Edit | Trim a section, or everything before or after a moment (transcript, notes, clips and comments shift with it); downloads in mp3, mp4, txt, srt, vtt and md. |

## Product decisions

**Capture is stubbed on purpose.** A reliable meeting bot is weeks of work: a Zoom Marketplace review, a Google OAuth
verification for calendar scopes, and headless-browser bots per platform. It's also where the product is most
interchangeable. I put the time into what happens after the recording, which is where Fathom earns its keep.
Uploads go through exactly the pipeline a bot recording would.

**The case that matters is the 8-person, hour-long call.** Three things break there, and each was handled:
- *Speakers.* The first diarizer (Sortformer) is capped at 4 speakers, so an 8-person call came out as 4 people.
  I measured it against human labels on three real hour-long ICSI meetings (8–9 people) and built a hybrid:
  Sortformer finds where speech is, and TitaNet voice-embedding clustering (no cap) decides who spoke.
  Speaker confusion fell from 15–21% to 5–8%. The hybrid runs only on calls over 5 minutes, and only replaces
  Sortformer's labels when it hears more than 4 voices. It still undercounts people who barely speak: it found
  5–6 of 8–9, mostly because quiet participants get merged.
- *Context.* An hour of transcript is about 15k tokens. Notes are written in a 32k context, and longer calls are summarised in parts and merged.
- *Reading it.* An hour is too long to read. Every note bullet, action item, tracker hit and search result links to its moment, so you listen to the two minutes that matter.

**Everything links to a moment.** Notes are only trusted if you can check them, so the model is asked to cite
the transcript line for each point. Stamps that don't exist in the recording are dropped rather than shown.

**Open models on our own GPU.** Transcripts never leave the server. The cost is some accuracy compared with
the large hosted models (measured below).

**Left out, and why:**
- Calendar sync, meeting bots and live notes: need a domain with HTTPS and OAuth app reviews (see above).
- Email: summaries and notifications stay in-app. No mail service is configured.
- CRM sync and deal view: needs HubSpot and Salesforce apps. The company view covers the part that doesn't need a CRM.
- SSO and SCIM: enterprise-only in Fathom.

## Models and measured quality

| Stage | Model | Measured on | Result |
|---|---|---|---|
| Speech to text | NVIDIA Parakeet TDT 0.6B v2 | 6 AMI meetings, single far-field mic | 26% WER (Parakeet RNNT 1.1B: 49%) |
| Speakers (≤4 people) | NVIDIA Sortformer v2, streaming | 6 AMI meetings | 33% DER |
| Speakers (8–9 people) | Sortformer speech regions + TitaNet clustering | 3 ICSI meetings, 52–60 min | speaker confusion 5–8% (Sortformer alone: 15–21%); DER 33/48/43% vs 40/49/41% |
| Notes, Ask, trackers, scorecards | Qwen3.5-9B-AWQ on vLLM, 32k context | AMI meetings vs human-written summaries | valid JSON 6/6; decisions matched the human summary |

A 39-minute meeting is processed in about 50 seconds end to end on one RTX 4000 Ada (20 GB).
Scripts and raw results: `model-tests/`.

## Architecture

```
browser ── https (Let's Encrypt) ── Apache ── 127.0.0.1:8020  FastAPI (server/sounding)
                                                  ├─ SQLite  /root/sounding/sounding.db
                                                  ├─ job worker ── ffmpeg ── Sortformer / TitaNet ── Parakeet
                                                  └─ 127.0.0.1:8011  vLLM (Qwen3.5-9B-AWQ)
```

- `server/sounding/`: API (`routes/`), access rules (`auth.py`), pipeline (`media.py`, `speech.py`, `cluster.py`, `jobs.py`), LLM prompts (`ai.py`).
- **One GPU, shared.** Qwen keeps about 14 GB of the 20 GB card loaded. The speech stage for each recording
  (Sortformer, then TitaNet clustering, then Parakeet) runs in a short-lived child process, one model at a time,
  so all of its GPU memory is released when it exits. Running it inside the API process leaked about 6 GB and
  caused out-of-memory failures on the third hour-long upload in a row.
- `src/`: React UI (Vite). Pages are in `src/pages/`; shared components are in `ui.jsx`.
- `scripts/seed_demo.py` seeds the demo through the API. `scripts/demo_reset.sh` resets it nightly.
- `scripts/capture_agent_logs.py` exports the agent session that built this into `.agent-logs/`.

## Demo workspace

- Every visitor is signed into a shared demo account that owns the nine seeded meetings. Admins sign in at `/#/login`.
- Demo viewers don't see real members' email addresses.
- Reviewers can do everything except change people, invites, account settings, API keys and webhooks.
  They also can't delete or trim the seeded meetings.
- Demo uploads are capped at 300 MB, 75 minutes and 6 a day.
- The workspace resets to its seeded snapshot every night at 03:30 (`sounding-demo-reset.timer`).

## Demo data and licences

- **ICSI Meeting Corpus**: real research-group meetings, CC BY 4.0 (International Computer Science Institute; distributed by the University of Edinburgh).
- **AMI Meeting Corpus**: product-design meetings recorded for research, CC BY 4.0 (AMI Consortium).
- Attendee emails on the seeded meetings use `.example` domains. Names shown for speakers come from the recordings or were detected by the model.

## Agent logs

The project was built with Claude Code. `.agent-logs/` has the exported prompts, responses and tool calls:
the main session plus seven sub-agents. Three built UI pages in parallel. Four made up the video post-production
team: camera, sound, graphics and QA.

- **How they were captured:** the 8x capture tool wasn't available in this environment, so I used an equivalent
  exporter (`scripts/capture_agent_logs.py`). It was set up partway through the build. The session history before
  that point was exported retroactively in full, and the logs were committed as work progressed from then on.
- **What was redacted before publishing** (`scripts/redact_logs.py`):
  - credentials (tokens, passwords, session cookies, keys)
  - personal email addresses
  - IP addresses other than the demo host
  - details of unrelated systems on the machine the session ran on

## Running it

```
# UI
npm install && npm run build            # deploy dist/ to /root/sounding/web
# API (on the GPU box)
uvicorn sounding.app:app --host 127.0.0.1 --port 8020   # from server/, env in /root/sounding/sounding.env
```
