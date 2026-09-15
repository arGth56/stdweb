"""Colour-compensated photometry from G / BP / RP sister tasks.

Convention (stdpipe.photometry.calibrate): ``mag_calib = mag_instr + ZP(x, y)`` with the
colour contribution left out, so the true magnitude of the target is

    mag = mag_calib + k * colour

where ``k`` is ``mag_color_term`` of that task and ``colour = BP - RP`` of the *target*.
Since BP and RP are themselves colour-dependent, the target colour is solved in closed form:

    colour = (BP_cal - RP_cal) / (1 - (k_BP - k_RP))

Everything here is deliberately strict: a triplet is only computed when every rule passes.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import datetime, timezone

from . import models

MATCH_ARCSEC = 5.0          # same target
DEFAULT_WINDOW_MIN = 90.0   # same epoch
MAX_MAGERR = 1.0 / 3.0      # detection at S/N >= 3 in every band
MIN_DENOM = 0.3             # |1 - (k_BP - k_RP)| must stay away from zero
BANDS = ('G', 'BP', 'RP')
MODES = {'direct': 'target.vot', 'sub': 'sub_target.vot'}
MODE_LABELS = {'direct': 'direct image', 'sub': 'template-subtracted'}
_BAND_OF_COLUMN = {'gmag': 'G', 'bpmag': 'BP', 'rpmag': 'RP'}


# --------------------------------------------------------------------------- #
# Small helpers                                                                 #
# --------------------------------------------------------------------------- #

def _finite(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def sep_arcsec(ra1, dec1, ra2, dec2):
    dra = (ra1 - ra2) * 3600.0 * math.cos(math.radians(0.5 * (dec1 + dec2)))
    ddec = (dec1 - dec2) * 3600.0
    return math.hypot(dra, ddec)


def _mjd(time_iso):
    if not time_iso:
        return None
    try:
        from astropy.time import Time
        return float(Time(str(time_iso)).mjd)
    except Exception:
        return None


def result_filename(mode):
    return f'color_compensated_{mode}.json'


def colour_free_notation(filter_name, band, k, color_name):
    """Label for a colour-free measurement, matching the task-page log notation.

    Example: ``Gmag + 0.21 (BPmag - RPmag)`` with k = −0.21 (stdpipe sign convention).
    """
    filt = (filter_name or '').strip() or (f'{band}mag' if band else '?')
    if k is None:
        if band:
            return f'{band} (colour term not applied)'
        return f'{filt} (colour term not applied)'
    cname = (color_name or '').strip() or 'BPmag - RPmag'
    try:
        from stdpipe import photometry
        return photometry.format_color_term(k, name=filt, color_name=cname)
    except Exception:
        sign = '-' if k > 0 else '+'
        return f'{filt} {sign} {abs(k):.2f} ({cname})'


# --------------------------------------------------------------------------- #
# Reading one task                                                              #
# --------------------------------------------------------------------------- #

def describe(task, mode):
    """Return everything the matching rules need about one task in one mode, or None
    if the task has no measurement file for that mode."""
    fname = MODES[mode]
    path = os.path.join(task.path(), fname)
    if not os.path.exists(path):
        return None
    try:
        from astropy.table import Table
        tbl = Table.read(path)
    except Exception:
        return None
    if len(tbl) == 0:
        return None
    row = tbl[0]

    def col(name):
        if name not in tbl.colnames:
            return None
        v = row[name]
        try:
            if hasattr(v, 'mask') and bool(v.mask):
                return None
        except Exception:
            pass
        return v

    config = task.config or {}
    filter_name = str(col('mag_filter_name') or config.get('cat_col_mag') or '')
    band = _BAND_OF_COLUMN.get(filter_name.lower())
    mag = _finite(col('mag_calib'))
    magerr = _finite(col('mag_calib_err'))
    ra = _finite(config.get('target_ra'))
    dec = _finite(config.get('target_dec'))
    time_iso = str(config.get('time') or '').strip()
    warnings = [
        w for w in (config.get('photometry_warnings') or [])
        if isinstance(w, dict) and w.get('check') == 'color_term'
    ]
    return {
        'id': task.id,
        'user_id': task.user_id,
        'title': (task.title or task.original_name or '')[:80],
        'state': task.state,
        'mode': mode,
        'band': band,
        'filter_name': filter_name,
        'cat_name': str(config.get('cat_name') or ''),
        'time_iso': time_iso,
        'mjd': _mjd(time_iso),
        'ra': ra,
        'dec': dec,
        'mag': mag,
        'magerr': magerr,
        'k': _finite(col('mag_color_term')),
        'color_name': str(col('mag_color_name') or ''),
        'colour_free_label': colour_free_notation(
            filter_name, band, _finite(col('mag_color_term')), str(col('mag_color_name') or ''),
        ),
        'detected': mag is not None and magerr is not None and 0 < magerr < MAX_MAGERR,
        'color_term_warnings': warnings,
    }


def available_modes(task):
    base = task.path()
    return [m for m, f in MODES.items() if os.path.exists(os.path.join(base, f))]


# --------------------------------------------------------------------------- #
# Finding sisters                                                               #
# --------------------------------------------------------------------------- #

def find_sisters(task, mode, window_min=DEFAULT_WINDOW_MIN):
    """Propose sister tasks for the two other bands. Never picks silently: each band is
    reported as ``ok`` (exactly one candidate), ``ambiguous`` (several) or ``missing``."""
    this = describe(task, mode)
    out = {'mode': mode, 'window_min': window_min, 'this': this, 'bands': {}, 'status': 'unavailable',
           'reasons': []}
    if this is None:
        out['reasons'].append(f'No {MODES[mode]} in this task.')
        return out
    if this['band'] is None:
        out['reasons'].append(f"Filter column '{this['filter_name']}' is not a Gaia G/BP/RP band.")
        return out
    if 'gaia' not in this['cat_name'].lower():
        out['reasons'].append(f"Reference catalogue is '{this['cat_name']}', not Gaia.")
        return out
    if this['ra'] is None or this['dec'] is None or this['mjd'] is None:
        out['reasons'].append('This task has no target position or observation time.')
        return out

    ra, dec = this['ra'], this['dec']
    pad = 0.02  # deg, coarse DB pre-filter; refined below with the 5" rule
    qs = (
        models.Task.objects
        .filter(user_id=task.user_id,
                config__target_ra__gte=ra - pad, config__target_ra__lte=ra + pad,
                config__target_dec__gte=dec - pad, config__target_dec__lte=dec + pad)
        .exclude(id=task.id)
        .order_by('-created')
    )

    wanted = [b for b in BANDS if b != this['band']]
    bands = {b: {'status': 'missing', 'candidates': [], 'nearest_outside': None} for b in wanted}

    for other in qs:
        d = describe(other, mode)
        if d is None or d['band'] not in wanted:
            continue
        if d['ra'] is None or d['dec'] is None or d['mjd'] is None:
            continue
        if d['cat_name'] != this['cat_name']:
            continue
        sep = sep_arcsec(ra, dec, d['ra'], d['dec'])
        if sep > MATCH_ARCSEC:
            continue
        dt_min = (d['mjd'] - this['mjd']) * 1440.0
        d = dict(d, sep_arcsec=round(sep, 2), dt_min=round(dt_min, 1))
        slot = bands[d['band']]
        if abs(dt_min) <= window_min:
            slot['candidates'].append(d)
        else:
            near = slot['nearest_outside']
            if near is None or abs(dt_min) < abs(near['dt_min']):
                slot['nearest_outside'] = d

    for b, slot in bands.items():
        slot['candidates'].sort(key=lambda c: abs(c['dt_min']))
        if slot['candidates']:
            slot['candidates'][0]['nearest'] = True
        n = len(slot['candidates'])
        slot['status'] = 'ok' if n == 1 else ('ambiguous' if n > 1 else 'missing')

    statuses = [s['status'] for s in bands.values()]
    if all(s == 'ok' for s in statuses):
        out['status'] = 'ok'
    elif 'missing' in statuses:
        out['status'] = 'missing'
    else:
        out['status'] = 'ambiguous'
    out['bands'] = bands
    return out


# --------------------------------------------------------------------------- #
# Validating a triplet                                                          #
# --------------------------------------------------------------------------- #

def validate_triplet(ids, mode, window_min=DEFAULT_WINDOW_MIN):
    """``ids`` maps band -> task id. Returns (ok, reasons, descs). Every violated rule
    gives one reason with the offending values, so the user sees exactly why."""
    reasons = []
    descs = {}

    if mode not in MODES:
        return False, [f"Unknown mode '{mode}'."], descs

    clean = {}
    for b in BANDS:
        try:
            clean[b] = int(ids.get(b))
        except (TypeError, ValueError):
            reasons.append(f'{b}: no task selected.')
    if reasons:
        return False, reasons, descs
    if len(set(clean.values())) != 3:
        reasons.append('The three tasks must be distinct.')
        return False, reasons, descs

    for b, tid in clean.items():
        task = models.Task.objects.filter(id=tid).first()
        if task is None:
            reasons.append(f'{b}: task {tid} does not exist.')
            continue
        d = describe(task, mode)
        if d is None:
            reasons.append(f'{b}: task {tid} has no {MODES[mode]} ({MODE_LABELS[mode]} mode).')
            continue
        descs[b] = d
    if reasons:
        return False, reasons, descs

    g = descs['G']
    for b, d in descs.items():
        if d['band'] != b:
            reasons.append(f"{b}: task {d['id']} is a {d['band'] or d['filter_name'] or 'non-Gaia'} measurement, not {b}.")
        if d['user_id'] != g['user_id']:
            reasons.append(f"{b}: task {d['id']} belongs to another observer.")
        if 'gaia' not in d['cat_name'].lower():
            reasons.append(f"{b}: task {d['id']} was calibrated against '{d['cat_name']}', not Gaia.")
        elif d['cat_name'] != g['cat_name']:
            reasons.append(f"{b}: catalogue '{d['cat_name']}' differs from G task '{g['cat_name']}'.")
        if d['ra'] is None or d['dec'] is None:
            reasons.append(f"{b}: task {d['id']} has no target position.")
        if d['mjd'] is None:
            reasons.append(f"{b}: task {d['id']} has no observation time.")
    if reasons:
        return False, reasons, descs

    for b in ('BP', 'RP'):
        d = descs[b]
        sep = sep_arcsec(g['ra'], g['dec'], d['ra'], d['dec'])
        d['sep_arcsec'] = round(sep, 2)
        if sep > MATCH_ARCSEC:
            shown = f'{sep:.1f}"' if sep < 600 else f'{sep / 3600.0:.2f} deg'
            reasons.append(f"{b}: task {d['id']} target is {shown} from the G target (max {MATCH_ARCSEC:g}\") — different object.")
        dt = (d['mjd'] - g['mjd']) * 1440.0
        d['dt_min'] = round(dt, 1)
    g['sep_arcsec'] = 0.0
    g['dt_min'] = 0.0
    mjds = [d['mjd'] for d in descs.values()]
    dt_max = (max(mjds) - min(mjds)) * 1440.0
    if dt_max > window_min:
        reasons.append(f'Observations span {dt_max:.0f} min (max {window_min:g} min).')

    for b, d in descs.items():
        if not d['detected']:
            if d['mag'] is None or d['magerr'] is None:
                reasons.append(f"{b}: task {d['id']} has no measured magnitude (not detected).")
            else:
                reasons.append(f"{b}: task {d['id']} error {d['magerr']:.2f} mag is above {MAX_MAGERR:.2f} (S/N < 3).")
        if d['k'] is None:
            reasons.append(f"{b}: task {d['id']} has no colour term (calibrated without colour).")
    if reasons:
        return False, reasons, descs

    denom = 1.0 - (descs['BP']['k'] - descs['RP']['k'])
    if abs(denom) < MIN_DENOM:
        reasons.append(f'Colour terms are degenerate: 1 - (k_BP - k_RP) = {denom:.2f} (|value| must be >= {MIN_DENOM}).')
        return False, reasons, descs

    return True, [], descs


# --------------------------------------------------------------------------- #
# Computing                                                                     #
# --------------------------------------------------------------------------- #

def compensate(descs, mode, window_min=DEFAULT_WINDOW_MIN, user=None):
    """Closed-form colour compensation of a validated triplet."""
    g, bp, rp = descs['G'], descs['BP'], descs['RP']
    denom = 1.0 - (bp['k'] - rp['k'])
    colour = (bp['mag'] - rp['mag']) / denom
    colour_err = math.hypot(bp['magerr'], rp['magerr']) / abs(denom)

    bands = {}
    for b, d in descs.items():
        corr = d['k'] * colour
        bands[b] = {
            'task_id': d['id'],
            'time_iso': d['time_iso'],
            'mag_cal': round(d['mag'], 3),
            'magerr_cal': round(d['magerr'], 3),
            'k': round(d['k'], 3),
            'correction': round(corr, 3),
            'mag': round(d['mag'] + corr, 3),
            'magerr': round(math.hypot(d['magerr'], d['k'] * colour_err), 3),
        }

    checks = []
    G, BP, RP = bands['G']['mag'], bands['BP']['mag'], bands['RP']['mag']
    if RP <= G <= BP:
        checks.append({'level': 'success',
                       'message': f'Physical order respected: RP {RP:.2f} <= G {G:.2f} <= BP {BP:.2f}.'})
    else:
        checks.append({'level': 'danger',
                       'message': f'Physically impossible ordering: RP {RP:.2f}, G {G:.2f}, BP {BP:.2f} '
                                  f'(G must be at least as bright as BP and no brighter than RP). '
                                  f'Check the subtraction or the individual calibrations.'})
    for b, d in descs.items():
        for w in d['color_term_warnings']:
            checks.append({'level': 'warning',
                           'message': f"{b} (task {d['id']}): {w.get('message', 'colour term anomaly')}"})
    if abs(denom) < 0.6:
        checks.append({'level': 'warning',
                       'message': f'Colour solution is poorly conditioned (1 - (k_BP - k_RP) = {denom:.2f}); '
                                  f'errors may be underestimated.'})

    mjds = [d['mjd'] for d in descs.values()]
    return {
        'version': 1,
        'mode': mode,
        'mode_label': MODE_LABELS[mode],
        'window_min': window_min,
        'computed_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'computed_by': getattr(user, 'username', '') or '',
        'source_task': g['id'],
        'task_ids': {b: d['id'] for b, d in descs.items()},
        'target_ra': g['ra'],
        'target_dec': g['dec'],
        'mjd': round(sum(mjds) / 3.0, 6),
        'dt_max_min': round((max(mjds) - min(mjds)) * 1440.0, 1),
        'sep_max_arcsec': round(max(d.get('sep_arcsec', 0.0) for d in descs.values()), 2),
        'colour': {'value': round(colour, 3), 'err': round(colour_err, 3), 'denominator': round(denom, 3)},
        'bands': bands,
        'checks': checks,
        'note': 'Errors propagate mag_calib_err only; the uncertainty of the colour terms themselves is not included.',
    }


def save_result(result):
    """Write the same result file into the three task directories so every sister page shows it."""
    written = []
    for tid in result['task_ids'].values():
        task = models.Task.objects.filter(id=tid).first()
        if task is None or not os.path.isdir(task.path()):
            continue
        path = os.path.join(task.path(), result_filename(result['mode']))
        with open(path, 'w') as f:
            json.dump(result, f, indent=1)
        written.append(path)
    return written


def load_result(task, mode):
    path = os.path.join(task.path(), result_filename(mode))
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def result_csv(result):
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ['mode', 'mjd', 'time_G', 'task_G', 'task_BP', 'task_RP', 'BP-RP', 'BP-RP_err']
    row = [result['mode'], result['mjd'], result['bands']['G']['time_iso'],
           result['task_ids']['G'], result['task_ids']['BP'], result['task_ids']['RP'],
           result['colour']['value'], result['colour']['err']]
    for b in BANDS:
        d = result['bands'][b]
        header += [f'{b}_cal', f'{b}_cal_err', f'k_{b}', f'{b}', f'{b}_err']
        row += [d['mag_cal'], d['magerr_cal'], d['k'], d['mag'], d['magerr']]
    header += ['checks']
    row += ['; '.join(f"{c['level']}: {c['message']}" for c in result['checks'])]
    w.writerow(header)
    w.writerow(row)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Panel for the task page                                                       #
# --------------------------------------------------------------------------- #

def build_panel(task, mode=None, window_min=DEFAULT_WINDOW_MIN, manual_ids=None):
    """Everything the template needs. ``manual_ids`` (band -> id) triggers a dry-run
    validation whose reasons are displayed without computing anything."""
    modes = available_modes(task)
    if not modes:
        return None
    if mode not in modes:
        mode = 'sub' if 'sub' in modes else 'direct'

    sisters = find_sisters(task, mode, window_min)
    this = sisters.get('this')

    # Pre-fill the form: this task in its own slot, unique candidates in theirs.
    selected = {}
    if this and this.get('band'):
        selected[this['band']] = this['id']
    for b, slot in sisters.get('bands', {}).items():
        if slot['status'] == 'ok':
            selected[b] = slot['candidates'][0]['id']

    # Dry-run the auto-detected triplet so the button is greyed out *with the reason*
    # (e.g. a sister without detection) instead of refusing after the click.
    auto_ok, auto_reasons = False, []
    if sisters['status'] == 'ok':
        auto_ok, auto_reasons, _ = validate_triplet(selected, mode, window_min)

    manual = None
    if manual_ids:
        ok, reasons, descs = validate_triplet(manual_ids, mode, window_min)
        manual = {'ids': {b: manual_ids.get(b) for b in BANDS}, 'ok': ok, 'reasons': reasons,
                  'descs': descs}
        if ok:
            selected = {b: d['id'] for b, d in descs.items()}

    result = load_result(task, mode)
    this_result = None
    if result and this and this.get('band'):
        this_result = result.get('bands', {}).get(this['band'])

    return {
        'modes': [{'key': m, 'label': MODE_LABELS[m], 'active': m == mode,
                   'has_result': load_result(task, m) is not None} for m in modes],
        'mode': mode,
        'mode_label': MODE_LABELS[mode],
        'window_min': window_min,
        'match_arcsec': MATCH_ARCSEC,
        'this': this,
        'status': sisters['status'],
        'reasons': sisters.get('reasons', []),
        'bands': [dict(band=b, **sisters['bands'][b]) for b in BANDS if b in sisters.get('bands', {})],
        'auto_ok': auto_ok,
        'auto_reasons': auto_reasons,
        'can_compute': auto_ok or (manual is not None and manual['ok']),
        'selected': selected,
        'manual': manual,
        'result': result,
        'this_result': this_result,
    }
