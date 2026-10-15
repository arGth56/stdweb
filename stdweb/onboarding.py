"""Derive onboarding progress from Task.state. No extra table."""
from __future__ import annotations

import os

from django.conf import settings

from . import models

INSPECT_DONE = {
    'inspect_done', 'photometry_done', 'subtraction_done', 'transients_simple_done',
}
PHOTOMETRY_DONE = {
    'photometry_done', 'subtraction_done', 'transients_simple_done',
}


def demo_task_id():
    return int(getattr(settings, 'ONBOARDING_DEMO_TASK_ID', 4853) or 4853)


def page_payload(request):
    demo = demo_task_id()
    user = getattr(request, 'user', None)
    authed = bool(user and user.is_authenticated)
    done = {
        'upload': False,
        'inspect': False,
        'photometry': False,
        'target': False,
    }
    veteran = False
    if authed:
        tasks = models.Task.objects.filter(user=user)
        real = tasks.exclude(id=demo)
        states = set(real.values_list('state', flat=True))
        done['upload'] = real.exclude(state='initial').exists()
        done['inspect'] = bool(states & INSPECT_DONE)
        done['photometry'] = bool(states & PHOTOMETRY_DONE)
        veteran = done['photometry']
        if not done['target']:
            done['target'] = models.LightcurvePoint.objects.filter(user=user).exclude(
                task_id=demo,
            ).exists()
            if not done['target']:
                for task in real.order_by('-id')[:8]:
                    if os.path.exists(os.path.join(task.path(), 'target.vot')):
                        done['target'] = True
                        break
    order = ('upload', 'target', 'inspect', 'photometry')
    nxt = next((k for k in order if not done[k]), 'done')
    return {
        'authenticated': authed,
        'veteran': veteran,
        'demo_task_id': demo,
        'done': done,
        'next': nxt,
    }
