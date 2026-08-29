"""Telegram notices: one text, one measure, one task link, one alert link."""
from __future__ import annotations

from django.conf import settings
from django.urls import reverse

from . import alerts
from . import lightcurve
from . import models


def public_base(request):
    base = (getattr(settings, 'SITE_PUBLIC_BASE_URL', None) or '').rstrip('/')
    if base:
        return base
    if request:
        return request.build_absolute_uri('/').rstrip('/')
    return 'https://stdweb.org.uk'


def task_url(request, task_id):
    return public_base(request) + reverse('tasks', kwargs={'id': task_id})


def mag_string(point):
    if point.is_detection and point.mag is not None:
        if point.magerr is not None:
            return f'{point.mag:.3f} ± {point.magerr:.3f}'
        return f'{point.mag:.3f}'
    if point.mag_limit is not None:
        return f'> {point.mag_limit:.2f}'
    return '—'


def observer_line(user):
    return lightcurve.observer_label(user)


def profile_match_params(user):
    radius_arcmin = 1.0
    age_hours = 24.0
    try:
        profile = models.get_user_profile(user)
        radius_arcmin = float(profile.telegram_radius_arcmin or 1.0)
        age_hours = float(profile.telegram_alert_age_hours or 24.0)
    except Exception:
        pass
    return max(radius_arcmin, 0.01) * 60.0, max(age_hours, 0.1)


def resolve_alert(task, point, user=None):
    """Object name is the alert name (TNS or EP-WXT), never raw coordinates."""
    config = task.config or {}
    ep = config.get('ep_alert') or {}
    tns = config.get('tns_alert') or {}

    if user is not None and point is not None:
        radius_arcsec, _ = profile_match_params(user)
        name = str(config.get('target') or point.target_name or '').splitlines()[0]
        if not ep.get('matched'):
            try:
                ep = alerts.match_ep_alert(
                    point.ra, point.dec,
                    texts=(name, config.get('fits_object')),
                    image_path=None,
                ) or ep
            except Exception:
                pass
        if not tns.get('matched') or not tns.get('on_target'):
            try:
                tns = alerts.match_target(
                    point.ra, point.dec, name=name, radius_arcsec=radius_arcsec,
                ) or tns
            except Exception:
                pass

    if ep.get('matched') and ep.get('trigger'):
        obj = f"EP-WXT {ep['trigger']}"
        if ep.get('best_name') and ep.get('on_target'):
            obj = f"{obj} / {ep['best_name']}"
        return {
            'object_name': obj[:120],
            'alert_url': ep.get('url') or '',
            'alert_kind': 'gcn',
            'alert_label': ep.get('subject') or obj,
            'discovery_iso': '',
            'ep': ep,
            'tns': tns if tns.get('matched') else None,
        }

    if tns.get('matched') and tns.get('tns_name'):
        return {
            'object_name': tns['tns_name'][:120],
            'alert_url': tns.get('tns_url') or '',
            'alert_kind': 'tns',
            'alert_label': tns.get('obj_type') or tns['tns_name'],
            'discovery_iso': tns.get('discovery_iso') or '',
            'ep': None,
            'tns': tns,
        }

    # Last resort: a name that already looks like an alert, never decimal coords.
    raw = str((point.target_name if point else '') or config.get('fits_object') or config.get('target') or '').strip()
    raw = raw.splitlines()[0].strip()[:120]
    looks_alert = bool(alerts.tns_bare_name(raw) or alerts.extract_ep_trigger(raw))
    return {
        'object_name': raw if looks_alert else '',
        'alert_url': '',
        'alert_kind': '',
        'alert_label': '',
        'discovery_iso': '',
        'ep': None,
        'tns': None,
    }


def age_string(point, alert, window_hours=24):
    iso = (alert or {}).get('discovery_iso') or ''
    tns = (alert or {}).get('tns') or {}
    disc_mjd = tns.get('discovery_mjd')
    if disc_mjd is None and iso:
        try:
            from astropy.time import Time
            disc_mjd = float(Time(iso.replace('T', ' ')).mjd)
        except Exception:
            disc_mjd = None
    if disc_mjd is None or point is None:
        return ''
    days = float(point.mjd) - float(disc_mjd)
    hours = days * 24.0
    if abs(hours) < max(float(window_hours), 1.0) * 1.01:
        sign = '+' if hours >= 0 else ''
        return f'{sign}{hours:.1f} h'
    sign = '+' if days >= 0 else ''
    return f'{sign}{days:.1f} d'


def draft_body(task, point, user, request, alert):
    obs = observer_line(user)
    mag = mag_string(point)
    filt = point.filt or ''
    when = (point.time_iso or '')[:19]
    mjd = f'{point.mjd:.5f}'
    obj = alert.get('object_name') or 'the target'
    task_link = task_url(request, task.id)
    alert_url = alert.get('alert_url') or ''
    window_hours = profile_match_params(user)[1]
    age = age_string(point, alert, window_hours)

    lines = [
        f'{obs} reports STDWeb photometry of {obj}.',
        '',
        f'{when} UT   MJD {mjd}   {filt} = {mag}'
        + ('  (difference image)' if point.is_diff else ''),
    ]
    if age:
        lines.append(f'Age of alert: {age}')
    lines += [
        '',
        f'Task: {task_link}',
    ]
    if alert_url:
        kind = 'TNS' if alert.get('alert_kind') == 'tns' else 'Alert'
        if alert.get('alert_kind') == 'gcn':
            kind = 'GCN'
        lines.append(f'{kind}: {alert_url}')
    return '\n'.join(lines)
