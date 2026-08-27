# STDWeb — "What it is" explainer video

A short, production-ready script + storyboard for the value video that plays on the
**welcome card** (playbook §5) and the landing page. Goal: in about a minute, make a new
observer understand *what STDWeb is* and *why it's worth their next frame* — and land them
on "upload yours, or try the demo".

> **The one sentence the video must deliver:**
> *STDWeb turns a raw FITS frame into a Gaia-calibrated magnitude — science-grade, in your
> browser, for free.*

---

## Format & specs

- **Primary cut:** 70–80 s, 1920×1080, captioned (most people watch muted — captions are
  mandatory, not optional).
- **Teaser cut:** 12–15 s (autoplay-muted loop for the landing hero / social).
- **Tone:** calm, competent, a little wonder. This audience distrusts hype; show the real
  UI and a real number, not stock "science" B-roll.
- **Assets:** screen-record the *actual* app — upload page, task page, the running spinner,
  the photometric-match zeropoint plot, a target cutout, the transient panel. Real pixels
  beat any animation here.
- **Music:** sparse, warm synth pad; duck under VO. No epic trailer drums.
- **Voice:** one narrator, unhurried. VO is optional — the piece must fully work muted via
  captions + on-screen text.

---

## The 75-second cut — shot by shot

| # | Time | Visual | On-screen text | Voiceover |
|---|------|--------|----------------|-----------|
| 1 | 0:00–0:05 | A raw, unremarkable FITS frame fades in — noisy, grey, a few dots. | *You took a frame last night.* | "Last night you captured a frame." |
| 2 | 0:05–0:11 | Cursor drags the FITS onto the STDWeb **upload** form; page redirects to a task. | *Drop it in the browser.* | "Drop it into STDWeb — nothing to install." |
| 3 | 0:11–0:20 | Task page: click **Run initial inspection**; header values (gain, filter, saturation) auto-fill; a mask appears over the frame. | *It reads your header. Finds your target. Masks the junk.* | "It reads the header, resolves your target, and masks the bad pixels — automatically." |
| 4 | 0:20–0:30 | Click **Run photometry**; the running spinner; then the **photometric-match** zeropoint plot renders. | *Blind astrometry. Gaia calibration.* | "Then it solves the astrometry and calibrates against Gaia — G, BP and RP." |
| 5 | 0:30–0:40 | Zoom to the target cutout; a label pops: **"your object: G = 17.46 ± 0.01"**. Hold on it. | *A real magnitude. With an error bar you can trust.* | "And out comes a real, science-grade magnitude — with an honest error bar." |
| 6 | 0:40–0:52 | Quick montage: template **subtraction** difference image; **transient detection** panel lighting up a candidate. | *Subtract a template. Catch what changed.* | "Go further: subtract a reference, do forced photometry, catch the transient." |
| 7 | 0:52–1:02 | The calibrated catalogue downloads; a shareable task link; the citation line. | *Export. Share. Cite.* | "Export the calibrated catalogue, share a link, cite it in your paper or your GCN." |
| 8 | 1:02–1:12 | STDWeb logo; the upload box, pulsing gently. Two buttons: **Upload your frame** · **Try the demo**. | **STDWeb** — science-grade photometry on the web. *Free.* | "STDWeb. Free, science-grade photometry — from your telescope to a number that means something." |
| 9 | 1:12–1:15 | Small print under the buttons. | *Powered by STDPipe · built by S. Karpov* | — |

**Total ≈ 75 s.** If a tighter 60 s is needed, compress shots 6–7 into a single 8 s montage.

---

## The 15-second teaser (muted loop)

| Time | Visual | On-screen text |
|------|--------|----------------|
| 0:00–0:03 | Raw FITS → dragged into browser. | *Raw FITS →* |
| 0:03–0:08 | Photometric-match plot renders. | *→ Gaia-calibrated* |
| 0:08–0:12 | Target cutout with **G = 17.46 ± 0.01**. | *→ a magnitude you can trust* |
| 0:12–0:15 | Logo + upload box. | **STDWeb — free, in your browser** |

Loops seamlessly (end frame ≈ start frame).

---

## Storyboard notes (why each beat is there)

- **Open on the user's problem, not the product** (shot 1). The frame is theirs; the hook is
  "you already did the hard part — now get the science out."
- **The aha is a single number** (shot 5), held long enough to read. Per the playbook, *the
  magnitude is the confetti.* Everything before it is setup; everything after is upside.
- **Real UI throughout.** No fake dashboards. The credibility of this audience is earned by
  showing the actual buttons they'll click (`Run initial inspection`, `Run photometry`).
- **"Free" is stated twice** (shots 8–9). It's a genuine differentiator — one of the few
  places on the web to get publication-quality calibrated magnitudes this fast.
- **CTA offers both paths** (shot 8): upload yours *or* try the demo — mirroring Module 1 so
  the data-less viewer can still reach the aha immediately.

---

## Copy bank (for captions / thumbnails / social)

- "Raw FITS → Gaia-calibrated magnitude. In your browser. Free."
- "From your telescope to a number that means something."
- "Science-grade photometry, no install. Powered by STDPipe."
- "Upload a frame. Get G/BP/RP, an error bar, and a transient — in minutes."

---

## What I need from you to produce the actual file

I can assemble this from screen recordings. To cut the video, provide (or let me script the
capture of):
1. A screen-recording of one real reduction on `stdweb.org.uk` — upload → inspect →
   photometry → (subtraction/transients) → export — ideally on the **demo frame** so the
   target magnitude is clean and repeatable.
2. Confirmation of the target magnitude/label to show in shot 5 (or I'll use the demo
   frame's real value).
3. Logo lockup + preferred accent colour, and whether you want VO or captions-only.

With those, I can deliver the 75 s cut + the 15 s teaser (e.g. via a simple
`ffmpeg`/edit list, or a storyboard a video tool can follow shot-for-shot).
