# STDWeb Onboarding

Design + build materials for helping new users get from "I just signed up" to their first
**Gaia-calibrated magnitude** — and a short video that explains what STDWeb *is*.

Adapted from a playbook that worked on another product (iMapper), re-grounded in STDWeb's
real workflow (`Task.state`: `uploaded → inspect_done → photometry_done → …`, the `action`
buttons on the task page, the `/tasks/<id>/state` endpoint).

## Contents

- **[ONBOARDING_PLAYBOOK.md](ONBOARDING_PLAYBOOK.md)** — the principles & convictions (14),
  each mapped to what STDWeb should concretely do. Read this first for the "why".
- **[ONBOARDING_SPEC.md](ONBOARDING_SPEC.md)** — build-ready spec: the exact steps, the
  server-side signals that tick them, spotlights/deep-links, data model (derive-first, zero
  migration), veteran detection, failure-aware coaching, and a 4-phase plan.
- **[VIDEO_SCRIPT.md](VIDEO_SCRIPT.md)** — a ~75 s explainer (shot-by-shot storyboard, VO,
  captions) + a 15 s teaser cut for the landing hero.

## The one idea

> Onboarding is the shortest **honest** path to the user's first real win — a calibrated
> magnitude — and the discipline to get out of the way once they're moving. STDWeb's
> `Task.state` machine already *is* the honest checklist; onboarding just reads it.
