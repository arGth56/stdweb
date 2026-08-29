"""Simplified TNS: public objects and their raw photometry."""
from __future__ import annotations

from astropy.time import Time

from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from . import alerts
from . import lightcurve


def telegram_slash(request):
    qs = request.META.get('QUERY_STRING')
    url = '/telegram/'
    if qs:
        url += '?' + qs
    return HttpResponsePermanentRedirect(url)


def _format_age(days):
    if days is None:
        return ''
    sign = '+' if days >= 0 else ''
    if abs(days) < 2:
        return f'{sign}{days * 24:.1f} h'
    return f'{sign}{days:.1f} d'


def _share_enabled(request):
    return (
        request.user.is_authenticated
        and hasattr(request.user, 'profile')
        and request.user.profile.share_public_lightcurves
    )


def _mag_string(point):
    if point.is_detection and point.mag is not None:
        if point.magerr is not None:
            return f'{point.mag:.3f} ± {point.magerr:.3f}'
        return f'{point.mag:.3f}'
    if point.mag_limit is not None:
        return f'> {point.mag_limit:.2f}'
    return '—'


def _now_mjd():
    return float(Time.now().mjd)


def _decorate_cluster(cluster):
    meta = alerts.ensure_alert_meta(
        cluster['name'],
        first_mjd=cluster['mjd_min'],
        first_iso=min((p.time_iso for p in cluster['points']), default=''),
        ra=cluster['ra'],
        dec=cluster['dec'],
    )
    tns = alerts.tns_payload(meta)
    discovery_mjd = meta.discovery_mjd if meta else cluster['mjd_min']
    discovery_iso = (meta.discovery_iso if meta else '') or ''
    source = (meta.source if meta else 'first_epoch') or 'first_epoch'
    tns_url = (tns or {}).get('tns_url') or ''
    name = (tns or {}).get('tns_name') or cluster['name'] or f"{cluster['ra_s']} {cluster['dec_s']}"

    offset = None
    if tns and tns.get('tns_ra') is not None:
        offset = lightcurve.sep_arcsec(cluster['ra'], cluster['dec'], tns['tns_ra'], tns['tns_dec'])

    last = max(cluster['points'], key=lambda p: p.mjd)
    rows = []
    for point in sorted(cluster['points'], key=lambda p: p.mjd, reverse=True):
        age = alerts.age_days(point.mjd, discovery_mjd)
        rows.append({
            'time': point.time_iso,
            'mjd': point.mjd,
            'age': _format_age(age),
            'filt': point.filt,
            'mag_s': _mag_string(point),
            'mag': point.mag,
            'magerr': point.magerr,
            'mag_limit': point.mag_limit,
            'is_detection': point.is_detection,
            'is_diff': point.is_diff,
            'observer': lightcurve.observer_label(point.user),
        })

    return {
        'name': name,
        'ra_s': cluster['ra_s'],
        'dec_s': cluster['dec_s'],
        'n': cluster['n'],
        'filters': cluster['filters'],
        'observers': cluster['observers'],
        'discovery_iso': discovery_iso[:19] if discovery_iso else '',
        'source': source,
        'tns_url': tns_url,
        'tns': tns,
        'tns_offset': f'{offset:.2f}″' if offset is not None else '',
        'sky': {
            'ra': round(float(cluster['ra']), 6),
            'dec': round(float(cluster['dec']), 6),
            'name': name,
            'tns_ra': (tns or {}).get('tns_ra'),
            'tns_dec': (tns or {}).get('tns_dec'),
            'tns_name': (tns or {}).get('tns_name') or '',
        },
        'age_now': _format_age(alerts.age_days(_now_mjd(), discovery_mjd)),
        'last_time': last.time_iso,
        'last_filt': last.filt,
        'last_mag': _mag_string(last),
        'last_observer': lightcurve.observer_label(last.user),
        'rows': rows,
        'object_url': reverse('telegram_object', kwargs={
            'ra': cluster['ra_s'], 'dec': cluster['dec_s'],
        }),
        'lc_url': reverse('lightcurve_target', kwargs={
            'ra': cluster['ra_s'], 'dec': cluster['dec_s'],
        }),
    }


def index(request):
    query = (request.GET.get('name') or request.GET.get('q') or '').strip()
    clusters = lightcurve.cluster_targets(lightcurve.public_points())
    objects = [_decorate_cluster(c) for c in clusters]
    if query:
        qlow = query.lower()
        objects = [
            o for o in objects
            if qlow in (o['name'] or '').lower()
            or qlow in o['ra_s']
            or any(qlow in obs.lower() for obs in o['observers'])
            or qlow in ((o.get('tns') or {}).get('obj_type') or '').lower()
            or qlow in ((o.get('tns') or {}).get('host_name') or '').lower()
        ]
    return TemplateResponse(request, 'lightcurve/telegram.html', {
        'objects': objects,
        'query': query,
        'share_enabled': _share_enabled(request),
    })


def object_page(request, ra, dec):
    try:
        ra_f = float(ra)
        dec_f = float(dec)
    except (TypeError, ValueError):
        return HttpResponse('Invalid coordinates', status=400)
    points = lightcurve.points_near(ra_f, dec_f)
    if not points:
        clusters = []
    else:
        clusters = lightcurve.cluster_targets(points)
    if not clusters:
        meta = alerts.ensure_alert_meta('', ra=ra_f, dec=dec_f)
        tns = alerts.tns_payload(meta)
        offset = None
        if tns and tns.get('tns_ra') is not None:
            offset = lightcurve.sep_arcsec(ra_f, dec_f, tns['tns_ra'], tns['tns_dec'])
        return TemplateResponse(request, 'lightcurve/telegram_object.html', {
            'obj': None,
            'ra': ra_f,
            'dec': dec_f,
            'tns': tns,
            'tns_offset': f'{offset:.2f}″' if offset is not None else '',
            'sky': {
                'ra': round(float(ra_f), 6),
                'dec': round(float(dec_f), 6),
                'name': (tns or {}).get('tns_name') or f'{ra_f:.5f} {dec_f:.5f}',
                'tns_ra': (tns or {}).get('tns_ra'),
                'tns_dec': (tns or {}).get('tns_dec'),
                'tns_name': (tns or {}).get('tns_name') or '',
            },
            'share_enabled': _share_enabled(request),
        })
    obj = _decorate_cluster(clusters[0])
    return TemplateResponse(request, 'lightcurve/telegram_object.html', {
        'obj': obj,
        'share_enabled': _share_enabled(request),
    })
