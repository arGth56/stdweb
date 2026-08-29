"""Public lightcurve: extract forced photometry and cluster by sky position."""
from __future__ import annotations

import logging
import math
import os
from collections import Counter

import numpy as np

from . import models

log = logging.getLogger(__name__)

MATCH_ARCSEC = 5.0
SN_DETECTION = 3.0


def observer_label(user):
    if not user:
        return 'unknown'
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    name = name or user.username
    affiliation = ''
    try:
        affiliation = (user.profile.affiliation or '').strip()
    except Exception:
        pass
    if affiliation:
        return f'{name} ({affiliation})'
    return name


def public_points():
    return (
        models.LightcurvePoint.objects
        .filter(published=True)
        .select_related('user', 'user__profile', 'task')
    )


def sep_arcsec(ra1, dec1, ra2, dec2):
    dra = (float(ra1) - float(ra2)) * 3600.0 * math.cos(math.radians(0.5 * (float(dec1) + float(dec2))))
    ddec = (float(dec1) - float(dec2)) * 3600.0
    return math.hypot(dra, ddec)


def _finite(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _target_name(config):
    raw = str(config.get('target') or '').strip()
    if not raw:
        return ''
    return raw.splitlines()[0].strip()[:250]


def _read_vot(path):
    from astropy.table import Table

    tbl = Table.read(path)
    if len(tbl) == 0:
        return None
    row = tbl[0]
    mag = _finite(row['mag_calib']) if 'mag_calib' in tbl.colnames else None
    magerr = _finite(row['mag_calib_err']) if 'mag_calib_err' in tbl.colnames else None
    mag_limit = _finite(row['mag_limit']) if 'mag_limit' in tbl.colnames else None
    mag_filter_name = ''
    if 'mag_filter_name' in tbl.colnames:
        mag_filter_name = str(row['mag_filter_name'] or '')
    detected = mag is not None and magerr is not None and magerr > 0 and magerr < 1.0 / SN_DETECTION
    return {
        'mag': mag if detected else None,
        'magerr': magerr if detected else None,
        'mag_limit': mag_limit,
        'is_detection': detected,
        'mag_filter_name': mag_filter_name,
    }


def extract_measurement(task):
    """Return photometry fields from sub_target.vot or target.vot, or None."""
    base = task.path()
    for name, is_diff in (('sub_target.vot', True), ('target.vot', False)):
        path = os.path.join(base, name)
        if os.path.exists(path):
            try:
                data = _read_vot(path)
            except Exception as exc:
                log.warning('lightcurve: could not read %s: %s', path, exc)
                data = None
            if data:
                data['is_diff'] = is_diff
                return data
    return None


def upsert_from_task(task):
    """Create or update the lightcurve point for a finished photometry task."""
    config = task.config or {}
    ra = _finite(config.get('target_ra'))
    dec = _finite(config.get('target_dec'))
    if ra is None or dec is None:
        return None

    time_iso = str(config.get('time') or '').strip()
    if not time_iso:
        return None
    try:
        from astropy.time import Time
        mjd = float(Time(time_iso).mjd)
    except Exception:
        return None

    meas = extract_measurement(task)
    if meas is None:
        models.LightcurvePoint.objects.filter(task=task).delete()
        return None

    filt = str(config.get('filter') or meas.get('mag_filter_name') or '').strip() or '?'
    defaults = {
        'user': task.user,
        'ra': ra,
        'dec': dec,
        'filt': filt[:50],
        'mag_filter_name': (meas.get('mag_filter_name') or '')[:50],
        'target_name': _target_name(config),
        'mjd': mjd,
        'time_iso': time_iso[:50],
        'mag': meas.get('mag'),
        'magerr': meas.get('magerr'),
        'mag_limit': meas.get('mag_limit'),
        'is_detection': bool(meas.get('is_detection')),
        'is_diff': bool(meas.get('is_diff')),
    }
    point, created = models.LightcurvePoint.objects.update_or_create(task=task, defaults=defaults)
    if created and user_has_published_target(task.user, ra, dec):
        point.published = True
        point.save(update_fields=['published'])
    return point


def user_has_published_target(user, ra, dec, radius_arcsec=MATCH_ARCSEC):
    for point in models.LightcurvePoint.objects.filter(user=user, published=True):
        if sep_arcsec(ra, dec, point.ra, point.dec) <= radius_arcsec:
            return True
    return False


def set_published_for_task(task, published):
    """Publish or unpublish this user's photometry of this target (not other targets)."""
    point = upsert_from_task(task)
    if point is None:
        return 0, None
    n = 0
    alert = (task.config or {}).get('tns_alert') or {}
    tns_name = (alert.get('tns_name') or '') if alert.get('matched') else ''
    for other in models.LightcurvePoint.objects.filter(user=task.user):
        if sep_arcsec(point.ra, point.dec, other.ra, other.dec) <= MATCH_ARCSEC:
            changed = other.published != published
            other.published = published
            if tns_name and other.target_name != tns_name:
                other.target_name = tns_name[:250]
                changed = True
            if changed:
                other.save()
            n += 1
    return n, point


def cluster_targets(points, radius_arcsec=MATCH_ARCSEC):
    clusters = []
    for point in points:
        placed = False
        for cluster in clusters:
            if sep_arcsec(point.ra, point.dec, cluster['ra'], cluster['dec']) <= radius_arcsec:
                cluster['points'].append(point)
                placed = True
                break
        if not placed:
            clusters.append({
                'ra': point.ra,
                'dec': point.dec,
                'points': [point],
            })

    out = []
    for cluster in clusters:
        pts = cluster['points']
        names = [p.target_name for p in pts if p.target_name]
        bands = sorted({p.filt for p in pts})
        observers = sorted({observer_label(p.user) for p in pts})
        mjds = [p.mjd for p in pts]
        ras = [p.ra for p in pts]
        decs = [p.dec for p in pts]
        name = Counter(names).most_common(1)[0][0] if names else ''
        ra_m = float(np.mean(ras))
        dec_m = float(np.mean(decs))
        out.append({
            'ra': ra_m,
            'dec': dec_m,
            'ra_s': f'{ra_m:.5f}',
            'dec_s': f'{dec_m:.5f}',
            'name': name,
            'n': len(pts),
            'filters': bands,
            'observers': observers,
            'mjd_min': min(mjds),
            'mjd_max': max(mjds),
            'last_iso': max((p.time_iso for p in pts), default=''),
            'points': pts,
        })
    out.sort(key=lambda c: c['mjd_max'], reverse=True)
    return out


def points_near(ra, dec, queryset=None, radius_arcsec=MATCH_ARCSEC):
    qs = queryset if queryset is not None else public_points()
    nearby = []
    for point in qs:
        if sep_arcsec(ra, dec, point.ra, point.dec) <= radius_arcsec:
            nearby.append(point)
    nearby.sort(key=lambda p: p.mjd)
    return nearby


def parse_search(query):
    """Return (ra, dec) or None. Accepts decimal pairs or a resolvable name."""
    text = (query or '').strip()
    if not text:
        return None
    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        parts = text.replace(',', ' ').split()
        if len(parts) >= 2 and not any(c.isalpha() for c in ''.join(parts[:2])):
            coord = SkyCoord(float(parts[0]), float(parts[1]), unit='deg')
            return float(coord.ra.deg), float(coord.dec.deg)
        coord = SkyCoord.from_name(text)
        return float(coord.ra.deg), float(coord.dec.deg)
    except Exception:
        return None


def point_payload(point, show_task=False):
    data = {
        'time': point.time_iso,
        'mjd': point.mjd,
        'ra': point.ra,
        'dec': point.dec,
        'filter': point.filt,
        'mag': point.mag,
        'magerr': point.magerr,
        'mag_limit': point.mag_limit,
        'is_detection': point.is_detection,
        'is_diff': point.is_diff,
        'observer': observer_label(point.user),
        'target': point.target_name,
    }
    if show_task:
        data['task_id'] = point.task_id
    return data
