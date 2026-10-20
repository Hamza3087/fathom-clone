# Walkthrough video polish: shared brief

## The product and the video
**Sounding** is a web app, an AI meeting notetaker in the style of Fathom (fathom.video). Its author, **Hamza**,
recorded a narrated product walkthrough. The screen recording has already been cut and timed to his voice.
Your job is one post-production polish pass on top of it. Other specialists handle the other passes in parallel.

The video: 1920×1080, 30 fps, about 4:43 long. It's a screen recording of the app. It contains:
- a fake cursor with click ripples
- orange (#ff7a1a) highlight rings with small orange labels
- a dark animated title card at the start and an end card at the end

There are no captions in it; subtitles are added in post. Brand colours: teal #0f6b73, light teal #57c2c9,
near-black #0a181c, off-white #f4f6f5, accent orange #ff7a1a. The UI font is **Schibsted Grotesk** (Google Fonts, OFL).

## Inputs (read-only, don't modify)
Directory `IN=/tmp/claude-0/-root/a2db516c-9100-4760-bff1-2f9fe28ddb93/scratchpad/polish`:
- `base.mp4`: the timed screen recording with the narration already mixed in (use its video stream).
- `base.voice.wav`: the narration alone, already placed on the final timeline (same length as the video).
- `base.clean.wav`: the whole cleaned narration recording, untimed.
- `base.edit.json`: everything you need in **final-video seconds**:
  - `total` (duration), `fps`, `width`, `height`, `ff_speed`
  - `blocks[]`: the 18 narration blocks: `n`, `title`, `start`/`end` (section on screen), `voice_at` (when speech starts),
    `voice_dur`, `rec_a0`/`rec_a1` (where that block is in the original recording).
    Mapping: `final_t = voice_at + (recording_t - rec_a0)` for recording times inside that block.
  - `fast[]`: `[start, end]` ranges that were fast-forwarded (AI waiting time played at `ff_speed`×).
  - `captions[]`: `{t, step, chapter, text, fast}`: the section labels and explanations the author wrote. There are 12
    chapters (`step` 1–12, with a `chapter` name each). Use these for chapter names; they're not what's spoken.
  - `cards[]`: `{t, kind}`, where `card` = a full-screen title/end card appears and `card-off` = it goes away.
    There's a title card at the start and an end card that lasts until the video ends.
  - `focus[]`: `{t, rect, tag}`: the moment a highlight ring moves to a UI element. `rect` = `{x, y, width, height}` in
    video pixels (CSS px of a 1920×1080 page), or `null` when the ring is removed. `tag` = the ring's label.
- `/tmp/claude-0/-root/a2db516c-9100-4760-bff1-2f9fe28ddb93/scratchpad/words.json`: speech-recognition word timings for the
  **original recording**, as `[[word, start, end], ...]`. It contains recognition errors (for example "Amza" = Hamza,
  "Ritham" = Fathom, "sounding" = Sounding, "founding" = Sounding, "Meetbot" = Meet bot, "scoreboard" = scorecard).
- `/root/fathom-clone/docs/NARRATION.md`: the script he read, in 18 blocks. He mostly followed it, with small deviations.

## Rules
- Put scripts in `/root/fathom-clone/scripts/video/polish/` (readable Python or shell, with a docstring explaining usage).
  Put outputs in `IN/out/<your-pass>/`. Don't touch other directories or other passes' outputs.
- Tools: ffmpeg 6+ (libass, libfreetype, zoompan, sidechaincompress, xfade, drawtext, drawbox), python3 with numpy and scipy.
  Don't install system packages. `pip install --user` of small pure-Python packages is OK if you really need one.
- Disk is nearly full (about 15 GB free, shared with others): keep temporary files under 3 GB and delete your temp frames when done.
- Keep the exact duration and frame rate. Everything must stay in sync with the narration.
- It must look and sound like a professional product video: understated, clean, no gimmicks, no "AI-generated" feel.
- Verify your own work: render stills or short previews and look at them (you can read images); measure what can be
  measured. Report the commands to reproduce, what you checked, and any known issues.

## Round 3 update (new on-camera narration)
- **New take:** Hamza re-recorded the narration on camera. `IN/base.*` and `scratchpad/words.json` are replaced with the new take,
  and `docs/NARRATION.md` has updated wording.
- **Spoken headings:** he read the section headings aloud. Those spans are removed via `blocks[].cuts` (recording-time ranges),
  so subtitle mapping must drop words inside cuts and shift later words in the block by the cut lengths.
- **Presenter camera bubble:** composited after the zoom pass and before the graphics, bottom-right. It's a circle centred
  at (1745, 905) with diameter 290; with its ring and shadow it occupies about x 1590–1900, y 750–1065. Treat it as a
  keep-out zone: no badge, chip or subtitle may overlap it, and the camera shouldn't park a focused element under it.
- **No login:** the video now opens straight into the app (a ring on the demo banner, tag "No sign-up, no login"). There's
  no Log out or "Back in" step; the shared clip is opened with cookies cleared.
- **Prior QA requests still apply:** chip and badge off the app header, sentence case, US spelling, light grammar clean-up,
  no zoom cropping rows, at least 1.2 s at 1.0 after fast ranges, and per-range `fast_speed[]` on the badge.
