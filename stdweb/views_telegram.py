"""Public telegrams: one editable notice per published measurement."""
from __future__ import annotations

import requests

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import celery_tasks
from . import lightcurve
from . import models
from . import telegram as tg


def telegram_slash(request):
    qs = request.META.get('QUERY_STRING')
    url = '/telegram/'
    if qs:
        url += '?' + qs
    return HttpResponsePermanentRedirect(url)


def _decorate(notice, request):
    point = notice.point
    task_id = point.task_id
    return {
        'id': notice.id,
        'object_name': notice.object_name,
        'title': notice.title or notice.object_name,
        'body': notice.body,
        'alert_url': notice.alert_url,
        'alert_kind': notice.alert_kind,
        'alert_label': (
            'TNS' if notice.alert_kind == 'tns'
            else 'GCN' if notice.alert_kind == 'gcn'
            else 'Alert'
        ),
        'mag_s': tg.mag_string(point),
        'filt': point.filt,
        'time': (point.time_iso or '')[:19],
        'mjd': point.mjd,
        'is_diff': point.is_diff,
        'observer': tg.observer_line(notice.user),
        'task_id': task_id,
        'task_url': tg.task_url(request, task_id),
        'url': reverse('telegram_detail', kwargs={'pk': notice.id}),
        'cutout_url': reverse('telegram_cutout', kwargs={'pk': notice.id}),
        'has_cutout': bool(tg.cutout_relpath(point.task)),
        'created': notice.created,
    }


def index(request):
    query = (request.GET.get('name') or request.GET.get('q') or '').strip()
    notices = list(
        models.TelegramNotice.objects.select_related(
            'point', 'point__task', 'user', 'user__profile',
        )
    )
    objects = [_decorate(n, request) for n in notices]
    if query:
        qlow = query.lower()
        objects = [
            o for o in objects
            if qlow in (o['object_name'] or '').lower()
            or qlow in (o['observer'] or '').lower()
            or qlow in (o['body'] or '').lower()
        ]
    return TemplateResponse(request, 'lightcurve/telegram.html', {
        'objects': objects,
        'query': query,
    })


def _verify_turnstile(request, expected_action='subscribe'):
    """Canonical Cloudflare Turnstile siteverify.

    The browser posts ``cf-turnstile-response``; we redeem it server-side and
    require ``success`` *and* the expected ``action`` *and* a frontend
    ``hostname`` from this deployment's allowlist. The token is single-use.
    """
    token = request.POST.get('cf-turnstile-response', '')
    if not token or len(token) > 2048:
        return False

    # Frontend hostnames this deployment accepts. Prefer an explicit allowlist
    # from .env; otherwise fall back to the (ALLOWED_HOSTS-validated) request host.
    allow = [h.strip() for h in getattr(settings, 'TURNSTILE_HOSTNAMES', '').split(',') if h.strip()]
    if not allow:
        allow = [request.get_host().split(':')[0]]

    try:
        resp = requests.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={
                'secret': settings.TURNSTILE_SECRET_KEY,
                'response': token,
                'remoteip': (request.META.get('HTTP_CF_CONNECTING_IP')
                             or request.META.get('REMOTE_ADDR', '')),
            },
            timeout=10,
        )
        result = resp.json()
    except Exception:
        return False

    if not result.get('success'):
        return False
    # Cloudflare's official dev *test* keys always pass but report hostname
    # 'example.com' and no action; skip the strict checks for them only. Real
    # production keys never set this flag, so prod stays fully enforced.
    if (result.get('metadata') or {}).get('result_with_testing_key'):
        return True
    # A widget with data-action set echoes it back; enforce it when present.
    action = result.get('action')
    if expected_action and action and action != expected_action:
        return False
    # Cloudflare always returns the solving hostname on success; require a match.
    hostname = result.get('hostname')
    if hostname and hostname not in allow:
        return False
    return True


@require_POST
def subscribe(request):
    """Public: add an email to the telegram notification list (captcha-protected)."""
    next_url = reverse('telegram')
    email = (request.POST.get('email') or '').strip().lower()

    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, 'Please enter a valid email address.')
        return HttpResponseRedirect(next_url)

    if not _verify_turnstile(request):
        messages.error(request, 'Captcha verification failed. Please try again.')
        return HttpResponseRedirect(next_url)

    sub, created = models.TelegramSubscriber.objects.get_or_create(email=email)
    if created:
        try:
            tg.send_subscription_welcome(request, sub)
        except Exception:
            pass
        messages.success(request, f'{email} is now subscribed to telegram alerts.')
    else:
        messages.info(request, f'{email} is already subscribed.')
    return HttpResponseRedirect(next_url)


def unsubscribe(request, token):
    """One-click opt-out from the link included in every notification email."""
    sub = models.TelegramSubscriber.objects.filter(token=token).first()
    if sub:
        email = sub.email
        sub.delete()
        messages.success(request, f'{email} has been unsubscribed from telegram alerts.')
    else:
        messages.info(request, 'This unsubscribe link is no longer valid.')
    return HttpResponseRedirect(reverse('telegram'))


def detail(request, pk):
    notice = get_object_or_404(
        models.TelegramNotice.objects.select_related(
            'point', 'point__task', 'user', 'user__profile',
        ),
        pk=pk,
    )
    return TemplateResponse(request, 'lightcurve/telegram_object.html', {
        'obj': _decorate(notice, request),
        'notice': notice,
    })


def object_page(request, ra, dec):
    """Old sky-coordinate URL: send to the notice at that position, if any."""
    try:
        ra_f, dec_f = float(ra), float(dec)
    except (TypeError, ValueError):
        return HttpResponseRedirect(reverse('telegram'))
    best, best_sep = None, 8.0
    for notice in models.TelegramNotice.objects.select_related('point'):
        sep = lightcurve.sep_arcsec(ra_f, dec_f, notice.point.ra, notice.point.dec)
        if sep < best_sep:
            best, best_sep = notice, sep
    if best is None:
        return HttpResponseRedirect(reverse('telegram'))
    return HttpResponsePermanentRedirect(
        reverse('telegram_detail', kwargs={'pk': best.id}),
    )


def _png_response(data, public=False):
    if not data:
        return HttpResponse('not found', status=404)
    response = HttpResponse(data, content_type='image/png')
    if public:
        response['Cache-Control'] = 'public, max-age=300'
    else:
        response['Cache-Control'] = 'private, no-store'
    return response


def cutout_png(request, pk):
    notice = get_object_or_404(
        models.TelegramNotice.objects.select_related('point__task'),
        pk=pk,
    )
    return _png_response(tg.render_illustration(notice.point.task), public=True)


def _may_edit_task(request, task):
    return request.user.is_authenticated and (
        request.user.is_staff or request.user == task.user
    )


def _task_cutout_allowed(request, task, point):
    if point is not None and point.published:
        return True
    return _may_edit_task(request, task)


def task_cutout_png(request, id):
    task = get_object_or_404(models.Task, id=id)
    point = models.LightcurvePoint.objects.filter(task=task).first()
    if not _task_cutout_allowed(request, task, point):
        return HttpResponse('not found', status=404)
    return _png_response(tg.render_illustration(task), public=bool(point and point.published))


@login_required
def compose(request, id):
    task = get_object_or_404(models.Task, id=id)
    if not _may_edit_task(request, task):
        messages.error(request, 'Only the observer can publish this photometry.')
        return HttpResponseRedirect(reverse('tasks', kwargs={'id': id}))
    if task.celery_id:
        messages.warning(request, f'Task {id} is still running.')
        return HttpResponseRedirect(reverse('tasks', kwargs={'id': id}))

    point = lightcurve.upsert_from_task(task)
    if point is None:
        messages.error(
            request,
            'No target photometry to publish. Finish photometry (or subtraction) first.',
        )
        return HttpResponseRedirect(reverse('tasks', kwargs={'id': id}))

    existing = models.TelegramNotice.objects.filter(point=point).first()
    alert = tg.resolve_alert(task, point, user=request.user)
    has_cutout = bool(tg.cutout_relpath(task))

    if request.method == 'POST':
        object_name = (request.POST.get('object_name') or '').strip()[:120]
        title = (request.POST.get('title') or '').strip()[:200]
        body = (request.POST.get('body') or '').strip()
        alert_url = (request.POST.get('alert_url') or '').strip()[:250]
        alert_kind = (request.POST.get('alert_kind') or alert.get('alert_kind') or '')[:20]
        if not body:
            messages.error(request, 'The telegram text cannot be empty.')
        elif not object_name:
            messages.error(request, 'Object must be the alert name (TNS, EP-WXT, GCN, …).')
        else:
            if object_name and point.target_name != object_name:
                point.target_name = object_name[:250]
            point.published = True
            point.save()
            notice, created = models.TelegramNotice.objects.update_or_create(
                point=point,
                defaults={
                    'user': task.user,
                    'object_name': object_name,
                    'alert_url': alert_url,
                    'alert_kind': alert_kind,
                    'title': title or object_name,
                    'body': body,
                },
            )
            if created:
                # Notify subscribers only on first publication, not on edits.
                try:
                    celery_tasks.task_notify_subscribers.delay(notice.id)
                except Exception:
                    pass
            messages.success(request, f'Published telegram for {object_name}.')
            return HttpResponseRedirect(
                reverse('telegram_detail', kwargs={'pk': notice.id}),
            )
    else:
        object_name = (existing.object_name if existing else '') or alert.get('object_name') or ''
        title = (existing.title if existing else '') or tg.draft_title(alert)
        body = (existing.body if existing else '') or tg.draft_body(
            task, point, task.user, request, alert,
        )
        alert_url = (existing.alert_url if existing else '') or alert.get('alert_url') or ''
        alert_kind = (existing.alert_kind if existing else '') or alert.get('alert_kind') or ''

    return TemplateResponse(request, 'telegram_compose.html', {
        'task': task,
        'point': point,
        'object_name': object_name,
        'title': title,
        'body': body,
        'alert_url': alert_url,
        'alert_kind': alert_kind,
        'has_cutout': has_cutout,
        'cutout_url': reverse('telegram_task_cutout', kwargs={'id': task.id}),
        'mag_s': tg.mag_string(point),
        'observer': tg.observer_line(task.user),
        'task_url': tg.task_url(request, task.id),
        'existing': existing,
        'alert': alert,
    })
