# STDWeb Onboarding — Concrete Spec

A build-ready companion to `ONBOARDING_PLAYBOOK.md`. This pins down the exact steps,
the **real signals** that tick them, the DOM/URLs to anchor them, the data model, and a
phased implementation plan. It is grounded in STDWeb's current code
(`Task` model, `Task.state`, the `action` buttons on `task.html`, the `task_state` endpoint).

---

## 0. Source of truth: the state machine we already have

STDWeb advances `Task.state` server-side (in `stdweb/celery_tasks.py`):

```
initial → uploaded → inspect_done → photometry_done → transients_simple_done
                                  ↘ subtraction_done
        (…_failed variants on error;  cleanup_done resets a task)
```

Onboarding **reads** this; it never writes task state. The value-defining milestone is
`photometry_done`.

---

## 1. Step list (the shared contract)

Canonical `step_key`s, shared between backend derivation and front-end UI. `important`
drives both the "Key step" badge and the quickstart filter.

| # | step_key | Module | important | Derived "done" signal | Manual? |
|---|----------|--------|:---------:|-----------------------|:-------:|
| 1 | `have_frame` | 1 · Get a frame in | ✓ | — (off-screen) or "use demo frame" click | ✓ (only truly un-observable step) |
| 2 | `upload` | 1 · Get a frame in | ✓ | user owns ≥1 `Task` (`state != 'initial'`) | — |
| 3 | `inspect` | 2 · Solve the frame | ✓ | user owns a task with `state='inspect_done'` (or later) | — |
| 4 | `astrometry` | 2 · Solve the frame | | `image.wcs` present in a task dir (WCS solved) | — |
| 5 | `photometry` | 3 · Measure it | ✓ | **user owns a task that reached `photometry_done`** | — |
| 6 | `target` | 3 · Measure it | | `target.vot` present / `config['targets']` set + forced value | — |
| 7 | `subtraction` | 4 · Go further | | `state='subtraction_done'` in any task | — |
| 8 | `transients` | 4 · Go further | | `state='transients_simple_done'` in any task | — |
| 9 | `share` | 4 · Go further | | results downloaded, SkyPortal upload, **or** API token created | — |

**Quickstart** (`important` only) = `have_frame → upload → inspect → photometry`
(+ `target` strongly recommended). That is the ≈5-minute path to the aha.

**Completion** = all `important` steps done → fire `completed`, show the payoff screen.

---

## 2. Module structure (what the user sees)

| Module | Title (user-facing) | Steps | Est. |
|--------|---------------------|-------|-----:|
| 1 | **Get a frame in** | have_frame, upload | ~3 min |
| 2 | **Solve the frame** | inspect, astrometry | ~4 min |
| 3 | **Get your first calibrated magnitude** ← *aha* | photometry, target | ~5 min |
| 4 | **Go further** | subtraction, transients, share | ~10 min |

Each module shows its honest time estimate and a one-line "why this matters".

---

## 3. Spotlights & deep-links (anchor to real UI)

The task page already exposes stable hooks. Use them directly.

| step_key | Page | Spotlight selector | Deep link |
|----------|------|--------------------|-----------|
| upload | `/` (index) | the upload `form[action="{% url 'upload' %}"]` | `/?onboard=upload` |
| inspect | `/tasks/<id>/` | `button[name="action"][value="inspect_image"]` | `/tasks/<id>/?onboard=inspect` |
| astrometry | `/tasks/<id>/` | *Photometry and astrometry* `<h3>` / WCS status row | `/tasks/<id>/?onboard=astrometry` |
| photometry | `/tasks/<id>/` | `button[name="action"][value="photometry_image"]` | `/tasks/<id>/?onboard=photometry` |
| target | `/tasks/<id>/` | the `targets` field in `form_inspect` | `/tasks/<id>/?onboard=target` |
| subtraction | `/tasks/<id>/` | `button[name="action"][value="subtract_image"]` | `/tasks/<id>/?onboard=subtraction` |
| transients | `/tasks/<id>/` | `button[name="action"][value="transients_simple_image"]` | `/tasks/<id>/?onboard=transients` |
| share | `/tasks/<id>/` | `button[name="action"][value="init"]` (SkyPortal) / API-tokens link | `/tasks/<id>/?onboard=share` |

The page must scrub `?onboard=…` after focusing the step (as the app already does with other query params). Step descriptions **reuse the existing `span-help-popover` copy** — do not duplicate/rewrite help text.

---

## 4. Progress derivation (server-side, one query)

Add a small helper (e.g. `stdweb/onboarding.py`) that computes a user's progress from
existing data — **no migration required** for the derive-first version:

```python
def onboarding_progress(user):
    tasks = Task.objects.filter(user=user)
    states = set(tasks.values_list('state', flat=True))
    reached = lambda *s: bool(states & set(s))
    done = {
        'upload':      tasks.exclude(state='initial').exists(),
        'inspect':     reached('inspect_done', 'photometry_done',
                               'subtraction_done', 'transients_simple_done'),
        'photometry':  reached('photometry_done', 'subtraction_done',
                               'transients_simple_done'),
        'subtraction': reached('subtraction_done'),
        'transients':  reached('transients_simple_done'),
        # astrometry/target/share need a file/flag check per task (see §6)
    }
    return done
```

Key properties:
- **Monotonic**: folded across *all* the user's tasks; deleting one task can't un-tick a
  step another task earned. (For strict monotonicity across deletions, back-fill an
  `OnboardingEvent` when a step first flips true — see §5.)
- **Veteran-safe**: exclude the seeded demo task id and read-only/shared tasks from the
  "real work" checks used for veteran detection (§7).

Expose it as JSON at `GET /onboarding/state` for the front-end pill/card, mirroring the
existing lightweight `task_state` endpoint style.

---

## 5. Storage — derive-first, with a thin event log for the rest

- **Progress**: derived (§4). No table.
- **UI state** (per-browser, disposable): `localStorage` — `onboard.dismissed`,
  `onboard.openModule`, `onboard.fastMode`.
- **The few non-derivable facts** (overrides, funnel, off-screen ticks): one append-only
  table, added only when needed:

```python
class OnboardingEvent(models.Model):
    user    = models.ForeignKey(User, on_delete=models.CASCADE)
    kind    = models.CharField(max_length=50)   # started|dismissed|step_undone|
                                                 # have_frame|reward_seen|veteran_backfilled
    step    = models.CharField(max_length=50, blank=True)
    payload = models.JSONField(default=dict, blank=True)  # {"progress": 4} etc.
    created = models.DateTimeField(auto_now_add=True)
```

Current state = `derive(Task.state)` **folded with** last-event-wins from this log
(explicit `step_undone`/override beats derivation — playbook §3). Cache terminal
(`completed`/`dismissed`) runs; recompute active ones cheaply.

---

## 6. Per-task file signals (for astrometry / target / share)

Some steps need a file check inside the task dir (`task.path()`):
- `astrometry` → `image.wcs` exists.
- `target` → `target.vot` exists **or** `config.get('targets')` non-empty and forced-phot ran.
- `share` → any of: a results download event, SkyPortal `init` action succeeded, or the
  user created an API token (there's already an api-tokens surface).

Keep these as cheap `os.path.exists` checks over the user's recent tasks, bounded to the
last N tasks for speed.

---

## 7. Veteran detection (don't condescend)

On first contact, before rendering anything:

```
is_veteran(user) := user has ≥1 non-demo, non-shared Task with state='photometry_done'
```

If veteran → write `veteran_backfilled` event, mark onboarding complete silently, show at
most the pill (or nothing). Never retire a run where the user has an explicit tick/override.
This protects STDWeb's real expert base (RAPAS / pro-am / alert follow-up).

---

## 8. The demo frame (reach the aha with zero data)

Ship a **seeded demo task** (a known, uncrowded field with a resolvable target) that any
new user can open read-only-ish and run through inspect → photometry to see a real
calibrated magnitude. This makes Module 1's `have_frame` optional for the curious and
guarantees everyone can reach the aha. Exclude its id from all "real work" / veteran checks.

---

## 9. Events & funnel (mostly free)

Emit at each transition; most are already `Task.state` changes in `celery_tasks.py`:

`started` · `have_frame` · `upload` · `inspect_done` · `astrometry` · **`photometry_done`** ·
`target_measured` · `subtraction_done` · `transients_done` · `share` · `quickstart_chosen` ·
`dismissed{progress:n}` · `completed`.

High-signal routing (best-effort, never blocks a reduction):
- first-ever `photometry_done` for a user → 🎉 maintainer channel.
- `photometry_failed` on a user's **first** task → 🚑 (very often the crowded-field/blend
  case — a known onboarding-content gap).
- `dismissed{progress}` → 🚪 with how far they got (**the most valuable event**).

---

## 10. Failure-aware content (STDWeb-specific)

First reductions fail in predictable ways; onboarding should catch and coach, not leave the
user staring at a red `…_failed`:

| Failure state / symptom | Inline onboarding hint |
|-------------------------|------------------------|
| `photometry_failed`, "too few calibrators" | crowded/large-PSF field → *tick "Filter catalogue blends" and lower blend radius toward 1×FWHM* (link to the blend-radius note) |
| WCS/astrometry didn't solve | check pixel scale / try blind match; add rough target coords |
| `inspect_failed` | header missing gain/filter → set them in the inspection form |

This turns the single biggest drop-off point (a failed first frame) into a guided recovery.

---

## 11. Front-end surfaces

- **Welcome card** (first contact, index): what STDWeb is → 4 modules → "Upload yours or
  try the demo frame". One component *owns first contact*; suppresses auto-open help
  popovers while active (playbook §9).
- **Pill** (persistent, corner): next step + count; opens the card; ✕ hides for session.
- **Spotlight** overlay: pulses the real `action` button for the current step; reads live
  status from `GET /tasks/<id>/state`.
- **Help `?`**: permanent re-entry + ambient progress ring.
- **Payoff screen**: the calibrated catalogue download + citation line + shareable task
  link + "unlock API/batch" (token + `API_USAGE.md`).

All UI honors `prefers-reduced-motion`.

---

## 12. Phased implementation plan

**Phase 1 — Derive + pill (no migration).**
`onboarding.py` progress helper (§4), `GET /onboarding/state`, the corner pill, spotlights on
the real `action` buttons, deep-links, veteran skip, `localStorage` UI state. Ships value
immediately, zero DB change.

**Phase 2 — Welcome card + demo frame + payoff.**
First-contact takeover, seeded demo task, personalized media (render the user's photometric
plot / target magnitude), payoff screen with catalogue + citation + share link.

**Phase 3 — Events, funnel & coaching.**
`OnboardingEvent` table, transition hooks in `celery_tasks.py`, maintainer-channel alerts,
failure-aware hints (§10), quickstart toggle + analytics.

**Phase 4 — Polish.**
Reduced-motion celebrations, ambient progress ring, A/B on module copy, retire behind one
flag if a v2 arrives.

---

## 13. Definition of done (for the onboarding itself)

- A brand-new account can reach a **real calibrated magnitude** (own frame or demo) in ≤5
  guided minutes, with every tick derived from `Task.state`/files — no self-report.
- A returning veteran sees **no beginner checklist**.
- Every step is deep-linkable and spotlights a real control.
- The funnel shows where first-timers drop off, and a failed first photometry produces a
  helpful hint instead of a dead end.
