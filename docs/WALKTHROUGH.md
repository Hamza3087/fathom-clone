# Walkthrough recording script (4:45 target, camera on)

Record with Loom (screen + camera bubble). Use a 1440×900 browser window.
**Say** is what to say, and **Do** is what to click. Button names match the app exactly.

## Before you press record (2 minutes)

1. Open a **private/incognito** window and go to `http://5.9.117.80:3000`.
   You want to show what a reviewer sees: not logged in.
2. Have `docs/sample-upload-AMI-IS1009a-2min.mp3` from the repo downloaded on your computer, ready for step 4.
3. Open a second private window. You'll paste a clip link into it in step 6 to prove it works logged out.
4. Close other tabs. Turn notifications off.

---

## 1. Intro · 0:00–0:25

**Do:** Stay on the login page, with the camera visible.

**Say:** "This is Sounding, my take on Fathom. Recording in, then a speaker-labelled transcript, notes, action items,
clips and search out. Everything runs on one GPU server with open models: Parakeet for speech, Sortformer and
TitaNet for who's speaking, and Qwen for the notes. No third-party AI APIs. One deliberate call up front: I stubbed the capture
layer. There's no bot joining Zoom. You upload the recording, and it goes through the same pipeline a bot recording
would. I spent that time on what happens after the call."

## 2. Get in as a reviewer · 0:25–0:40

**Do:** Click **Explore the demo workspace**.

**Say:** "Anyone can get in with one click. This is a shared workspace seeded with real recorded meetings: research
meetings with eight or nine people from the ICSI corpus, and product design meetings from AMI. It resets every night."

**Do:** Point at the **My calls** list: titles, meeting-type tags, speaker faces and the "open" counts.

**Say:** "Every meeting was named and summarised by the pipeline. Nothing here was written by hand."

## 3. The hour-long, eight-person call · 0:40–1:50

**Do:** Click **Research Progress and Meeting Overlap Analysis** (the 1-hour meeting).

**Say:** "Fathom's hard case is an hour-long call with eight people, so I started there. The first speaker
model I used can only track four people, so eight came out as four. I measured it against human labels on three real
hour-long meetings, then added a second pass that clusters voice embeddings. Speaker mix-ups dropped from about 20%
to about 6%. It finds seven voices here, and quiet people still get merged. That's in the README."

**Do:** Point at the coloured **speaker strip** and the legend with talk percentages.

**Do:** Click the **play** button, then click any **timestamp** in the transcript on the left. The player jumps.

**Say:** "Transcript and playback stay in sync. Click any line and it plays from there."

**Do:** On the right, under **Summary**, click a teal **timestamp** after a bullet point.

**Say:** "An hour is too long to read, so every note links to the moment it came from. If the model cites a moment
that doesn't exist, it's dropped rather than shown."

## 4. Working with one meeting · 1:50–2:55

**Do:** In **Template**, switch from *Project sync* to **Sales discovery (SPICED)**. A spinner appears while the notes are rewritten.

**Say:** "Templates rewrite the notes for the kind of meeting. Admins can also set a template per meeting type,
and the type is detected automatically."

**Do:** While it's rewriting, click the **Action items** tab. Open the **assignee** dropdown on one item and pick a teammate.

**Say:** "Action items carry who said it and when, and you can assign them. The assignee gets a notification."

**Do:** Click the **Ask** tab and type: `What did they decide about data collection?` Then press Enter.

**Say:** "Ask works on one meeting, and the answer cites the moments." Click a timestamp in the answer.

## 5. Highlight a moment · 2:55–3:20

**Do:** With the recording playing, click **Highlight** (top right of the player).

**Say:** "Highlight saves the last 20 seconds and the next 10, because by the time you notice a moment, it's already
happened. You can also clip a range from the transcript." Show the line menu **⋯ → Clip from here**.

**Do:** Type a title in the clip dialog, then click **Save clip**.

## 6. Share with someone who wasn't on the call · 3:20–3:45

**Do:** Click the **Clips** tab. On your clip, open the **⋯** menu and choose **Copy public link**.

**Do:** Paste it into the **second private window**. The clip plays with its transcript, logged out.

**Say:** "That's the clip only, not the whole recording. Anyone with the link can play it. You can also create
organisation-only links, and turn any link off under **Share**."

## 7. Across meetings · 3:45–4:20

**Do:** Type `battery` in the top **search** bar and press Enter. Results show matching moments.

**Do:** Click **Ask Sounding** in the sidebar and ask: `What deadlines came up across these meetings?`

**Say:** "Search and Ask work across every meeting you can see, and answers link to the meeting and the moment."

**Do:** Click **Trackers** in the sidebar and open **Deadlines and dates**.

**Say:** "Trackers are keyword or AI. The AI ones match meaning, so 'we need this by Friday' counts even without the word deadline."

**Do:** Click **Coaching**, then the **Scorecards** tab.

**Say:** "Talk time, questions and monologues per person, plus AI scorecards per meeting type."

## 8. Upload + what I left out · 4:20–4:45

**Do:** Click **Upload recording** and choose `sample-upload-AMI-IS1009a-2min.mp3`. Leave everything else as it is and
click **Upload and transcribe**. The processing steps appear.

**Say:** "Uploads go through the same pipeline. This one will be done in about a minute. What I left out: the calendar
and meeting bot, which need OAuth app reviews and a domain, plus email and CRM sync. The next thing I'd build is a Google Meet
bot, then live notes. The code and the agent logs are in the repo. Thanks."

---

## If something goes wrong while recording

- **A template switch is still spinning:** carry on to the next step and come back to it. It takes about 20–40 seconds.
- **The upload is still processing at the end:** say "it'll finish in the background" and stop. That's fine.
- **You made a mess of the demo data:** it resets every night at 03:30. Ask Claude to run `demo_reset.sh` if you need a clean slate sooner.
