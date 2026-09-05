# The STDWeb Onboarding Playbook

*How we should build STDWeb's onboarding — the thinking behind it, adapted from a playbook that worked on another product (iMapper), rewritten for what STDWeb actually is: a browser that turns a raw FITS frame into a science-grade, Gaia-calibrated magnitude.*

This is not an API reference. It's the set of principles and product convictions that should shape STDWeb onboarding, each one paired with what STDWeb should concretely do and why. If you take one thing from this doc: **good onboarding is not a tour of buttons — it's the shortest honest path to the user's first real win, and the discipline to get out of the way once they're moving.**

For STDWeb, the first real win is unambiguous:

> **From "I just uploaded a FITS" to "I have a Gaia-calibrated magnitude of my object, with an error bar I can trust."**

Everything below serves that sentence.

---

## The one-line philosophy

> Onboarding exists to move a new observer from "I just signed up" to "STDWeb measured my star", as fast as the pipeline honestly allows — and then to disappear.

When a decision is hard, ask: *does this get the user to a calibrated magnitude faster, or is it just showing off the pipeline?* If it's the latter, cut it.

---

## 1. Structure onboarding as the observer's real journey, not the pipeline's step list

**The spirit.** People don't onboard into "features". They onboard into *doing the job they came to do* — reducing a frame they took last night. The shape of the checklist should mirror the real arc of that job, including the parts that happen at the telescope, not on the screen.

**What STDWeb should do.** Four *modules*, each a genuine phase of an observer's reduction, each with an honest time estimate:

1. **Get a frame in (~3 min)** — log in, understand what a "task" is, upload your first FITS (or pick a ready-made demo frame already on the server). *Off-screen: you have a plate-solved-or-not light frame from your scope.*
2. **Solve the frame (~4 min)** — run *initial inspection* (gain, saturation, filter, target resolution, masking), confirm the astrometry (blind-match or refine WCS).
3. **Get your first calibrated magnitude (~5 min)** — run *photometry*, read the Gaia-calibrated zero point and your target's magnitude ± error. **This is the aha.**
4. **Go further (~10 min)** — template *subtraction* + forced photometry on the difference image, *simple transient detection*, and share/export (download the calibrated catalogue, push to SkyPortal, or grab an API token).

Note that Module 1 is partly *the real world* (having a usable FITS), not clicks — and that we offer a **demo frame** so a curious user with no data can still reach the aha. The journey is the physical + digital path to a delivered magnitude.

**Why it matters for STDWeb.** The pipeline exposes `inspect → photometry → subtraction → transients`. That's the *engine's* order, and it's fine — but the onboarding narrates it as *"solve it, measure it, then go further"*, which is how an observer thinks.

---

## 2. Derive completion from real actions — never make people self-report

**The spirit.** A checkbox the user ticks themselves is a lie waiting to happen. A step is done because the user actually did the thing and the system noticed. Self-reported progress is noise; derived progress is truth.

**What STDWeb should do.** STDWeb already has the perfect, un-fakeable signal: **`Task.state`**, advanced server-side by the Celery workers, plus the files each step writes to the task directory. Map every step to that reality:

| Onboarding step | Real signal (server-side, un-fakeable) |
| --- | --- |
| Uploaded a frame | a `Task` row exists for the user (`state='uploaded'`) |
| Inspected & masked | `state='inspect_done'` (and `image.fits` + gain/FWHM in `config`) |
| **Got calibrated photometry** | **`state='photometry_done'`** + `objects.vot` / `photometry.pickle` written |
| Measured your target | `target.vot` present / `config['targets']` set + forced-phot value |
| Ran subtraction | `state='subtraction_done'` |
| Detected transients | `state='transients_simple_done'` |
| Shared / automated | results downloaded, SkyPortal upload, or an API token created |

The value-defining milestone — *"the pipeline calibrated your frame"* — is **`photometry_done`**, computed by the worker, never trusted from the browser. The client cannot fake it. Reserve a manual "Done" only for genuinely un-observable, off-screen actions (e.g. *"point your telescope / have a FITS ready"*).

This is the single highest-leverage idea in the whole system, and STDWeb is unusually well-positioned for it: **the state machine is already the source of truth.** Onboarding just reads it.

---

## 3. Be honest and monotonic — but let explicit intent win

**The spirit.** Derived state needs rules, or it flickers. Two rules keep it trustworthy: (a) *progress is monotonic* — doing something once counts forever, even if the user later deletes that task; and (b) *explicit user intent outranks the machine* — "mark this not done" sticks and beats re-derivation.

**What STDWeb should do.** Cleaning up a task (`cleanup_task`) or deleting it must **not** un-tick "got calibrated photometry" — the user *did* learn it. Because tasks are disposable and `Task.state` is per-task, onboarding progress should be a property of the **user**, folded across *all their tasks* ("has this user ever reached `photometry_done`?"), not tied to one task's current state. Provide a "mark not done" escape hatch that writes an authoritative override, and resolve the classic race between "user un-ticks" and "a worker finishing a task re-ticks" in favour of the explicit override.

---

## 4. Store the state as events, not a schema you'll regret

**The spirit.** Onboarding is the part of the product that changes most and fastest — steps get added, reordered, renamed, A/B'd. Don't marry it to a rigid schema you migrate every sprint. Model it as an append-only log and reconstruct current state by folding.

**What STDWeb should do.** STDWeb is a small Django app with a single `Task` model and a `config` JSONField — deliberately minimal. Keep onboarding equally light. Two viable options, in order of preference:

- **Derive-first (zero storage).** Compute progress live from what already exists: `Task` rows and their `state`, plus files on disk. "Has the user reached photometry_done?" is a one-line query. UI-only state (dismissed, which module is open, fast-mode) lives in `localStorage`. This adds **no migration** and no new table — very much in STDWeb's spirit.
- **Event log (when you need overrides/analytics).** Add one append-only `OnboardingEvent(user, kind, payload_json, created)` table for the handful of things you *can't* derive: `started`, `dismissed(step_count)`, `step_undone:x`, `reward_seen`, `veteran_backfilled`. Current state = derive-from-Task-state **folded with** last-event-wins from this log. New steps/event kinds slot in with zero schema change; cache only terminal (completed/dismissed) runs.

Either way: **never** put an "onboarding" column on `Task`. Tasks are transient; onboarding is about the *user*.

---

## 5. Take over once — then shrink to a quiet pill and stay out of the way

**The spirit.** The first run deserves a confident, full-attention moment. After that, onboarding must become nearly invisible — a small, dismissible presence. Nagging is how you get people to hate onboarding.

**What STDWeb should do.** On a new user's first visit to the landing/upload page, show a full-attention **welcome card** (what STDWeb is → the four modules → "upload yours, or try a demo frame"). Once they have any real progress (a task exists), collapse it to a small **pill** in the corner: *"Next: run photometry — 2 steps left"*. The card reopens from the pill, the Help/`?` menu, or a deep link; its ✕ hides it for the session. Durable progress lives server-side (follows the user across devices / the two servers — local and stdweb.org.uk); disposable UI prefs live in `localStorage`. A returning user with progress resumes straight to the pill — never re-shown the intro.

---

## 6. Show, don't tell — and make it land on the user's own frame

**The spirit.** A sentence describing a step is far weaker than seeing it happen. And STDWeb has a unique advantage: it produces *pictures* — the pipeline already renders diagnostic PNGs (FWHM vs mag, photometric-match zeropoint maps, cutouts). Use them.

**What STDWeb should do.** Each step's media is, wherever possible, **the user's own output**, not a generic clip:

- Inspection step → show *their* mask / FWHM diagnostic once `inspect_done`.
- Photometry step → the highlight: render *their* photometric-match plot and call out the measured zero point and, if a target was set, *"your object: G = 17.46 ± 0.01"*. That single, personalized number is the most persuasive media in the whole flow.
- For users with no data, the **demo frame** plays the same role end-to-end.

Short GIFs for the buttons that must be clicked; the real rendered plots for the payoff. Always have a graceful fallback (a static example image) so a step never shows a broken box while a worker is still running.

---

## 7. Put the reward at the "aha", and let it pull the whole way

**The spirit.** Motivation should be anchored to the moment of value, not dangled arbitrarily. STDWeb's "aha" *is* the reward — a publication-quality magnitude, for free — so the incentive must reinforce that, not bolt on a generic coupon.

**What STDWeb should do.** Align the reward with what a scientist actually wants next:

- The payoff screen delivers the *deliverable*: a downloadable **calibrated catalogue** + the citation line + a one-click **shareable task link** ("send this to your collaborator / attach to a GCN").
- Tease it throughout ("→ a citable, Gaia-calibrated magnitude at the end") on the intro and the pill.
- Reinforce competence, not discounts: completion can unlock **batch / API access** (an API token + a link to `API_USAGE.md`) so a hooked user can automate their next 200 frames. That is the aligned "power-up" — it deepens the exact value they just tasted.

Alignment matters more than size: the reward is *"you can now do real science, faster."*

---

## 8. Offer two speeds, from one source of truth

**The spirit.** Some users want the guided path; some want the five things that matter. Give both — but never let "quick" and "full" disagree about what's essential.

**What STDWeb should do.** One `important` flag per step drives both the **"Key step"** badge and the **quickstart** filter. Quickstart ≈ 5 min (log in → upload/demo → inspect → **photometry** → read your magnitude); full path ≈ 20 min (adds subtraction, transients, export/API). The essential spine is *upload → inspect → photometry*; everything else is opt-in depth. Switching modes is one toggle, and each toggle is an analytics event so we learn how many observers just want the magnitude vs the whole pipeline.

---

## 9. Cooperate with the app you already have — one thing owns "first contact"

**The spirit.** Onboarding lives inside a real product with its own pages, forms and help popovers. It must cooperate, not fight — and exactly one component owns the user's first moment so two things don't pop at once.

**What STDWeb should do.** The task page already carries a rich set of `span-help-popover` blocks (inspection, photometry, upload…). Onboarding must not collide with them: while the welcome takeover owns first contact, suppress auto-opening help popovers, and let onboarding *reuse the existing popover copy* as its step descriptions (single source of truth — don't re-write the help text). A single predicate — *"is onboarding owning first contact right now?"* — gates this. Onboarding reads task status through the page's existing `task_state` polling endpoint (`GET /tasks/<id>/state`) rather than inventing its own — that endpoint already returns `{state, celery_id}` and is how the page live-updates while a worker runs.

---

## 10. Instrument the entire funnel — quietly, and toward the humans who care

**The spirit.** You can't improve what you can't see. Every meaningful moment should be an event, so drop-off is measurable — and whoever cares about activation gets a gentle, real-time signal without noise.

**What STDWeb should do.** Emit an event at every transition: `started`, `uploaded`, `inspect_done`, `photometry_done`, `subtraction_done`, `dismissed(with how far they got)`, `completed`, `quickstart_chosen`, `api_token_created`. Most of these are *free* because they're already `Task.state` changes — hook the state-transition point in `celery_tasks.py`. Route the high-signal ones (first `photometry_done` for a user 🎉, `photometry_failed` on a first task 🚑, dismissed-with-progress 🚪) to a maintainer channel (email/Slack/webhook), best-effort so telemetry can never break a reduction. **Dismissal-with-progress is gold** — it tells you exactly where observers give up (very often: a *failed* first photometry, e.g. the crowded-field / blend-radius case — which is itself an onboarding-content opportunity).

---

## 11. Detect experienced users and never condescend to them

**The spirit.** Nothing makes a power user feel unseen like a beginner checklist. Detect people already past onboarding and silently mark them complete.

**What STDWeb should do.** On first contact, a "veteran" check looks for *real* prior work: does the user already own tasks that reached `photometry_done` (excluding the seeded **demo** task and read-only shared tasks)? If yes, silently back-fill onboarding as complete and go straight to the pill — or nothing. Retire an *in-progress* run for someone who turns out experienced, but **never** if they've hand-ticked/overridden a step. STDWeb has genuine expert users (RAPAS/pro-am, alert follow-up) who must never be shown "Step 1: what is a FITS file?".

---

## 12. Guide in the real product, and make every step addressable

**The spirit.** The best guidance points at the actual control the user must click, and every step should be linkable — for support, docs, "start here".

**What STDWeb should do.** Anchor spotlights to real DOM the task page already exposes — the action buttons carry stable `name="action" value="…"` hooks:

| Step | Spotlight target |
| --- | --- |
| Upload | the upload `form` on `/` (index) |
| Inspect | `button[value="inspect_image"]` under *Initial inspection and masking* |
| Photometry | `button[value="photometry_image"]` under *Photometry and astrometry* |
| Subtraction | `button[value="subtract_image"]` |
| Transients | `button[value="transients_simple_image"]` |
| Share | SkyPortal `button[value="init"]` / the API-tokens page |

Make each step deep-linkable (`/tasks/<id>/?onboard=<step>`), so support can send "click this to jump exactly here", and scrub the param after jumping. Provide a permanent, low-key re-entry point (the existing `?` help affordance is a natural home) and show progress ambiently (a subtle ring on the Help icon) rather than shouting a percentage.

---

## 13. Celebrate progress — accessibly

**The spirit.** Small wins deserve small celebrations; they pull people forward. But delight must never override accessibility.

**What STDWeb should do.** A small burst when a step ticks; a bigger one at the **photometry_done** aha and at completion — all skipped under `prefers-reduced-motion`. Keep it proportional and, given the audience, tasteful: astronomers will forgive a quiet sparkle, not a carnival. The real celebration is *the number* — surfacing "your object: G = 17.46 ± 0.01" is the confetti that matters.

---

## 14. Design for constant evolution

**The spirit.** Onboarding will be rewritten more than any other part of the product. Build it so change is cheap and old versions retire cleanly.

**What STDWeb should do.** Keep one canonical list of **step keys** (`upload`, `inspect`, `photometry`, `target`, `subtraction`, `transients`, `share`) shared and asserted-equal between the Django side (progress derivation) and the front-end (UI). New steps append; renames reuse keys where meaning matches. If a v2 ever replaces this, gate the old one behind a single obvious flag and ensure only one version "owns" the `completed` transition. Because most progress is *derived from `Task.state`*, evolving the step list rarely needs a migration at all — a very STDWeb-friendly property.

---

## A reusable checklist for building STDWeb onboarding

- [ ] **Write the real journey** to the first calibrated magnitude — telescope-side steps included — chunked into the 4 named, time-estimated modules (upload → solve → measure → go further).
- [ ] **Map every step to a `Task.state` / file signal**; derive automatically. Reserve manual ticks only for off-screen actions ("have a FITS ready").
- [ ] **Anchor the milestone on `photometry_done`** (worker-set, un-fakeable), folded across *all* the user's tasks so it's monotonic.
- [ ] **Progress is a property of the user, not a task**; cleanup/delete never un-ticks; provide an authoritative "mark not done".
- [ ] **Derive-first, zero migration**; add one append-only `OnboardingEvent` table only for overrides/analytics you can't derive.
- [ ] **One big welcome, then a tiny pill.** Durable progress server-side (follows across the two servers); disposable UI prefs in `localStorage`. Never re-pop the intro.
- [ ] **Personalized media = the user's own plots** (photometric-match zeropoint, target magnitude); demo frame for the data-less; static fallbacks while workers run.
- [ ] **Reward = the deliverable**: calibrated catalogue + citation + shareable link, and unlock API/batch. Tease it throughout.
- [ ] **Two speeds from one `important` flag**; quickstart = upload → inspect → photometry.
- [ ] **One predicate owns first contact**; reuse existing help-popover copy; read status via the existing `/tasks/<id>/state` endpoint.
- [ ] **Event at every transition** (mostly free from `Task.state`); route first-photometry, first-failure and dismiss-with-progress to a maintainer channel, best-effort.
- [ ] **Detect veterans** (owns a real `photometry_done` task, excluding the demo); silently complete; never override deliberate ticks.
- [ ] **Spotlight the real `action` buttons; deep-link every step; low-key `?` re-entry; ambient progress ring.**
- [ ] **Proportional, reduced-motion-safe celebration; the real confetti is the magnitude.**
- [ ] **One shared step-key contract; retire old versions behind one flag; only one version owns `completed`.**

---

## The three convictions to keep, if nothing else

1. **Truth over theater.** Derive completion from `Task.state`; never ask users to self-report. STDWeb's state machine *is* the honest checklist.
2. **Take over once, then vanish.** A confident welcome, then a quiet, dismissible, one-click-back pill. Nagging kills onboarding — and this audience especially.
3. **Ship it as (mostly derived) events and measure everything.** Model it so change is nearly migration-free and the funnel is visible — because onboarding is never "done", it's tuned forever, and STDWeb's first-frame failures (bad WCS, crowded-field blends, too-few calibrators) are exactly what the funnel will reveal.

*— iMapper onboarding, distilled and re-grounded in STDWeb. The spirit is identical; the "first real win" is a Gaia-calibrated magnitude.*
