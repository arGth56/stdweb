"""Public lightcurve pages — independent chrome from the STDWeb task UI."""
from __future__ import annotations

import csv

from django.http import HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.shortcuts import redirect

from . import lightcurve
from . import alerts


def lightcurve_slash(request):
    qs = request.META.get('QUERY_STRING')
    url = '/lightcurve/'
    if qs:
        url += '?' + qs
    return HttpResponsePermanentRedirect(url)


def _can_see_task(request, point):
    user = request.user
    if not user.is_authenticated:
        return False
    return user.is_staff or point.user_id == user.id


def index(request):
    query = (request.GET.get('q') or '').strip()
    if query:
        parsed = lightcurve.parse_search(query)
        if parsed:
            ra, dec = parsed
            return redirect('lightcurve_target', ra=f'{ra:.5f}', dec=f'{dec:.5f}')

    clusters = lightcurve.cluster_targets(lightcurve.public_points())
    if query:
        qlow = query.lower()
        clusters = [
            c for c in clusters
            if qlow in (c['name'] or '').lower()
            or any(qlow in o.lower() for o in c['observers'])
            or any(qlow == f.lower() for f in c['filters'])
        ]

    return TemplateResponse(request, 'lightcurve/index.html', {
        'clusters': clusters,
        'query': query,
        'share_enabled': (
            request.user.is_authenticated
            and hasattr(request.user, 'profile')
            and request.user.profile.share_public_lightcurves
        ),
    })


def _target_points(request, ra, dec):
    try:
        ra_f = float(ra)
        dec_f = float(dec)
    except (TypeError, ValueError):
        return None, None, None
    points = lightcurve.points_near(ra_f, dec_f)
    name = ''
    names = [p.target_name for p in points if p.target_name]
    if names:
        from collections import Counter
        name = Counter(names).most_common(1)[0][0]
    return ra_f, dec_f, {'points': points, 'name': name}


def target(request, ra, dec):
    ra_f, dec_f, info = _target_points(request, ra, dec)
    if ra_f is None:
        return HttpResponse('Invalid coordinates', status=400)
    meta = alerts.ensure_alert_meta(
        info['name'],
        first_mjd=None,
        ra=ra_f,
        dec=dec_f,
    )
    tns = alerts.tns_payload(meta)
    offset = None
    if tns and tns.get('tns_ra') is not None:
        offset = lightcurve.sep_arcsec(ra_f, dec_f, tns['tns_ra'], tns['tns_dec'])
    return TemplateResponse(request, 'lightcurve/target.html', {
        'ra': ra_f,
        'dec': dec_f,
        'name': (tns or {}).get('tns_name') or info['name'],
        'n': len(info['points']),
        'filters': sorted({p.filt for p in info['points']}),
        'json_url': reverse('lightcurve_json', kwargs={'ra': f'{ra_f:.5f}', 'dec': f'{dec_f:.5f}'}),
        'csv_url': reverse('lightcurve_csv', kwargs={'ra': f'{ra_f:.5f}', 'dec': f'{dec_f:.5f}'}),
        'telegram_url': reverse('telegram_object', kwargs={'ra': f'{ra_f:.5f}', 'dec': f'{dec_f:.5f}'}),
        'tns': tns,
        'tns_offset': f'{offset:.2f}″' if offset is not None else '',
        'sky': {
            'ra': round(float(ra_f), 6),
            'dec': round(float(dec_f), 6),
            'name': (tns or {}).get('tns_name') or info['name'] or f'{ra_f:.5f} {dec_f:.5f}',
            'tns_ra': (tns or {}).get('tns_ra'),
            'tns_dec': (tns or {}).get('tns_dec'),
            'tns_name': (tns or {}).get('tns_name') or '',
        },
        'share_enabled': (
            request.user.is_authenticated
            and hasattr(request.user, 'profile')
            and request.user.profile.share_public_lightcurves
        ),
    })


def target_json(request, ra, dec):
    ra_f, dec_f, info = _target_points(request, ra, dec)
    if ra_f is None:
        return JsonResponse({'error': 'invalid coordinates'}, status=400)
    points = [
        lightcurve.point_payload(p, show_task=_can_see_task(request, p))
        for p in info['points']
    ]
    return JsonResponse({
        'ra': ra_f,
        'dec': dec_f,
        'name': info['name'],
        'n': len(points),
        'points': points,
    })


def target_csv(request, ra, dec):
    ra_f, dec_f, info = _target_points(request, ra, dec)
    if ra_f is None:
        return HttpResponse('Invalid coordinates', status=400)
    response = HttpResponse(content_type='text/csv')
    slug = f'{ra_f:.5f}_{dec_f:+.5f}'
    response['Content-Disposition'] = f'attachment; filename="lightcurve_{slug}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'mjd', 'time', 'filter', 'mag', 'magerr', 'mag_limit',
        'is_detection', 'is_diff', 'observer', 'target', 'ra', 'dec',
    ])
    for p in info['points']:
        writer.writerow([
            f'{p.mjd:.8f}',
            p.time_iso,
            p.filt,
            '' if p.mag is None else f'{p.mag:.4f}',
            '' if p.magerr is None else f'{p.magerr:.4f}',
            '' if p.mag_limit is None else f'{p.mag_limit:.4f}',
            int(p.is_detection),
            int(p.is_diff),
            lightcurve.observer_label(p.user),
            p.target_name,
            f'{p.ra:.6f}',
            f'{p.dec:.6f}',
        ])
    return response
