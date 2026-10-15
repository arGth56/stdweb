"""Telegram notices: one text, one measure, one task link, one cutout."""
from __future__ import annotations

import io
import os

from django.conf import settings
from django.urls import reverse

from . import alerts
from . import colorcomp
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


def _unsubscribe_url(base, subscriber):
    return base + reverse('telegram_unsubscribe', kwargs={'token': subscriber.token})


def send_subscription_welcome(request, subscriber):
    """Confirmation email sent right after someone subscribes."""
    from django.core.mail import send_mail
    base = public_base(request)
    subject = 'Subscribed to STDWeb telegrams'
    body = (
        "You are now subscribed to STDWeb telegrams.\n"
        "You will receive an email whenever a new telegram is published.\n\n"
        f"Browse telegrams: {base + reverse('telegram')}\n\n"
        f"Unsubscribe at any time: {_unsubscribe_url(base, subscriber)}\n"
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [subscriber.email],
              fail_silently=True)


def telegram_notification(notice, subscriber, base):
    """Return (subject, body) for a 'new telegram published' notification email."""
    point = notice.point
    detail_url = base + reverse('telegram_detail', kwargs={'pk': notice.id})
    task_link = base + reverse('tasks', kwargs={'id': point.task_id})
    subject = f"[STDWeb Telegram] {notice.object_name}"
    lines = [
        notice.title or notice.object_name,
        '',
        f"Object:   {notice.object_name}",
        f"Measure:  {_notification_measure(point.task, point)}",
        f"UTC:      {(point.time_iso or '')[:19]}",
        f"Observer: {observer_line(notice.user)}",
        '',
        (notice.body or '').strip(),
        '',
        f"Details:  {detail_url}",
        f"Task:     {task_link}",
        '',
        f"Unsubscribe: {_unsubscribe_url(base, subscriber)}",
    ]
    return subject, '\n'.join(lines)


def mag_string(point):
    if point.is_detection and point.mag is not None:
        if point.magerr is not None:
            return f'{point.mag:.3f} ± {point.magerr:.3f}'
        return f'{point.mag:.3f}'
    if point.mag_limit is not None:
        return f'> {point.mag_limit:.2f}'
    return '—'


def _finite(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    import math
    return x if math.isfinite(x) else None


def _format_color_name(name):
    """``BPmag - RPmag`` → ``BP − RP`` for telegram text."""
    if not name:
        return 'BP − RP'
    s = str(name)
    for token in ('mag', 'Mag', 'MAG'):
        s = s.replace(token, '')
    s = s.replace(' - ', ' − ').replace('-', ' − ').replace('  ', ' ').strip()
    return s or 'BP − RP'


def _obs_time_ut(time_iso):
    if not time_iso:
        return 'unknown time'
    return str(time_iso)[:19].replace('T', ' ') + ' UT'


def _signed_num(value, prec=3):
    x = _finite(value)
    if x is None:
        return '?'
    return f'{x:+.{prec}f}'.replace('-', '−')


def colorcomp_steps(band, d, col, color_name=''):
    """Structured colour-compensation facts for templates and telegrams."""
    cname = _format_color_name(color_name)
    k_str = _signed_num(d.get('k'))
    col_str = f'{col["value"]:.2f} ± {col["err"]:.2f}'
    return {
        'band': band,
        'mag_cal': d['mag_cal'],
        'magerr_cal': d['magerr_cal'],
        'k': d.get('k'),
        'k_str': k_str,
        'color_name': cname,
        'obs_time': _obs_time_ut(d.get('time_iso')),
        'task_id': d.get('task_id'),
        'target_colour': col_str,
        'correction': d.get('correction'),
        'correction_str': _signed_num(d.get('correction')),
        'mag_true': d['mag'],
        'magerr_true': d['magerr'],
        'equation': (
            f'Gaia {band} = {d["mag_cal"]:.3f} + ({k_str}) × (BP−RP = {col_str})'
            f' = {d["mag"]:.3f} ± {d["magerr"]:.3f} mag'
        ),
    }


def colorcomp_measure_lines(band, d, col, color_name='', triplet_ids=None, mode_label=''):
    """Three-step colour compensation text for telegram bodies."""
    s = colorcomp_steps(band, d, col, color_name=color_name)
    lines = [
        (
            f'(1) Calibrated magnitude (mag_cal): Gaia {band} = {s["mag_cal"]:.3f} ± {s["magerr_cal"]:.3f} mag'
            f' — as on the STDWeb task page; target colour not applied.'
        ),
        (
            f'(2) Colour term measured on this exposure: k = {s["k_str"]}'
            f' ({s["color_name"]} of field stars), from photometric calibration of task {s["task_id"]}'
            f' ({s["obs_time"]}).'
        ),
        '(3) Estimated true Gaia magnitude:',
        f'    {s["equation"]}',
    ]
    if triplet_ids:
        lines.append(
            f'    Triplet tasks {triplet_ids["G"]} (G) / {triplet_ids["BP"]} (BP) / {triplet_ids["RP"]} (RP), {mode_label}.'
        )
    return lines


def _read_target_photometry(task, is_diff):
    """Colour term and filter from the same VOT file as the published measurement."""
    from astropy.table import Table

    base = task.path()
    fname = 'sub_target.vot' if is_diff else 'target.vot'
    path = os.path.join(base, fname)
    if not os.path.exists(path):
        return None
    try:
        tbl = Table.read(path)
    except Exception:
        return None
    if len(tbl) == 0:
        return None
    row = tbl[0]
    mag_filter_name = str(row['mag_filter_name']) if 'mag_filter_name' in tbl.colnames else ''
    return {
        'mag_filter_name': mag_filter_name,
        'band': colorcomp._BAND_OF_COLUMN.get(mag_filter_name.lower()),
        'k': _finite(row['mag_color_term']) if 'mag_color_term' in tbl.colnames else None,
        'color_name': str(row['mag_color_name']) if 'mag_color_name' in tbl.colnames else '',
    }


def _filt_label(band, point_filt, filter_name):
    if band in colorcomp.BANDS:
        return f'Gaia {band}'
    return (point_filt or (filter_name or '') or '?').strip()


def photometry_context(task, point, with_desc=True, with_sisters=False):
    """STDWeb vs colour-compensated magnitudes for one task.

    Cheap by default on list pages: only reads the stored colour-compensation JSON.
    ``with_desc`` additionally reads the task's VOT (colour term k, filter label);
    ``with_sisters`` runs the sister lookup (a DB scan) — use it only on the single-task
    compose page, never when rendering many notices.
    """
    mode = 'sub' if point.is_diff else 'direct'
    cc = colorcomp.load_result(task, mode)
    # Reading the target VOT is only needed for the detailed (compose/detail) path.
    # Keep list rendering cheap: skip it unless callers ask for descriptions.
    target_ph = _read_target_photometry(task, point.is_diff) if with_desc else None

    # Band from the stored triplet mapping first (no file access).
    band = None
    if cc:
        for b, tid in (cc.get('task_ids') or {}).items():
            if tid == task.id:
                band = b
                break

    desc = colorcomp.describe(task, mode) if with_desc else None
    if band is None and desc is not None:
        band = desc.get('band')
    if band is None and target_ph is not None:
        band = target_ph.get('band')

    sisters = None
    if with_sisters and cc is None and desc is not None and band:
        sisters = colorcomp.find_sisters(task, mode)

    compensated_this = compensated_all = None
    if cc and band and band in cc.get('bands', {}):
        compensated_this = cc['bands'][band]
        compensated_all = cc['bands']

    target_colour = cc.get('colour') if cc else None
    steps = None
    if compensated_this and target_colour and band:
        steps = colorcomp_steps(
            band, compensated_this, target_colour,
            color_name=(desc or {}).get('color_name', ''),
        )

    k = (desc or target_ph or {}).get('k')
    color_name = (desc or target_ph or {}).get('color_name') or ''
    config = task.config or {}
    color_term_used = config.get('use_color') is not False and _finite(k) is not None

    filt_label = _filt_label(band, point.filt, (desc or target_ph or {}).get('filter_name'))

    # Single "published measure" headline, harmonised across all telegram types so
    # the eye always lands on the same block first.
    if steps:
        headline = {
            'label': f'Gaia {steps["band"]}',
            'value': f'{steps["mag_true"]:.3f} ± {steps["magerr_true"]:.3f}',
            'note': 'true magnitude (colour-compensated)',
            'is_true': True,
        }
    elif point.is_detection and point.mag is not None:
        note = ('calibrated; target colour not applied' if color_term_used
                else 'calibrated; no colour term used')
        headline = {
            'label': filt_label,
            'value': mag_string(point),
            'note': note,
            'is_true': False,
        }
    else:
        headline = {
            'label': filt_label,
            'value': mag_string(point),
            'note': 'upper limit' if point.mag_limit is not None else '',
            'is_true': False,
        }

    return {
        'filt_label': filt_label,
        'band': band,
        'headline': headline,
        'mag_stdweb': point.mag,
        'magerr_stdweb': point.magerr,
        'mag_limit': point.mag_limit,
        'is_detection': point.is_detection,
        'k': k if color_term_used else None,
        'color_name': color_name,
        'color_term_used': color_term_used,
        'mode': mode,
        'mode_label': colorcomp.MODE_LABELS[mode],
        'has_colorcomp': cc is not None,
        'compensated_this': compensated_this,
        'compensated_all': compensated_all,
        'target_colour': target_colour,
        'colorcomp_steps': steps,
        'colorcomp_equation': steps['equation'] if steps else None,
        'triplet_ids': cc.get('task_ids') if cc else None,
        'sisters_status': sisters.get('status') if sisters else None,
        'sisters_computable': bool(sisters and sisters.get('status') == 'ok'),
    }


def _notification_measure(task, point):
    ctx = photometry_context(task, point, with_desc=False)
    steps = ctx.get('colorcomp_steps')
    if steps:
        band = steps['band']
        return (
            f'Gaia {band} = {steps["mag_true"]:.3f} ± {steps["magerr_true"]:.3f} mag'
            f' (true, colour-compensated; mag_cal = {steps["mag_cal"]:.3f}, k = {steps["k_str"]})'
        )
    return f'{mag_string(point)} {ctx["filt_label"]}'.rstrip()


def photometry_measure_lines(task, point):
    """Lines inserted into the telegram body between the epoch line and the coordinates."""
    ctx = photometry_context(task, point, with_desc=True, with_sisters=True)
    lines = []

    if ctx['has_colorcomp'] and ctx['compensated_this']:
        d = ctx['compensated_this']
        band = ctx['band'] or '?'
        col = ctx['target_colour']
        for ln in colorcomp_measure_lines(
            band, d, col,
            color_name=ctx.get('color_name') or '',
            triplet_ids=ctx.get('triplet_ids'),
            mode_label=ctx.get('mode_label') or '',
        ):
            lines.append(f'  {ln}')
        if ctx['compensated_all']:
            all_mags = ', '.join(
                f'{b} = {ctx["compensated_all"][b]["mag"]:.3f} ± {ctx["compensated_all"][b]["magerr"]:.3f}'
                for b in colorcomp.BANDS
                if b in ctx['compensated_all']
            )
            lines.append(f'  All bands (colour-compensated): {all_mags}')
    else:
        lines.append(f'  {ctx["filt_label"]} = {mag_string(point)} mag')
        if ctx['color_term_used']:
            cname = _format_color_name(ctx['color_name'])
            lines.append(f'  Calibration colour term: k = {ctx["k"]:.3f} ({cname} of field stars).')
            lines.append('  This magnitude does not include the colour of the target itself.')
            if ctx['sisters_computable'] and not ctx['has_colorcomp']:
                lines.append(
                    '  A G/BP/RP triplet is available on the task page; compute colour compensation'
                    ' there before publishing for true Gaia magnitudes.'
                )
            elif ctx['band'] in colorcomp.BANDS and not ctx['has_colorcomp']:
                lines.append(
                    '  Colour compensation requires G, BP and RP images the same night (see task page).'
                )
        else:
            lines.append('  Note: a colour term was not used in this photometry.')

    return lines, ctx


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

    tns_ok = bool(tns.get('matched') and tns.get('tns_name') and tns.get('on_target', True))
    ep_ok = bool(ep.get('matched') and ep.get('trigger'))

    if tns_ok:
        return {
            'object_name': tns['tns_name'][:120],
            'alert_url': tns.get('tns_url') or '',
            'alert_kind': 'tns',
            'alert_label': tns.get('obj_type') or tns['tns_name'],
            'discovery_iso': tns.get('discovery_iso') or '',
            'ep': ep if ep_ok else None,
            'tns': tns,
        }

    if ep_ok:
        obj = f"EP-WXT {ep['trigger']}"
        return {
            'object_name': obj[:120],
            'alert_url': ep.get('url') or '',
            'alert_kind': 'gcn',
            'alert_label': ep.get('subject') or obj,
            'discovery_iso': '',
            'ep': ep,
            'tns': tns if tns.get('matched') else None,
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


def coord_string(ra, dec):
    try:
        from astropy.coordinates import SkyCoord
        return SkyCoord(float(ra), float(dec), unit='deg').to_string(
            'hmsdms', precision=1, alwayssign=True,
        )
    except Exception:
        return f'{float(ra):.5f} {float(dec):+.5f}'


def draft_title(alert):
    obj = (alert or {}).get('object_name') or 'target'
    kind = (alert or {}).get('alert_kind')
    if kind == 'tns':
        return f'Optical photometry of {obj}'
    if kind == 'gcn':
        return f'Optical photometry of {obj}'
    return f'STDWeb photometry of {obj}'


def draft_body(task, point, user, request, alert):
    obs = observer_line(user)
    when = (point.time_iso or '')[:19].replace('T', ' ')
    mjd = f'{point.mjd:.5f}'
    obj = alert.get('object_name') or 'the target'
    task_link = task_url(request, task.id)
    alert_url = alert.get('alert_url') or ''
    window_hours = profile_match_params(user)[1]
    age = age_string(point, alert, window_hours)
    sky = coord_string(point.ra, point.dec)
    how = 'difference-image photometry' if point.is_diff else 'aperture photometry'
    measure_lines, _ctx = photometry_measure_lines(task, point)

    lines = [
        f'{obs} reports {how} of {obj} obtained with STDWeb.',
        '',
        f'On {when} UT (MJD {mjd}) we measure',
        *measure_lines,
        f'at {sky} (J2000).',
    ]
    if age:
        lines += ['', f'Age of the alert at this epoch: {age}.']
    lines += [
        '',
        'Photometry, logs and cutout:',
        task_link,
    ]
    if alert_url:
        kind = 'TNS'
        if alert.get('alert_kind') == 'gcn':
            kind = 'GCN'
        elif alert.get('alert_kind') and alert.get('alert_kind') != 'tns':
            kind = 'Alert'
        lines += ['', f'{kind}: {alert_url}']
    return '\n'.join(lines)


def cutout_relpath(task):
    base = task.path()
    for name in (
        'sub_target.cutout',
        'target.cutout',
        'targets/target_0000.cutout',
    ):
        if os.path.exists(os.path.join(base, name)):
            return name
    return None


def _mark_radii(task):
    config = task.config or {}
    try:
        fwhm = float(config.get('fwhm') or 0)
    except (TypeError, ValueError):
        fwhm = 0.0
    if fwhm <= 0:
        return 8.0, None, None
    try:
        r1 = fwhm * float(config.get('rel_aper') or 1.0)
    except (TypeError, ValueError):
        r1 = fwhm
    r2 = r3 = None
    try:
        if config.get('rel_bg1') is not None:
            r2 = fwhm * float(config['rel_bg1'])
        if config.get('rel_bg2') is not None:
            r3 = fwhm * float(config['rel_bg2'])
    except (TypeError, ValueError):
        pass
    return max(r1, 3.0), r2, r3


def _target_mark_radec(task, plane):
    """Sky position to mark on the cutout: the *measured* (centroided) target
    position from the photometry table, so the crosshair/aperture sits on the
    photocentre rather than the raw catalogue coordinates. Returns (None, None)
    if no measurement is available (caller falls back to target_ra/target_dec)."""
    import math
    try:
        from astropy.table import Table
    except Exception:
        return None, None
    base = task.path()
    names = ['sub_target.vot', 'target.vot'] if plane == 'diff' else ['target.vot', 'sub_target.vot']
    for name in names:
        p = os.path.join(base, name)
        if not os.path.exists(p):
            continue
        try:
            t = Table.read(p)
            if len(t) and 'ra' in t.colnames and 'dec' in t.colnames:
                ra, dec = float(t['ra'][0]), float(t['dec'][0])
                if math.isfinite(ra) and math.isfinite(dec):
                    return ra, dec
        except Exception:
            continue
    return None, None


def render_illustration(task):
    """PNG of the target cutout with the photometry aperture drawn on the pixels."""
    rel = cutout_relpath(task)
    if not rel:
        return None
    fullpath = os.path.join(task.path(), rel)
    try:
        import numpy as np
        from matplotlib.figure import Figure
        from matplotlib.patches import Circle
        from stdpipe import cutouts
    except Exception:
        return None

    try:
        cutout = cutouts.load_cutout(fullpath)
    except Exception:
        return None

    plane = 'diff' if rel.startswith('sub_target') and cutout.get('diff') is not None else 'image'
    if cutout.get(plane) is None:
        for name in ('image', 'diff', 'template', 'convolved'):
            if cutout.get(name) is not None:
                plane = name
                break
        else:
            return None

    arr = np.asarray(cutout[plane], dtype=float)
    config = task.config or {}
    # Mark where photometry was actually measured (the centroided target
    # position), not the raw catalogue coordinates, so the crosshair sits on
    # the photocentre. Fall back to the catalogue position if unavailable.
    ra, dec = _target_mark_radec(task, plane)
    if ra is None or dec is None:
        ra, dec = config.get('target_ra'), config.get('target_dec')
    mark_r, mark_r2, mark_r3 = _mark_radii(task)

    x = y = None
    if ra is not None and dec is not None and cutout.get('wcs') is not None:
        try:
            x, y = cutout['wcs'].all_world2pix(float(ra), float(dec), 0)
            x, y = float(x), float(y)
        except Exception:
            x = y = None
    if x is None:
        y, x = arr.shape[0] / 2.0, arr.shape[1] / 2.0

    fig = Figure(facecolor='white', dpi=96, figsize=(5.0, 5.4), tight_layout=True)
    ax = fig.add_subplot(1, 1, 1)
    good = np.isfinite(arr)
    if np.any(good):
        vmin, vmax = np.percentile(arr[good], [0.5, 99.5])
    else:
        vmin, vmax = 0, 1
    ax.imshow(arr, cmap='Blues_r', vmin=vmin, vmax=vmax, interpolation='nearest')
    ax.set_axis_off()
    color = '#ff3b30'
    ax.add_artist(Circle((x, y), mark_r, edgecolor=color, facecolor='none', lw=1.8))
    for radius in (mark_r2, mark_r3):
        if radius:
            ax.add_artist(Circle(
                (x, y), radius, edgecolor=color, facecolor='none', lw=0.9, ls='--',
            ))
    s = max(mark_r * 1.7, 10)
    ax.plot([x - s, x + s], [y, y], color=color, lw=1.2)
    ax.plot([x, x], [y - s, y + s], color=color, lw=1.2)
    title = 'Difference' if plane == 'diff' else 'Target'
    ax.set_title(title, fontsize=11)

    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    return buf.getvalue()
