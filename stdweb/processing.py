from django.conf import settings

import os, glob, shutil

from functools import partial

import numpy as np

from astropy.wcs import WCS
from astropy.io import fits as fits

from astropy.table import Table, vstack
from astropy.stats import mad_std
from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.time import Time

import astroscrappy
import time

import sep

import reproject

import dill as pickle

# STDPipe
from stdpipe import astrometry, photometry, catalogs, cutouts
from stdpipe import templates, subtraction, plots, pipeline, utils, psf
from stdpipe import resolve

# Disable some annoying warnings from astropy
import warnings
from astropy.wcs import FITSFixedWarning
from astropy.io.fits.verify import VerifyWarning
warnings.simplefilter(action='ignore', category=FITSFixedWarning)
warnings.simplefilter(action='ignore', category=VerifyWarning)


def _coerce_sn(config, default=5.0):
    """config['sn'] is a numeric S/N cut, not a supernova name."""
    try:
        v = float(config.get('sn'))
    except (TypeError, ValueError):
        v = default
    if not np.isfinite(v) or v == 0:
        v = default
    config['sn'] = v


def _fits_pointing(header):
    """Return (ra_deg, dec_deg) from FITS pointing keywords, or None."""
    ra = header.get('RA')
    dec = header.get('DEC')
    try:
        if ra is not None and dec is not None:
            return float(ra), float(dec)
    except (TypeError, ValueError):
        pass
    ra_s = header.get('OBJCTRA')
    dec_s = header.get('OBJCTDEC')
    if ra_s and dec_s:
        try:
            sc = SkyCoord(str(ra_s).strip(), str(dec_s).strip(), unit=(u.hourangle, u.deg))
            return sc.ra.deg, sc.dec.deg
        except Exception:
            pass
    return None


# Supported filters and their aliases
supported_filters = {
    # Johnson-Cousins
    'U': {'name':'Johnson-Cousins U', 'aliases':[]},
    'B': {'name':'Johnson-Cousins B', 'aliases':[]},
    'V': {'name':'Johnson-Cousins V', 'aliases':[]},
    'R': {'name':'Johnson-Cousins R', 'aliases':["Rc"]},
    'I': {'name':'Johnson-Cousins I', 'aliases':["Ic", "I'"]},
    # Sloan-like
    'u': {'name':'Sloan u', 'aliases':["sdssu", "SDSS u", "SDSS-u", "SDSS-u'", "Sloan-u", "sloanu", "Sloan u", "Su", "SU", "sU"]},
    'g': {'name':'Sloan g', 'aliases':["sdssg", "SDSS g", "SDSS-g", "SDSS-g'", "Sloan-g", "sloang", "Sloan g", "Sg", "SG", "sG", "ZTF_g"]},
    'r': {'name':'Sloan r', 'aliases':["sdssr", "SDSS r", "SDSS-r", "SDSS-r'", "Sloan-r", "sloanr", "Sloan r", "Sr", "SR", "sR", "ZTF_r"]},
    'i': {'name':'Sloan i', 'aliases':["sdssi", "SDSS i", "SDSS-i", "SDSS-i'", "Sloan-i", "sloani", "Sloan i", "Si", "SI", "sI", "ZTF_i"]},
    'z': {'name':'Sloan z', 'aliases':["sdssz", "SDSS z", "SDSS-z", "SDSS-z'", "Sloan-z", "sloanz", "Sloan z", "Sz", "SZ", "sZ"]},
    # Gaia
    'G': {'name':'Gaia G', 'aliases':[]},
    'BP': {'name':'Gaia BP', 'aliases':[]},
    'RP': {'name':'Gaia RP', 'aliases':[]},
}

supported_catalogs = {
    'gaiadr3syn': {'name':'Gaia DR3 synphot', 'filters':['U', 'B', 'V', 'R', 'I', 'u', 'g', 'r', 'i', 'z', 'y'],
                   'limit': 'rmag'},
    'ps1': {'name':'Pan-STARRS DR1', 'filters':['B', 'V', 'R', 'I', 'g', 'r', 'i', 'z'],
            'limit':'rmag'},
    'skymapper': {'name':'SkyMapper DR4', 'filters':['B', 'V', 'R', 'I', 'g', 'r', 'i', 'z'],
                  'limit':'rPSF'},
    'sdss': {'name':'SDSS DR16', 'filters':['u', 'g', 'r', 'i', 'z'],
             'limit':'rmag'},
    'atlas': {'name':'ATLAS-REFCAT2', 'filters':['B', 'V', 'R', 'I', 'g', 'r', 'i', 'z'],
              'limit':'rmag'},
    'gaiaedr3': {'name':'Gaia eDR3', 'filters':['G', 'BP', 'RP'],
              'limit':'Gmag'},
}

supported_catalogs_transients = {
    **supported_catalogs,
    'II/371/des_dr2': {'name':'DES DR2', 'filters':['g', 'r', 'i', 'z'],
            'limit': 'rmag'},
}

supported_templates = {
    'custom': {'name': 'Custom template'},
    'ps1': {'name': 'Pan-STARRS DR2', 'filters': {'g', 'r', 'i', 'z'}},
    'ls': {'name': 'Legacy Survey DR10', 'filters': {'g', 'r', 'i', 'z'}},
    'skymapper': {'name': 'SkyMapper DR1 (HiPS)', 'filters': {
        'u': 'CDS/P/skymapper-U',
        'g': 'CDS/P/skymapper-G',
        'r': 'CDS/P/skymapper-R',
        'i': 'CDS/P/skymapper-I',
        'z': 'CDS/P/skymapper-Z',
    }},
    'des': {'name': 'Dark Energy Survey DR2 (HiPS)', 'filters': {
        'g': 'CDS/P/DES-DR2/g',
        'r': 'CDS/P/DES-DR2/r',
        'i': 'CDS/P/DES-DR2/i',
        'z': 'CDS/P/DES-DR2/z',
    }},
    # 'legacy': {'name': 'DESI Legacy Surveys DR10 (HiPS)', 'filters': {
    #     'g': 'CDS/P/DESI-Legacy-Surveys/DR10/g',
    #     'i': 'CDS/P/DESI-Legacy-Surveys/DR10/i',
    # }},
    'decaps': {'name': 'DECaPS DR2 (HiPS)', 'filters': {
        'g': 'CDS/P/DECaPS/DR2/g',
        'r': 'CDS/P/DECaPS/DR2/r',
        'i': 'CDS/P/DECaPS/DR2/i',
        'z': 'CDS/P/DECaPS/DR2/z',
    }},
    'ztf': {'name': 'ZTF DR7 (HiPS)', 'filters': {
        'g': 'CDS/P/ZTF/DR7/g',
        'r': 'CDS/P/ZTF/DR7/r',
        'i': 'CDS/P/ZTF/DR7/i',
    }},
}

# Best guess template filter mappings
filter_mappings = {
    'U': ['u', 'g'],
    'B': ['u', 'g'],
    'V': ['g'],
    'R': ['r', 'i'],
    'I': ['i', 'z'],
    'u': ['u', 'g'],
    'g': ['g', 'g'],
    'r': ['r', 'i'],
    'i': ['i', 'r'],
    'z': ['z', 'r'],
    'G': ['r', 'g'],
    'BP': ['g'],
    'RP': ['i', 'r'],
}


# Conversion to AB mags, from https://www.astronomy.ohio-state.edu/martini.10/usefuldata.html
filter_ab_offset = {
    'U': 0.79,
    'B': -0.09,
    'V': 0.02,
    'R': 0.21,
    'I': 0.45,
    'u': 0,
    'g': 0,
    'r': 0,
    'i': 0,
    'z': 0,
    'G': 0,
    'BP': 0,
    'RP': 0,
}


# Files created at every step

files_inspect = [
    'inspect.log',
    'mask.fits', 'image_target.fits',
]

files_photometry = [
    'photometry.log',
    'objects.png', 'fwhm.png',
    'segmentation.fits',
    'image_bg.fits', 'image_rms.fits',
    'photometry.png', 'photometry_unmasked.png',
    'photometry_zeropoint.png', 'photometry_model.png',
    'photometry_residuals.png', 'astrometry_dist.png',
    'photometry.pickle',
    'objects.vot', 'cat.vot',
    'limit_hist.png', 'limit_sn.png',
    'target.vot', 'target.cutout', 'targets'
]

files_transients_simple = [
    'transients_simple.log',
    'candidates_simple', 'candidates_simple.vot'
]

files_subtraction = [
    'subtraction.log',
    'sub_image.fits', 'sub_mask.fits',
    'sub_template.fits', 'sub_template_mask.fits',
    'sub_diff.fits', 'sub_sdiff.fits', 'sub_conv.fits', 'sub_ediff.fits',
    'sub_scorr.fits', 'sub_fpsf.fits', 'sub_fpsferr.fits',
    'sub_target.vot', 'sub_target.cutout',
    'candidates', 'candidates.vot'
]

cleanup_inspect = files_inspect + files_photometry + files_transients_simple + files_subtraction

cleanup_photometry = files_photometry + files_transients_simple + files_subtraction

cleanup_transients_simple = files_transients_simple

cleanup_subtraction = files_subtraction

def cleanup_paths(paths, basepath=None):
    for path in paths:
        fullpath = os.path.join(basepath, path)
        if os.path.exists(fullpath):
            if os.path.isdir(fullpath):
                shutil.rmtree(fullpath)
            else:
                os.unlink(fullpath)


def print_to_file(*args, clear=False, logname='out.log', **kwargs):
    if clear and os.path.exists(logname):
        print('Clearing', logname)
        os.unlink(logname)

    if len(args) or len(kwargs):
        print(*args, **kwargs)
        with open(logname, 'a+') as lfd:
            print(file=lfd, *args, **kwargs)


def pickle_to_file(filename, obj):
    with open(filename, 'wb') as f:
        pickle.dump(obj, f)


def pickle_from_file(filename):
    with open(filename, 'rb') as f:
        return pickle.load(f)


def fits_write(filename, image, header=None, compress=False):
    """Store image with or without header to FITS file, with or without compression"""
    if compress:
        hdu = fits.CompImageHDU(image, header)
    else:
        hdu = fits.PrimaryHDU(image, header)

    hdu.writeto(filename, overwrite=True)


def get_wcs(filename, header=None, verbose=True):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    # Load the WCS from separate file, if it exists
    if os.path.exists(os.path.join(basepath, "image.wcs")):
        wcs = WCS(fits.getheader(os.path.join(basepath, "image.wcs")))

        if header is not None:
            # Update the header in-place with this new solution
            astrometry.clear_wcs(header)
            header += wcs.to_header(relax=True) # in-place update

        log("WCS loaded from file:image.wcs")
    else:
        if header is None:
            header = fits.getheader(filename, -1)
        wcs = WCS(header)
        log("Using original WCS from FITS header")

    return wcs


def _wcs_match_count(obj, cat, wcs, sr_deg, cat_col_ra='RAJ2000', cat_col_dec='DEJ2000'):
    """How many detections land on catalogue stars within `sr_deg`."""
    if (
        wcs is None
        or not getattr(wcs, 'is_celestial', False)
        or obj is None
        or cat is None
        or not cat_col_ra
        or not cat_col_dec
        or cat_col_ra not in getattr(cat, 'colnames', [])
        or cat_col_dec not in getattr(cat, 'colnames', [])
    ):
        return 0, None
    try:
        ra, dec = wcs.all_pix2world(np.asarray(obj['x'], float), np.asarray(obj['y'], float), 0)
        _, _, dist = astrometry.spherical_match(
            ra, dec,
            np.asarray(cat[cat_col_ra], float),
            np.asarray(cat[cat_col_dec], float),
            sr_deg,
        )
    except Exception:
        return 0, None
    n = int(len(dist))
    if n == 0:
        return 0, None
    return n, float(np.median(dist) * 3600.0)


def _wcs_as_tpv(wcs, obj=None, cat=None, sr_deg=None, cat_col_ra=None, cat_col_dec=None, log=None):
    """SIP → TPV without a SCAMP re-fit, for SWarp. Keep SIP if conversion wrecks matches."""
    log = log or (lambda *args, **kwargs: None)
    if wcs is None or not getattr(wcs, 'is_celestial', False) or getattr(wcs, 'sip', None) is None:
        return wcs
    try:
        wcs2 = WCS(astrometry.wcs_sip2pv(wcs.to_header(relax=True)))
    except Exception as exc:
        log(f"SIP to TPV conversion failed ({exc}); keeping SIP WCS")
        return wcs
    if not wcs2.is_celestial:
        return wcs
    if obj is not None and cat is not None and sr_deg:
        n_sip, _ = _wcs_match_count(obj, cat, wcs, sr_deg, cat_col_ra, cat_col_dec)
        n_tpv, _ = _wcs_match_count(obj, cat, wcs2, sr_deg, cat_col_ra, cat_col_dec)
        if n_sip >= 15 and n_tpv < 0.5 * n_sip:
            log(f"SIP to TPV dropped matches {n_sip} → {n_tpv}; keeping SIP WCS")
            return wcs
    log("Converted SIP WCS to TPV for SWarp (no SCAMP re-fit)")
    return wcs2


def _fmt_match_stats(n, med):
    if med is None:
        return f"{n} matches"
    return f"{n} matches, median {med:.2f} arcsec"


def fix_header(header, verbose=True):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    # Fix FITS standard errors in the header
    for _ in header.cards:
        _.verify('silentfix')
        __ = str(_) # it runs self.image()

    # Fix SCAMP headers with TAN type what are actually TPV (and thus break AstroPy WCS)
    if header.get('CTYPE1') == 'RA---TAN' and 'PV1_5' in header.keys():
        header['CTYPE1'] = 'RA---TPV'
        header['CTYPE2'] = 'DEC--TPV'

    # Fix PRISM headers
    if header.get('CTYPE2') == 'DEC---TAN':
        header['CTYPE2'] = 'DEC--TAN'
    for _ in ['CDELTM1', 'CDELTM2', 'XPIXELSZ', 'YPIXELSZ']:
        header.remove(_, ignore_missing=True)
    if header.get('CTYPE1') == 'RA---TAN':
        for _ in ['PV1_1', 'PV1_2']:
            header.remove(_, ignore_missing=True)

    # Fix some IRAF stuff that breaks astropy WCS
    for _ in ['WCSDIM', 'LTM1_1', 'LTM2_2', 'WAT0_001', 'WAT1_001', 'WAT2_001']:
        header.remove(_, ignore_missing=True)

    # Ensure WCS keywords are numbers, not strings
    for kw in ['CRVAL1', 'CRVAL2', 'CRPIX1', 'CRPIX2', 'CD1_1', 'CD1_2', 'CD2_1', 'CD2_2', 'CDELT1', 'CDELT2']:
        if kw in header:
            header[kw] = float(header[kw])

    if 'FOCALLEN' in header and not header.get('FOCALLEN'):
        header.remove('FOCALLEN')


def pre_fix_image(filename, verbose=True):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    # First check for compressed data
    hdus = fits.open(filename)
    if len(hdus) > 1:
        for i,hdu in enumerate(hdus):
            if hdu.is_image and len(hdu.shape) == 2:
                log(f"Keeping first usable plane ({i}: {hdu.name}) from multi-extension or tile compressed image")
                fits.writeto(filename, hdu.data, hdu.header, overwrite=True)
                break

    hdus.close()

    # Handle various special cases
    image,header = fits.getdata(filename), fits.getheader(filename)
    if 'BSOFTEN' in header and 'BOFFSET' in header:
        # Pan-STARRS image in ASINH scaling, let's convert it to linear
        log('Detected Pan-STARRS ASINH scaled image, fixing it')
        image,header = templates.normalize_ps1_skycell(image, header, verbose=verbose)
        fits.writeto(filename, image, header, overwrite=True)


def fix_image(filename, config, verbose=True):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    image,header = fits.getdata(filename, -1), fits.getheader(filename, -1)
    fix_header(header)

    if os.path.exists(os.path.join(basepath, 'image.wcs')):
        wcs = WCS(fits.getheader(os.path.join(basepath, "image.wcs")))
        astrometry.clear_wcs(header)
        header += wcs.to_header(relax=True)
        header.add_comment('WCS updated by STDWeb', before='WCSAXES')

    # Write fixed image and header back
    fits.writeto(filename, image, header, overwrite=True)


def crop_image(filename, config, x1=None, y1=None, x2=None, y2=None, verbose=True):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    image,header = fits.getdata(filename, -1), fits.getheader(filename, -1)
    fix_header(header)

    # Sanitize input
    try:
        x1 = int(x1)
    except:
        x1 = 0

    try:
        y1 = int(y1)
    except:
        y1 = 0

    try:
        x2 = int(x2)
    except:
        x2 = image.shape[1]

    try:
        y2 = int(y2)
    except:
        y2 = image.shape[0]

    # Interpret negative values as offsets from the top
    if x1 < 0:
        x1 = image.shape[1] + x1
    if x2 < 0:
        x2 = image.shape[1] + x2
    if y1 < 0:
        y1 = image.shape[0] + y1
    if y2 < 0:
        y2 = image.shape[0] + y2

    # Ensure we are within the image
    x1,x2 = max(0, x1), min(image.shape[1], x2)
    y1,y2 = max(0, y1), min(image.shape[0], y2)

    image,header = cutouts.crop_image(image, x1, y1, x2 - x1, y2 - y1, header=header)

    # Write cropped image and header back
    fits.writeto(filename, image, header, overwrite=True)


from astropy_healpix import healpy
def round_coords_to_grid(ra0, dec0, sr0, nside=None):
    """Tries to round the coordinates to nearest HEALPix pixel center"""
    if nside is None:
        for n in range(1, 16):
            nside = 2**n
            res = healpy.nside_to_pixel_resolution(nside).to('deg').value
            if res < 0.05*sr0:
                break
    else:
        res = healpy.nside_to_pixel_resolution(nside).to('deg').value

    ipix = healpy.ang2pix(nside, ra0, dec0, lonlat=True)
    ra1,dec1 = healpy.pix2ang(nside, ipix, lonlat=True)
    sr1 = (np.floor(sr0/res) + 1)*res

    return ra1, dec1, sr1


def guess_hips_survey(ra, dec, filter_name='R'):
    survey_filter = filter_mappings.get(filter_name, 'r')[0]

    # TODO: add Legacy Survey?..

    if dec > -30:
        if survey_filter == 'u':
            survey_filter = 'g'

        survey = f"PanSTARRS/DR1/{survey_filter}"

    else:
        survey = f"CDS/P/skymapper-{survey_filter.upper()}"

    return survey


def guess_vizier_catalogues(ra, dec):
    vizier = ['gaiaedr3'] # All-sky

    if dec > -30:
        vizier.append('ps1')

    if dec < 0:
        vizier.append('skymapper')

    return vizier


def guess_catalogue_mag_columns(fname, cat):
    cat_col_mag = None
    cat_col_mag_err = None

    # Cross-match helpers may return None on failures or no rows.
    if cat is None:
        return cat_col_mag, cat_col_mag_err

    # Most of augmented catalogues
    if f"{fname}mag" in cat.colnames:
        cat_col_mag = f"{fname}mag"

        if f"e_{fname}mag" in cat.colnames:
            cat_col_mag_err = f"e_{fname}mag"

    # Non-augmented PS1 etc
    elif "gmag" in cat.colnames and "rmag" in cat.colnames:
        if fname in ['U', 'B', 'V', 'BP']:
            cat_col_mag = "gmag"
        if fname in ['R', 'G']:
            cat_col_mag = "rmag"
        if fname in ['I', 'RP']:
            cat_col_mag = "imag"

        if f"e_{cat_col_mag}" in cat.colnames:
            cat_col_mag_err = f"e_{cat_col_mag}"

    # SkyMapper
    elif f"{fname}PSF" in cat.colnames:
        cat_col_mag = f"{fname}PSF"

        if f"e_{fname}PSF" in cat.colnames:
            cat_col_mag_err = f"e_{fname}PSF"

    # Gaia DR2/eDR3/DR3 from Vizier
    elif "BPmag" in cat.colnames and "RPmag" in cat.colnames and "Gmag" in cat.colnames:
        if fname in ['U', 'B', 'V', 'R', 'u', 'g', 'r', 'BP']:
            cat_col_mag = "BPmag"
        elif fname in ['I', 'i', 'z', 'RP']:
            cat_col_mag = "RPmag"
        else:
            cat_col_mag = "Gmag"

        if f"e_{cat_col_mag}" in cat.colnames:
            cat_col_mag_err = f"e_{cat_col_mag}"

    # Gaia DR2/eDR3/DR3 from XMatch
    elif "phot_bp_mean_mag" in cat.colnames and "phot_rp_mean_mag" in cat.colnames and "phot_g_mean_mag" in cat.colnames:
        if fname in ['U', 'B', 'V', 'R', 'u', 'g', 'r', 'BP']:
            cat_col_mag = "phot_bp_mean_mag"
        elif fname in ['I', 'i', 'z', 'RP']:
            cat_col_mag = "phot_rp_mean_mag"
        else:
            cat_col_mag = "phot_g_mean_mag"

        if f"{cat_col_mag}_error" in cat.colnames:
            cat_col_mag_err = f"{cat_col_mag}_error"

    # else:
    #     raise RuntimeError(f"Unsupported filter {fname} and/or catalogue")

    return cat_col_mag, cat_col_mag_err


def guess_catalogue_radec_columns(cat):
    cat_col_ra = None
    cat_col_dec = None

    # Find relevant coordinate columns
    if 'RAJ2000' in cat.keys():
        cat_col_ra = 'RAJ2000'
        cat_col_dec = 'DEJ2000'

    elif '_RAJ2000' in cat.keys():
        cat_col_ra = '_RAJ2000'
        cat_col_dec = '_DEJ2000'

    elif 'RA_ICRS' in cat.keys():
        cat_col_ra = 'RA_ICRS'
        cat_col_dec = 'DE_ICRS'

    # SkyMapper 1.1
    elif 'RAICRS' in cat.keys():
        cat_col_ra = 'RAICRS'
        cat_col_dec = 'DEICRS'

    # SkyMapper 4
    elif 'RAdeg' in cat.keys():
        cat_col_ra = 'RAdeg'
        cat_col_dec = 'DEdeg'

    # cross-match with Gaia eDR3
    elif 'ra_2' in cat.keys():
        cat_col_ra = 'ra_2'
        cat_col_dec = 'dec_2'

    # else:
    #     raise RuntimeError(f"Cannot find coordinate columns for the catalogue")

    return cat_col_ra, cat_col_dec


from sklearn.ensemble import IsolationForest

def filter_sextractor_detections(obj, verbose=True, classifier=None, return_classifier=False):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    var1,label1 = obj['FLUX_RADIUS'], 'FLUX_RADIUS'
    var2,label2 = obj['fwhm'], 'FWHM'
    var3,label3 = obj['mag']-obj['MAG_AUTO'], 'MAG_APER - MAG_AUTO'

    log("Using isolation forest outline detection over columns ({})".format(
        ", ".join([label1, label2, label3])
    ))

     # Exclude blends etc from the fit, as well as broken measurements
    idx = obj['flags'] == 0
    idx &= np.isfinite(var1) & (var1 > 0)
    idx &= np.isfinite(var2) & (var2 > 0)
    idx &= np.isfinite(var3)

    X = np.array([np.log10(var1), np.log10(var2), var3]).T
    if classifier is None:
        classifier = IsolationForest().fit(X[idx])
    X[~np.isfinite(X)] = -1000 # Definitely outside of the good locus
    res = classifier.predict(X)

    log(f"{np.sum(res > 0)} good, {np.sum(res < 0)} outliers")

    if return_classifier:
        return classifier

    return res > 0


def plot_outline(x, y, *args, ax=None, **kwargs):
    points = np.vstack((np.ma.filled(x), np.ma.filled(y))).T

    from scipy.spatial import ConvexHull
    hull = ConvexHull(points)

    if ax is None:
        ax = plt.gca()

    for simplex in hull.simplices:
        ax.plot(points[simplex, 0], points[simplex, 1], *args, **kwargs)
        if 'label' in kwargs:
            kwargs.pop('label')


from sklearn.cluster import AgglomerativeClustering

def filter_catalogue_blends(
        cat_in,
        sr,
        cat_col_ra='RAJ2000',
        cat_col_dec='DEJ2000',
        cat_col_mag=None,
        cat_col_mag_err=None
):
    # Clustering fails if we have less than 2 stars. And it is meaningless anyway
    if len(cat_in) < 2:
        return cat_in

    x,y,z = astrometry.radectoxyz(cat_in[cat_col_ra], cat_in[cat_col_dec])

    # Cluster into groups using sr radius
    cids = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=np.deg2rad(sr),
        linkage='single'
    ).fit_predict(np.array([x, y, z]).T)

    # Unique clusters
    uid,uids,urids,ucnt = np.unique(cids, return_index=True, return_inverse=True, return_counts=True)

    # Copy of the catalogue to work on
    cat = cat_in.copy()
    # cat['__blend__'] = False
    cat['__remove__'] = False

    for i,row in enumerate(cat):
        uid1 = urids[i]

        if row['__remove__']:
            continue

        if ucnt[uid1] > 1:
            ids = np.where(urids == uid1)[0]

            if cat_col_mag is not None:
                x1,y1,z1 = astrometry.radectoxyz(cat_in[cat_col_ra][ids], cat_in[cat_col_dec][ids])
                flux1 = 10**(-0.4*cat[cat_col_mag][ids])
                flux1 = np.ma.filled(flux1, np.nan)
                x0,y0,z0 = [np.nansum(_*flux1)/np.nansum(flux1) for _ in [x1,y1,z1]]
                ra,dec = astrometry.xyztoradec([x0,y0,z0])

                if not np.isfinite(ra) or not np.isfinite(dec):
                    # No usable fluxes at all?..
                    continue

                cat[cat_col_ra][ids[0]],cat[cat_col_dec][ids[0]] = ra, dec
                cat[cat_col_mag][ids[0]] = -2.5*np.log10(np.nansum(flux1))

                # cat['__blend__'][ids[0]] = True
                cat['__remove__'][ids[1:]] = True
            else:
                cat['__remove__'][ids] = True

        else:
            pass

    cat = cat[~cat['__remove__']]
    cat.remove_column('__remove__')
    # cat.remove_column('__blend__')

    return cat


def filter_vizier_blends(
    obj,
    sr,
    sr_blend=None,
    obj_col_ra='ra',
    obj_col_dec='dec',
    fname=None,
    vizier=[],
    col_id=None,
    vizier_checker_fn=None,
    verbose=False,
):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    log(
        'Blend filtering routine started with %d initial candidates, %.1f arcsec blending radius and %.1f arcsec matching radius'
        % (len(obj), sr_blend * 3600, sr * 3600)
    )
    cand_idx = np.ones(len(obj), dtype=bool)

    if col_id is None:
        col_id = 'stdpipe_id'

    if col_id not in obj.keys():
        obj = obj.copy()
        obj[col_id] = np.arange(len(obj))

    if sr_blend is None:
        sr_blend = 4*sr # It assumes sr to be half FWHM

    for catname in vizier or []:
        if not np.any(cand_idx):
            break

        xcat = catalogs.xmatch_objects(
            obj[cand_idx][[col_id, obj_col_ra, obj_col_dec]],
            catname,
            sr_blend,
            col_ra=obj_col_ra,
            col_dec=obj_col_dec,
        )

        # Some catalog queries may fail or return no rows; skip gracefully.
        if xcat is None or not len(xcat):
            log(
                np.sum(cand_idx),
                'remains after matching blends with',
                catalogs.catalogs.get(catname, {'name': catname})['name'],
            )
            continue

        if fname is not None:
            # Find relevant magnitude and coordinate columns
            cat_col_mag,_ = guess_catalogue_mag_columns(fname, xcat)
            cat_col_ra,cat_col_dec = guess_catalogue_radec_columns(xcat)

            if cat_col_ra is None:
                log("Cannot guess catalogue coordinate columns, skipping")
                log(xcat.keys())
                continue

            if cat_col_mag:
                xcat = filter_catalogue_blends(
                    xcat,
                    sr_blend,
                    cat_col_ra=cat_col_ra,
                    cat_col_dec=cat_col_dec,
                    cat_col_mag=cat_col_mag
                )

                oidx,xidx,_ = astrometry.spherical_match(
                    obj[cand_idx][obj_col_ra],
                    obj[cand_idx][obj_col_dec],
                    xcat[cat_col_ra],
                    xcat[cat_col_dec],
                    sr,
                )
                xcat = xcat[xidx]

                if xcat is not None and len(xcat):
                    if callable(vizier_checker_fn):
                        # Pass matched results through user-supplied checker
                        xobj = obj[[np.where(obj[col_id] == _)[0][0] for _ in xcat[col_id]]]
                        xidx = vizier_checker_fn(xobj, xcat, catname)
                        xcat = xcat[xidx]

                    cand_idx &= ~np.in1d(obj[col_id], xcat[col_id])

        log(
            np.sum(cand_idx),
            'remains after matching blends with',
            catalogs.catalogs.get(catname, {'name': catname})['name'],
        )

    return obj[cand_idx]


import regions
def write_ds9_regions(filename, objs, radius=None):
    regs = []

    if radius is None:
        radius = 5/3600 # 5 arcsec

    for row in objs:
        regs.append(regions.CircleSkyRegion(SkyCoord(row['ra'], row['dec'], unit='deg'), radius*u.deg))

    regs = regions.Regions(regs)

    regs.write(filename, format='ds9', overwrite=True)


# Actual processing steps below

def inspect_image(filename, config, verbose=True, show=False):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    # Cleanup stale plots
    cleanup_paths(cleanup_inspect, basepath=basepath)

    _coerce_sn(config)
    config['initial_aper'] = config.get('initial_aper') or 3
    config['initial_r0'] = config.get('initial_r0') if config.get('initial_r0') is not None else 0
    config['rel_aper'] = config.get('rel_aper') or 1
    config['rel_bg1'] = config.get('rel_bg1') or 5
    config['rel_bg2'] = config.get('rel_bg2') or 7
    config['spatial_order'] = config.get('spatial_order') if config.get('spatial_order') is not None else 2
    config['minarea'] = config.get('minarea') or 5
    config['use_color'] = config.get('use_color') if config.get('use_color') is not None else True
    config['blend_radius'] = config.get('blend_radius') if config.get('blend_radius') is not None else 2.0
    config['refine_wcs'] = config.get('refine_wcs') if config.get('refine_wcs') is not None else True
    config['blind_match_wcs'] = config.get('blind_match_wcs') if config.get('blind_match_wcs') is not None else False
    if config.get('blind_match_ps_lo') is None:
        config['blind_match_ps_lo'] = 0.2
    if config.get('blind_match_ps_up') is None:
        config['blind_match_ps_up'] = 4.0
    if config.get('blind_match_sr0') is None:
        config['blind_match_sr0'] = 1.0
    config['hotpants_extra'] = config.get('hotpants_extra') or {'ko':0, 'bgo':0}
    config['sub_size'] = config.get('sub_size') or 1000
    config['sub_overlap'] = config.get('sub_overlap') if config.get('sub_overlap') is not None else 50
    config['sub_verbose'] = config.get('sub_verbose') if config.get('sub_verbose') is not None else False
    config['subtraction_mode'] = config.get('subtraction_mode') or 'detection'

    # Fix some initial problems with the image like compression etc
    pre_fix_image(filename, verbose=verbose)

    # Load the image
    log(f'Inspecting {filename}')
    try:
        image,header = fits.getdata(filename, -1).astype(np.double), fits.getheader(filename, -1)
    except:
        raise RuntimeError('Cannot load the image')

    # Fix image Inf values?..
    image[~np.isfinite(image)] = np.nan

    log(f"Image size is {image.shape[1]} x {image.shape[0]}")

    log(f"Image Min / Median / Max : {np.nanmin(image):.2f} {np.nanmedian(image):.2f} {np.nanmax(image):.2f}")
    log(f"Image RMS: {np.nanstd(image):.2f}")

    fix_header(header)

    # Guess some parameters from keywords
    if not config.get('gain'):
        config['gain'] = float(header.get('GAIN', 1))
        if config['gain'] == 0:
            log("Header gain is zero, setting it to 1")
            config['gain'] = 1

        if config['gain'] == 1 and np.nanstd(image) <= 1:
            log(f"Warning: Pixel values are significantly re-scaled, guessing gain from max value")
            # Here we assume that original gain was 1 and original max value was 65535
            config['gain'] = 65535./np.nanmax(image)
    log(f"Gain is {config['gain']:.2f}")

    # Filter
    if not config.get('filter'):
        config['filter'] = 'unknown'
        for kw in ['FILTER', 'FILTERS', 'CAMFILT']:
            if kw in header:
                config['filter'] = header.get(kw).strip()
                break
    log(f"Filter is {config['filter']}")

    obj = header.get('OBJECT')
    if obj and str(obj).strip():
        config['fits_object'] = str(obj).strip()

    # Normalize filters
    for fname in supported_filters.keys():
        if config['filter'] in supported_filters[fname]['aliases']:
            config['filter'] = fname
            log(f"Filter name normalized to {config['filter']}")
            break

    # Fallback filter
    if config['filter'] not in supported_filters.keys():
        log(f"Unknown filter {config['filter']}, falling back to r")
        config['filter'] = 'r'

    # Saturation
    img_max    = np.nanmax(image)
    img_median = np.nanmedian(image)

    # Validate any previously stored saturation (e.g. from a prior inspection run or FITS header)
    # If it is <= 2x the sky median it almost certainly refers to raw detector ADU while the image
    # has been calibrated (bias/flat/e- units), which would flag all stars as saturated.
    if config.get('saturation') and config['saturation'] <= 2.0 * img_median:
        log(f"Warning: stored saturation level ({config['saturation']:.0f}) is only "
            f"{config['saturation']/img_median:.1f}x the sky median ({img_median:.0f}) — "
            f"resetting to image-based estimate.")
        config.pop('saturation', None)

    if not config.get('saturation'):
        satlevel = header.get(
            'SATURATE',
            header.get('DATAMAX')
        )

        if satlevel:
            # Same sanity check on the header value
            if satlevel <= 2.0 * img_median:
                log(f"Warning: header saturation level ({satlevel:.0f}) is only "
                    f"{satlevel/img_median:.1f}x the sky median ({img_median:.0f}) — "
                    f"this looks like a raw-ADU value on a calibrated image. Ignoring it.")
                satlevel = None
            elif satlevel < 0.5 * img_max:
                log(f"Warning: header saturation level ({satlevel:.0f}) is significantly "
                    f"smaller than image max value ({img_max:.0f})!")
            elif satlevel > img_max:
                log(f"Warning: header saturation level ({satlevel:.0f}) is larger than "
                    f"image max value ({img_max:.0f}).")

        if satlevel:
            log("Got saturation level from FITS header")
        else:
            satlevel = 0.05 * img_median + 0.95 * img_max  # sky + 0.95*(max-sky)
            log("Estimating saturation level from the image max value")

        config['saturation'] = satlevel
    log(f"Saturation level is {config['saturation']:.1f}")

    # Mask
    mask = np.isnan(image)
    mask |= image >= config['saturation']

    # Custom mask
    if os.path.exists(os.path.join(basepath, 'custom_mask.fits')):
        mask |= fits.getdata(os.path.join(basepath, 'custom_mask.fits'), -1) > 0
        log("Custom mask loaded from custom_mask.fits")

    # Background size
    if not config.get('bg_size'):
        bg_size = 256
        if bg_size > 0.5*image.shape[0] or bg_size > 0.5*image.shape[1]:
            bg_size = int(min(image.shape[0]/2, image.shape[1]/2))
        log(f"Background mesh size set to {bg_size} x {bg_size} pixels")
        config['bg_size'] = bg_size

    # Cosmics
    if config.get('mask_cosmics', True):
        # We will use custom noise model for astroscrappy as we do not know whether
        # the image is background-subtracted already, or how it was flatfielded
        bg = sep.Background(image, mask=mask)
        rms = bg.rms()
        var = rms**2 + np.abs(image - bg.back())/config.get('gain', 1)
        cmask, cimage = astroscrappy.detect_cosmics(image, mask, verbose=False,
                                                    invar=var.astype(np.float32),
                                                    gain=config.get('gain', 1),
                                                    satlevel=config.get('saturation'),
                                                    cleantype='medmask')
        log(f"Done masking cosmics, {np.sum(cmask)} ({100*np.sum(cmask)/cmask.shape[0]/cmask.shape[1]:.1f}%) pixels masked")
        mask |= cmask

    log(f"{np.sum(mask)} ({100*np.sum(mask)/mask.shape[0]/mask.shape[1]:.1f}%) pixels masked")

    if np.sum(mask) > 0.95*mask.shape[0]*mask.shape[1]:
        raise RuntimeError('More than 95% of the image is masked')

    fits_write(os.path.join(basepath, 'mask.fits'), mask.astype(np.int8), compress=True)
    log("Mask written to file:mask.fits")

    # WCS
    wcs = get_wcs(filename, header=header, verbose=verbose)

    if wcs and wcs.is_celestial:
        ra0,dec0,sr0 = astrometry.get_frame_center(wcs=wcs, width=image.shape[1], height=image.shape[0])
        pixscale = astrometry.get_pixscale(wcs=wcs)

        log(f"Field center is at {ra0:.3f} {dec0:.3f}, radius {sr0:.2f} deg")
        log(f"Pixel scale is {3600*pixscale:.2f} arcsec/pix")

        config['refine_wcs'] = True

        if pixscale > 100.0/3600:
            log("Warning: Pixel scale is too large, most probably WCS is broken! Enabling blind matching.")
            ra0,dec0,sr0 = None,None,None
            config['blind_match_wcs'] = True

    else:
        ra0,dec0,sr0 = None,None,None
        config['blind_match_wcs'] = True
        log("No usable WCS found, blind matching enabled")

    # Target: config (stdbatch/API) wins if it is a real name; otherwise FITS
    # OBJECT / TARGET / pointing. Never resolve placeholders like "image" via Sesame.
    _dummy = {
        'none', 'null', 'nan', 'snva?none',
        'image', 'snapshot', 'light', 'science', 'unknown', 'n/a', 'na', '?',
    }

    def _is_dummy_name(s: str) -> bool:
        v = str(s or '').strip()
        if not v:
            return True
        low = v.lower()
        return (
            low in _dummy
            or low.endswith('?none')
            or low.endswith('.fits')
            or low.endswith('.fit')
            or low.endswith('.fts')
        )

    cfg_target = str(config.get('target', '')).strip() if 'target' in config else ''
    if _is_dummy_name(cfg_target):
        header_name = ''
        for kw in ('OBJECT', 'TARGET', 'OBJNAME', 'OBJECTID'):
            hv = header.get(kw)
            if hv is not None and not _is_dummy_name(str(hv)):
                header_name = str(hv).strip()
                break
        if header_name:
            config['target'] = header_name
            log(f"Target from FITS {kw}: {header_name}")
        else:
            ra = header.get('OBJCTRA')
            dec = header.get('OBJCTDEC')
            if ra and dec:
                config['target'] = f"{str(ra).strip()} {str(dec).strip()}"
                log(f"Target from FITS OBJCTRA/OBJCTDEC: {config['target']}")
            else:
                config.pop('target', None)
                log("No target specified (placeholder value ignored)")

    if config.get('target'):
        config['targets'] = []
        for i,target_name in enumerate(config['target'].splitlines()):
            target = {'name': target_name.strip()}
            target_title = "Primary target" if i == 0 else f"Secondary target {i}"

            if target_name:
                log(f"{target_title} is {target['name']}")
                try:
                    # ── Step 0: try direct coordinate parsing ──────────────────────────
                    # Handles "163.1522 22.9317", "10 52 36.5 +22 55 54", etc.
                    import re as _re2
                    from astropy.coordinates import SkyCoord
                    import astropy.units as _u

                    _stripped = target['name'].strip()
                    # A coordinate string contains at least one space and starts with a digit or +/-
                    _looks_like_coords = (
                        ' ' in _stripped and
                        _re2.match(r'^[\d+\-]', _stripped)
                    )
                    # A bare decimal number (single token, no letters) is likely an incomplete coord
                    _bare_number = _re2.match(r'^[\+\-]?\d+(\.\d+)?$', _stripped)

                    if _bare_number:
                        raise ValueError(
                            f"Target '{_stripped}' looks like a single coordinate value (RA only?). "
                            "Please enter both RA and Dec, e.g. '163.152 22.932' or '10 52 36.5 +22 55 54'."
                        )

                    if _looks_like_coords:
                        try:
                            # Try decimal degrees first, then sexagesimal
                            parts = _stripped.split()
                            if len(parts) == 2:
                                _sc = SkyCoord(float(parts[0]), float(parts[1]), unit=_u.deg)
                            else:
                                _sc = SkyCoord(_stripped, unit=(_u.hourangle, _u.deg))
                            target['ra']  = _sc.ra.deg
                            target['dec'] = _sc.dec.deg
                            log(f"  Resolved as coordinates: RA={target['ra']:.5f}, Dec={target['dec']:.5f}")
                        except Exception as _ce:
                            # Doesn't parse as coords either — fall through to Sesame
                            pass

                    if 'ra' not in target:
                        # Skip the slow stdpipe resolver for obvious transient names (SN/AT)
                        # and campaign IDs that are never in Simbad (EP-…, EP_…).
                        _n = target['name'].lower().replace(" ", "")
                        if _n.startswith("sn") or _n.startswith("at"):
                            raise RuntimeError("Transient name – skip stdpipe resolver")
                        if _n.startswith("ep-") or _n.startswith("ep_") or _n.startswith("ep017"):
                            pointing = _fits_pointing(header)
                            if pointing is not None:
                                target['ra'], target['dec'] = pointing
                                log(f"  Campaign id — using FITS pointing "
                                    f"RA={target['ra']:.5f} Dec={target['dec']:.5f}")
                            else:
                                raise RuntimeError("Campaign / EP id – skip stdpipe resolver")

                    # First attempt: standard stdpipe resolver (Simbad/Sesame with path syntax)
                    if 'ra' not in target:
                        coords = resolve.resolve(target['name'])
                        if coords is None:
                            raise RuntimeError("Sesame returned no match")
                        target['ra'] = coords.ra.deg
                        target['dec'] = coords.dec.deg
                except Exception as e:
                    if 'ra' in target:
                        pass  # already resolved as coordinates above, ignore exception
                    else:
                        # Fallback for the 2025-08-16 change in Sesame URL API: try the "?name" syntax
                        try:
                            import requests, xml.etree.ElementTree as ET

                            url = f"https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp?{requests.utils.quote(target['name'])}"
                            r = requests.get(url, timeout=10)
                            if r.ok:
                                root = ET.fromstring(r.text)
                                # Look for first <Target>/<Resolver> that has <jradeg> & <jdedeg>
                                jra = root.find('.//jradeg')
                                jde = root.find('.//jdedeg')
                                if jra is not None and jde is not None:
                                    target['ra'] = float(jra.text)
                                    target['dec'] = float(jde.text)
                                else:
                                    raise ValueError("Sesame XML missing coordinates")
                            else:
                                raise RuntimeError(f"Sesame fallback HTTP {r.status_code}")
                        except Exception as e_ses:
                            # Header pointing (RA/DEC or OBJCTRA/OBJCTDEC) is enough
                            # for campaign names that are not in Simbad.
                            pointing = _fits_pointing(header)
                            if pointing is not None:
                                target['ra'], target['dec'] = pointing
                                log(f"  Sesame failed ({e_ses}); using FITS pointing "
                                    f"RA={target['ra']:.5f} Dec={target['dec']:.5f}")
                            else:
                                # For transient names we do NOT abort here; we'll try TNS next.
                                _n = target['name'].lower().replace(" ", "")
                                if not (_n.startswith("sn") or _n.startswith("at")):
                                    raise e_ses
                            # Transient – log and continue to TNS CSV fallback
                            log("Sesame returned no coordinates, falling back to TNS public CSV…")

                            # --- TNS fallback (runs immediately here) ---
                            try:
                                import csv, io, urllib.parse as _up, time as _time, re as _re
                                import requests

                                # Bare name without "AT "/"SN " prefix (TNS URLs use the bare name)
                                bare_name = _re.sub(r'^(AT|SN)\s*', '', target['name'], flags=_re.IGNORECASE).strip()

                                def _norm(s):
                                    return str(s).strip().lower().replace(" ", "")

                                _ua_headers = {"User-Agent": "Mozilla/5.0 (compatible; stdweb/1.0)"}

                                def _get_with_retries(url, timeout, attempts=3, backoff_s=2):
                                    last_exc = None
                                    for i in range(attempts):
                                        try:
                                            return requests.get(
                                                url,
                                                headers=_ua_headers,
                                                timeout=timeout,
                                                allow_redirects=True,
                                            )
                                        except Exception as exc:
                                            last_exc = exc
                                            if i < attempts - 1:
                                                delay = backoff_s * (i + 1)
                                                log(
                                                    f"TNS request failed ({type(exc).__name__}): {exc}. "
                                                    f"Retry {i + 1}/{attempts - 1} in {delay}s"
                                                )
                                                _time.sleep(delay)
                                    raise last_exc

                                # --- Strategy 1: direct TNS object page (no auth required) ---
                                # The public page at wis-tns.org/object/<name> embeds decimal
                                # RA/Dec directly in the HTML, e.g. "131.52412414551 +10.794554710388"
                                tns_obj_url = f"https://www.wis-tns.org/object/{_up.quote(bare_name)}"
                                log(f"TNS object page: GET {tns_obj_url}")
                                rr = _get_with_retries(tns_obj_url, timeout=15, attempts=3, backoff_s=2)
                                log(f"TNS object page: HTTP {rr.status_code} ({len(rr.content)} bytes)")
                                if rr.status_code == 200:
                                    m = _re.search(r'\b(\d{2,3}\.\d{4,})\s+([+-]\d{1,2}\.\d{4,})\b', rr.text)
                                    if m:
                                        target['ra'] = float(m.group(1))
                                        target['dec'] = float(m.group(2))
                                        log(f"TNS object page resolved: RA={target['ra']:.6f} DEC={target['dec']:.6f}")

                                # --- Strategy 2: paginated public CSV (fallback) ---
                                if 'ra' not in target:
                                    for page in range(1, 6):
                                        url = (
                                            "https://www.wis-tns.org/search?"
                                            "reported_within_last_value=365&reported_within_last_units=days&"
                                            "num_page=500&public=1&format=csv&include_redshift=1&"
                                            f"page={page}"
                                        )
                                        log(f"TNS page CSV: page={page} -> GET {url}")
                                        rr = _get_with_retries(url, timeout=20, attempts=3, backoff_s=2)
                                        log(f"TNS page CSV: HTTP {rr.status_code} ({len(rr.content)} bytes)")
                                        if rr.status_code == 429:
                                            log("TNS page CSV: rate limited, stopping")
                                            break
                                        if rr.status_code != 200 or len(rr.content) < 20:
                                            continue
                                        content = rr.content.lstrip(b"\xef\xbb\xbf").decode(errors="replace")
                                        reader = csv.DictReader(io.StringIO(content))
                                        rows_found = 0
                                        for row in reader:
                                            rows_found += 1
                                            row_name = _norm(row.get("Name", ""))
                                            if row_name in (_norm(target['name']), _norm(bare_name)):
                                                ra_str, dec_str = row.get("RA"), row.get("DEC")
                                                if ra_str and dec_str:
                                                    from astropy.coordinates import SkyCoord
                                                    c = SkyCoord(ra_str + " " + dec_str, unit=(u.hourangle, u.deg))
                                                    target['ra'] = c.ra.deg
                                                    target['dec'] = c.dec.deg
                                                    log(f"TNS CSV matched (page {page}): RA={target['ra']:.6f} DEC={target['dec']:.6f}")
                                                    break
                                        if 'ra' in target or rows_found < 500:
                                            break
                                        _time.sleep(2)
                            except Exception as e2:
                                log(f"TNS lookup failed: {e2}")

                if 'ra' in target and 'dec' in target:
                    if not len(config['targets']):
                        # Keep backwards-compatible primary target coordinates
                        config['target_ra'] = target['ra']
                        config['target_dec'] = target['dec']

                    # Activate target photometry mode
                    config['subtraction_mode'] = 'target'

                    log(f"Resolved to RA={target['ra']:.4f} Dec={target['dec']:.4f}")

                    config['targets'].append(target)
                else:
                    log("Target name could not be resolved to coordinates")

        if (config.get('target_ra') or config.get('target_dec')) and wcs and wcs.is_celestial:
            if ra0 is not None and dec0 is not None and sr0 is not None:
                if astrometry.spherical_distance(ra0, dec0,
                                                 config.get('target_ra'),
                                                 config.get('target_dec')) > 2.0*sr0:
                    log("Primary target is very far from the image center!")

            try:
                x0,y0 = wcs.all_world2pix(config.get('targets')[0].get('ra'), config.get('targets')[0].get('dec'), 0)

                if x0 > 0 and y0 > 0 and x0 < image.shape[1] and y0 < image.shape[0]:
                    cutout,cheader = cutouts.crop_image_centered(image, x0, y0, 100, header=header)
                    fits.writeto(os.path.join(basepath, 'image_target.fits'), cutout, cheader, overwrite=True)
                    log(f"Primary target is at x={x0:.1f} y={y0:.1f}")
                    log("Primary target cutout written to file:image_target.fits")
                else:
                    log("Primary target is outside the image")
                    log(f"{x0} {y0}")
            except:
                pass

        # We may initialize some blind match parameters from the target position, if any
        if config.get('target_ra') is not None and config.get('blind_match_center') is None:
            config['blind_match_center'] = "{} {}".format(config.get('target_ra'), config.get('target_dec'))

    else:
        # Remove fields that are computed from the target
        config.pop('target_ra', None)
        config.pop('target_dec', None)

    # Suggested catalogue
    if not config.get('cat_name'):
        if config['filter'] in ['U', 'B', 'V', 'R', 'I']:
            config['cat_name'] = 'gaiadr3syn'
        elif config['filter'] in ['G', 'BP', 'RP']:
            config['cat_name'] = 'gaiaedr3'
        else:
            config['cat_name'] = 'ps1'

            if (dec0 is not None and dec0 < -30) or config.get('target_dec', 0) < -30:
                config['cat_name'] = 'skymapper'

        log(f"Suggested catalogue is {supported_catalogs[config['cat_name']]['name']}")

    if not config.get('cat_limit'):
        # Modest limit to restrict getting too faint stars
        config['cat_limit'] = 20.0

    # Suggested template
    if not config.get('template'):
        if ((dec0 is not None and templates.point_in_ps1(ra0, dec0)) or
            (config.get('target_dec') and templates.point_in_ps1(config.get('target_ra'), config.get('target_dec')))):
            # Always try PS1 first, even when LS is available?..
            config['template'] = 'ps1'
        elif ((dec0 is not None and templates.point_in_ls(ra0, dec0)) or
            (config.get('target_dec') and templates.point_in_ls(config.get('target_ra'), config.get('target_dec')))):
            config['template'] = 'ls'
        elif (dec0 is not None and dec0 < -30) or config.get('target_dec', 0) < -30:
            config['template'] = 'skymapper'
        else:
            config['template'] = 'ps1' # Fallback

        log(f"Suggested template is {supported_templates[config['template']]['name']}")

    # Time
    if not config.get('time'):
        time = utils.get_obs_time(header=header, verbose=verbose)

        if time is not None:
            config['time'] = time.iso

    if config.get('time'):
        log(f"Time is {config.get('time')}")
        log(f"MJD is {Time(config.get('time')).mjd}")


def check_photometry_quality(basepath, config, m, target_obj, log=None):
    """
    Run QA checks on a just-completed forced photometry measurement and store
    any warnings in config['photometry_warnings'].

    Checks are filter-agnostic and work for any user/instrument:

    1. Color-term anomaly: compare this task's color term against the
       historical median for the same filter over the last 60 days.
       A large shift in color term signals non-standard atmospheric
       chromaticity (e.g. high airmass, thin clouds with wavelength-
       dependent extinction).

    2. Short-term light-curve consistency: compare the new measurement
       against the linear trend extrapolated from the last 5 same-filter
       measurements of the same target.  A large deviation flags a
       potential bad measurement even when the photometric calibration
       itself looks formally good.

    3. Gaia broadband ordering (only when catalogue is gaiaedr3): for any
       object with a positive SED, the G passband always collects more flux
       than the narrower BP or RP passbands, so G must be at least as bright
       as both.  G > BP or G < RP is physically impossible and signals either
       a template subtraction artefact or an observing condition problem
       specific to that exposure.
    """
    if log is None:
        log = print

    warnings = []

    filt = config.get('filter')
    cat_name = config.get('cat_name', '')
    target_ra = config.get('target_ra')

    current_term = m.get('color_term') if isinstance(m, dict) else None
    current_mag = None
    if len(target_obj) > 0 and 'mag_calib' in target_obj.colnames:
        v = float(target_obj['mag_calib'][0])
        current_mag = v if np.isfinite(v) else None

    # Helper: read the best available calibrated magnitude from a task path
    def _read_mag(task_path):
        for fname in ('sub_target.vot', 'target.vot'):
            p = os.path.join(task_path, fname)
            if os.path.exists(p):
                try:
                    from astropy.table import Table as _Table
                    tbl = _Table.read(p)
                    if 'mag_calib' in tbl.colnames and len(tbl):
                        ct = float(tbl['mag_color_term'][0]) if 'mag_color_term' in tbl.colnames else None
                        mv = float(tbl['mag_calib'][0])
                        return mv if np.isfinite(mv) else None, ct
                except Exception:
                    pass
        return None, None

    # ------------------------------------------------------------------ #
    # Check 1 – color-term anomaly (any filter)                           #
    # ------------------------------------------------------------------ #
    if current_term is not None and filt:
        try:
            from django.apps import apps
            Task = apps.get_model('stdweb', 'Task')
            import datetime as _dt

            cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=60)
            prev_tasks = (
                Task.objects
                .filter(config__filter=filt, created__gte=cutoff,
                        state__in=['photometry_done', 'subtraction_done', 'done'])
                .exclude(id=config.get('_task_id'))
                .order_by('-created')[:300]
            )

            terms = []
            for t in prev_tasks:
                _, ct = _read_mag(t.path())
                if ct is not None:
                    terms.append(ct)

            if len(terms) >= 5:
                arr = np.array(terms)
                hist_median = float(np.median(arr))
                hist_mad = float(np.median(np.abs(arr - hist_median)))
                threshold = max(0.08, 4.0 * hist_mad)
                deviation = abs(current_term - hist_median)
                if deviation > threshold:
                    msg = (
                        f"Color term anomaly ({filt} band): "
                        f"current={current_term:.3f}, "
                        f"historical median={hist_median:.3f} over {len(terms)} tasks "
                        f"(|deviation|={deviation:.3f} > threshold={threshold:.3f}). "
                        f"Possible non-standard atmospheric chromaticity."
                    )
                    log(f"Warning: {msg}")
                    warnings.append({'level': 'warning', 'check': 'color_term', 'message': msg})
        except Exception as e:
            log(f"check_photometry_quality: color-term check skipped ({e})")

    # ------------------------------------------------------------------ #
    # Check 2 – short-term light-curve consistency (any filter)           #
    # ------------------------------------------------------------------ #
    if current_mag is not None and filt and target_ra is not None:
        try:
            from django.apps import apps
            Task = apps.get_model('stdweb', 'Task')
            from astropy.time import Time as _Time
            import datetime as _dt

            obs_time_str = config.get('time')
            if obs_time_str:
                obs_mjd = _Time(obs_time_str).mjd
                cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=14)
                prev_tasks = (
                    Task.objects
                    .filter(config__filter=filt,
                            config__target_ra=target_ra,
                            created__gte=cutoff,
                            state__in=['photometry_done', 'subtraction_done', 'done'])
                    .exclude(id=config.get('_task_id'))
                    .order_by('-created')[:20]
                )

                mjds, mags = [], []
                for t in prev_tasks:
                    t_obs_str = t.config.get('time')
                    if not t_obs_str:
                        continue
                    try:
                        t_mjd = _Time(t_obs_str).mjd
                    except Exception:
                        continue
                    mv, _ = _read_mag(t.path())
                    if mv is not None:
                        mjds.append(t_mjd)
                        mags.append(mv)

                # Need at least 3 prior points to fit a trend
                if len(mjds) >= 3:
                    mjds_arr = np.array(mjds)
                    mags_arr = np.array(mags)
                    # Linear fit to recent points
                    coeffs = np.polyfit(mjds_arr, mags_arr, 1)
                    predicted = np.polyval(coeffs, obs_mjd)
                    residuals = mags_arr - np.polyval(coeffs, mjds_arr)
                    trend_rms = float(np.std(residuals))
                    deviation = abs(current_mag - predicted)
                    # Flag if deviation > max(5×trend_rms, 0.3 mag)
                    threshold = max(5.0 * trend_rms, 0.3)
                    if deviation > threshold:
                        direction = "brighter" if current_mag < predicted else "fainter"
                        msg = (
                            f"Light-curve outlier ({filt} band): "
                            f"measured={current_mag:.3f}, "
                            f"trend prediction={predicted:.3f} "
                            f"({direction} by {deviation:.3f} mag, "
                            f"threshold={threshold:.3f} based on {len(mjds)} recent points, "
                            f"trend_rms={trend_rms:.3f}). "
                            f"Consider verifying image quality."
                        )
                        log(f"Warning: {msg}")
                        warnings.append({'level': 'warning', 'check': 'lightcurve_trend', 'message': msg})
        except Exception as e:
            log(f"check_photometry_quality: light-curve trend check skipped ({e})")

    # ------------------------------------------------------------------ #
    # Check 3 – Gaia broadband ordering (only for gaiaedr3 catalogue)     #
    # G passband spans the full BP+RP range, so G flux ≥ BP flux and      #
    # G flux ≥ RP flux for any positive SED: G must be brighter than both.#
    # This is true regardless of object colour or instrument.             #
    # ------------------------------------------------------------------ #
    is_gaia_g = (
        current_mag is not None
        and 'gaia' in cat_name.lower()
        and config.get('cat_col_mag', '').lower() in ('gmag', 'g', 'g_mean_mag')
    )
    if is_gaia_g:
        try:
            from django.apps import apps
            Task = apps.get_model('stdweb', 'Task')
            from astropy.time import Time as _Time
            import datetime as _dt

            obs_time_str = config.get('time')
            if obs_time_str and target_ra is not None:
                obs_dt = _Time(obs_time_str).to_datetime()
                # Find BP and RP sibling tasks for the same target (±4 h obs time)
                sibling_tasks = (
                    Task.objects
                    .filter(
                        config__target_ra=target_ra,
                        config__cat_col_mag__in=['BPmag', 'RPmag', 'BP', 'RP',
                                                  'phot_bp_mean_mag', 'phot_rp_mean_mag'],
                        created__gte=obs_dt - _dt.timedelta(days=2),
                        created__lte=obs_dt + _dt.timedelta(days=2),
                        state__in=['photometry_done', 'subtraction_done', 'done'],
                    )
                )

                bp_mag = rp_mag = None
                for t in sibling_tasks:
                    t_obs_str = t.config.get('time')
                    if t_obs_str:
                        try:
                            t_obs = _Time(t_obs_str).to_datetime()
                            if abs((t_obs - obs_dt).total_seconds()) > 4 * 3600:
                                continue
                        except Exception:
                            pass
                    col = t.config.get('cat_col_mag', '').lower()
                    mv, _ = _read_mag(t.path())
                    if mv is None:
                        continue
                    if col in ('bpmag', 'bp', 'phot_bp_mean_mag'):
                        bp_mag = mv
                    elif col in ('rpmag', 'rp', 'phot_rp_mean_mag'):
                        rp_mag = mv

                g_mag = current_mag
                if bp_mag is not None and g_mag > bp_mag:
                    delta = g_mag - bp_mag
                    msg = (
                        f"Gaia G is fainter than BP (physically impossible): "
                        f"G={g_mag:.3f} > BP={bp_mag:.3f} (ΔG-BP={delta:+.3f} mag). "
                        f"The G passband contains all BP wavelengths — this signals a "
                        f"template subtraction residual or exposure-specific problem."
                    )
                    log(f"Warning: {msg}")
                    warnings.append({'level': 'danger', 'check': 'gaia_G_vs_BP', 'message': msg})

                if rp_mag is not None and g_mag < rp_mag:
                    delta = rp_mag - g_mag
                    msg = (
                        f"Gaia G is brighter than RP (physically impossible): "
                        f"G={g_mag:.3f} < RP={rp_mag:.3f} (ΔG-RP={-delta:+.3f} mag). "
                        f"Check calibration or template subtraction."
                    )
                    log(f"Warning: {msg}")
                    warnings.append({'level': 'danger', 'check': 'gaia_G_vs_RP', 'message': msg})

        except Exception as e:
            log(f"check_photometry_quality: Gaia ordering check skipped ({e})")

    if warnings:
        config['photometry_warnings'] = warnings
        log(f"Photometry quality check: {len(warnings)} warning(s) raised.")
    else:
        config.pop('photometry_warnings', None)
        log("Photometry quality check passed.")


def photometry_image(filename, config, verbose=True, show=False):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    # Image
    image,header = fits.getdata(filename, -1).astype(np.double), fits.getheader(filename, -1)

    fix_header(header)

    # Mask
    mask = fits.getdata(os.path.join(basepath, 'mask.fits'), -1) > 0

    # Custom mask
    if os.path.exists(os.path.join(basepath, 'custom_mask.fits')):
        custom_mask = fits.getdata(os.path.join(basepath, 'custom_mask.fits'), -1) > 0
    else:
        custom_mask = None

    # Time
    time = Time(config.get('time')) if config.get('time') else None

    # Secondary targets - backward compatibility
    if not 'targets' in config and 'target_ra' in config and 'target_dec' in config:
        config['targets'] = [{'ra': config.get('target_ra'), 'dec': config.get('target_dec')}]

    # Normalize config: replace None (from optional form fields) with safe defaults
    _coerce_sn(config)
    config['initial_aper'] = config.get('initial_aper') or 3
    config['initial_r0'] = config.get('initial_r0') if config.get('initial_r0') is not None else 0
    config['rel_aper'] = config.get('rel_aper') or 1
    config['rel_bg1'] = config.get('rel_bg1') or 5
    config['rel_bg2'] = config.get('rel_bg2') or 7
    config['spatial_order'] = config.get('spatial_order') if config.get('spatial_order') is not None else 2
    config['minarea'] = config.get('minarea') or 5
    config['use_color'] = config.get('use_color') if config.get('use_color') is not None else True
    config['blend_radius'] = config.get('blend_radius') if config.get('blend_radius') is not None else 2.0
    config['refine_wcs'] = config.get('refine_wcs') if config.get('refine_wcs') is not None else True
    config['blind_match_wcs'] = config.get('blind_match_wcs') if config.get('blind_match_wcs') is not None else False
    if config.get('blind_match_ps_lo') is None:
        config['blind_match_ps_lo'] = 0.2
    if config.get('blind_match_ps_up') is None:
        config['blind_match_ps_up'] = 4.0
    if config.get('blind_match_sr0') is None:
        config['blind_match_sr0'] = 1.0
    config['hotpants_extra'] = config.get('hotpants_extra') or {'ko':0, 'bgo':0}
    config['sub_size'] = config.get('sub_size') or 1000
    config['sub_overlap'] = config.get('sub_overlap') if config.get('sub_overlap') is not None else 50
    config['sub_verbose'] = config.get('sub_verbose') if config.get('sub_verbose') is not None else False
    config['subtraction_mode'] = config.get('subtraction_mode') or 'detection'

    # Cleanup stale plots
    cleanup_paths(cleanup_photometry, basepath=basepath)

    log("\n---- Object detection ----\n")

    # SExtractor does not take mask into account while computing the background
    # so we better mask custom-masked regions manually
    if custom_mask is not None:
        image_masked = image.copy()
        image_masked[custom_mask] = np.nan
    else:
        image_masked = image

    # Extract objects and get segmentation map
    obj,segm,fimg,bg,bgrms = photometry.get_objects_sextractor(
        image_masked, mask=mask,
        aper=config.get('initial_aper', 3.0),
        gain=config.get('gain', 1.0),
        extra={
            'BACK_SIZE': config.get('bg_size', 256),
            'SATUR_LEVEL': config.get('saturation')
        },
        extra_params=['NUMBER', 'MAG_AUTO', 'ISOAREA_IMAGE'],
        checkimages=['SEGMENTATION', 'FILTERED', 'BACKGROUND', 'BACKGROUND_RMS'],
        minarea=config.get('minarea', 3),
        r0=config.get('initial_r0', 0.0),
        verbose=verbose,
        _tmpdir=settings.STDPIPE_TMPDIR,
        _exe=settings.STDPIPE_SEXTRACTOR
    )

    # FIXME: Filter some problematic detections
    obj = obj[obj['MAG_AUTO'] < 90]
    obj = obj[obj['fwhm'] > 0]
    obj = obj[obj['ISOAREA_IMAGE'] > config.get('minarea', 3)]

    # Ignore "deblended" flag from SExtractor, for now
    # obj['flags'] &= 0xfffd

    log(f"{len(obj)} objects found")

    fits_write(os.path.join(basepath, 'segmentation.fits'), segm, header, compress=True)
    log("Segmemtation map written to file:segmentation.fits")

    fits_write(os.path.join(basepath, 'filtered.fits'), fimg, header, compress=True)
    log("Filtered image written to file:filtered.fits")

    if config.get('inspect_bg'):
        fits_write(os.path.join(basepath, 'image_bg.fits'), bg, header, compress=True)
        log("Background map written to file:image_bg.fits")

        fits_write(os.path.join(basepath, 'image_rms.fits'), bgrms, header, compress=True)
        log("Background RMS map written to file:image_rms.fits")

    if not len(obj):
        raise RuntimeError('Cannot detect objects in the image')

    log("\n---- Object measurement ----\n")

    # FWHM star selection
    # Start with "ideal" selection: no SExtractor flags + S/N > 20 + pre-filter
    idx = obj['flags'] == 0
    idx &= obj['magerr'] < 1/20

    if config.get('prefilter_detections', True):
        log("Pre-filtering SExtractor detections with simple shape classifier")
        fidx = filter_sextractor_detections(obj, verbose=verbose)
        idx &= fidx
        # Also store it in the flags to exclude from photometric match later
        obj['flags'][~fidx] |= 0x800

    if not len(obj[idx]):
        # Diagnose and try progressively relaxed selections
        n_clean   = np.sum(obj['flags'] == 0)
        n_sn20    = np.sum(obj['magerr'] < 1/20)
        n_prefilt = np.sum((obj['flags'] & 0x800) == 0) if config.get('prefilter_detections', True) else len(obj)
        log(f"Warning: strict FWHM selection empty (flags==0: {n_clean}, S/N>20: {n_sn20}, prefilter ok: {n_prefilt})")
        log("Trying relaxed star selection for FWHM estimation…")

        # Relaxation 1: allow SExtractor flags (deblended, etc.) but keep pre-filter + any S/N
        idx = (obj['flags'] & 0x800) == 0   # only exclude pre-filter outliers
        idx &= obj['magerr'] < 1/20

        if not len(obj[idx]):
            # Relaxation 2: drop S/N > 20 — use best 20% by S/N among pre-filter survivors
            idx = (obj['flags'] & 0x800) == 0
            if len(obj[idx]):
                magerr_threshold = np.percentile(obj['magerr'][idx], 20)
                idx &= obj['magerr'] <= magerr_threshold
                log(f"Relaxation 2: using top-20%% S/N stars (magerr ≤ {magerr_threshold:.3f}, "
                    f"i.e. S/N ≥ {1/magerr_threshold:.1f})")

        if not len(obj[idx]):
            raise RuntimeError(
                f"No suitable stars for FWHM estimation. "
                f"Detected {len(obj)} objects; {n_clean} have no SExtractor flags; "
                f"{n_sn20} have S/N > 20. "
                f"Check gain value ({config.get('gain')}) and saturation level ({config.get('saturation'):.0f}). "
                f"Image sky median={np.nanmedian(image):.0f} ADU, max={np.nanmax(image):.0f} ADU."
            )
        else:
            log(f"Using {np.sum(idx)} stars for FWHM after relaxed selection.")

    fwhm_values = 2.0*obj['FLUX_RADIUS'] # obj['fwhm']

    fwhm = np.median(fwhm_values[idx]) # TODO: make it position-dependent
    log(f"FWHM is {fwhm:.2f} pixels")

    if config.get('fwhm_override'):
        fwhm = config.get('fwhm_override')
        log(f"Overriding with user-specified FWHM value of {fwhm:.2f} pixels")

    config['fwhm'] = fwhm

    # Plot FWHM map
    with plots.figure_saver(os.path.join(basepath, 'fwhm.png'), figsize=(8, 6), show=show) as fig:
        ax = fig.add_subplot(1, 1, 1)
        plots.binned_map(
            obj[idx]['x'], obj[idx]['y'], fwhm_values[idx],
            range=[[0, image.shape[1]], [0, image.shape[0]]],
            bins=8, statistic='median',
            show_dots=True, ax=ax
        )
        ax.set_aspect(1)
        ax.set_xlim(0, image.shape[1])
        ax.set_ylim(0, image.shape[0])
        # ax.legend()
        ax.set_title(f"FWHM: median {np.median(fwhm_values[idx]):.2f} pix RMS {np.std(fwhm_values[idx]):.2f} pix")

    # Plot FWHM vs instrumental
    with plots.figure_saver(os.path.join(basepath, 'fwhm_mag.png'), figsize=(8, 6), show=show) as fig:
        ax = fig.add_subplot(1, 1, 1)
        ax.plot(fwhm_values, obj['mag'], '.', label='All objects')
        ax.plot(fwhm_values[idx], obj['mag'][idx], '.', label='Used for FWHM')

        ax.axvline(fwhm, ls='--', color='red')
        ax.invert_yaxis()
        ax.legend()
        ax.grid(alpha=0.2)
        ax.set_title(f"FWHM: median {np.median(fwhm_values[idx]):.2f} pix RMS {np.std(fwhm_values[idx]):.2f} pix")
        ax.set_xlabel('FWHM, pixels')
        ax.set_ylabel('Instrumental magnitude')
        ax.set_xlim(0, np.percentile(fwhm_values, 98))

    log(f"FWHM diagnostic plot stored to file:fwhm_mag.png")

    # Plot pre-filtering diagnostics
    if config.get('prefilter_detections', True):
        with plots.figure_saver(os.path.join(basepath, 'prefilter.png'), figsize=(8, 8), show=show) as fig:
            var1,label1 = obj['FLUX_RADIUS'], 'FLUX_RADIUS'
            var2,label2 = obj['fwhm'], 'FWHM'
            var3,label3 = obj['mag']-obj['MAG_AUTO'], 'MAG_APER - MAG_AUTO'

            if len(var1) > 1000:
                alpha = 0.3
            elif len(var1) > 100:
                alpha = 0.5
            else:
                alpha = 1

            # All flags except 0x800 that we just set for outliers
            idx = (obj['flags'] & (0x7fff - 0x800)) > 0

            # Subtypes
            idx1 = idx & (obj['flags'] & 0x04 > 0) & (obj['flags'] & 0x100 > 0) # Saturated
            idx2 = idx & (obj['flags'] & 0x04 == 0) & (obj['flags'] & 0x100 > 0) # Cosmics
            idx3 = idx & (obj['flags'] & 0x02 > 0) & (obj['flags'] & 0x100 == 0) # Deblended
            idx4 = idx & ~idx1 & ~idx2 & ~idx3 # Other flags
            idx0 = ~idx1 & ~idx2 & ~idx3 # Unflagged

            ax1 = fig.add_subplot(221)
            ax1.plot(var1[idx0], var2[idx0], '.', alpha=alpha)
            ax1.plot(var1[idx1], var2[idx1], '.', alpha=alpha, color='C1', label='Saturated')
            ax1.plot(var1[idx2], var2[idx2], '.', alpha=alpha, color='C3', label='Cosmics')
            ax1.plot(var1[idx3], var2[idx3], '.', alpha=alpha, color='C4', label='Deblended')
            ax1.plot(var1[idx4], var2[idx4], '.', alpha=alpha, color='C5', label='Other flags')
            plot_outline(var1[fidx], var2[fidx], 'r-', ax=ax1)#, label='Good')
            ax1.legend()

            ax1.set_xscale('log')
            ax1.set_yscale('log')

            ax2 = fig.add_subplot(222, sharey=ax1)
            ax2.plot(var3[idx0], var2[idx0], '.', alpha=alpha)
            ax2.plot(var3[idx1], var2[idx1], '.', alpha=alpha, color='C1', label='Saturated')
            ax2.plot(var3[idx2], var2[idx2], '.', alpha=alpha, color='C3', label='Cosmics')
            ax2.plot(var3[idx3], var2[idx3], '.', alpha=alpha, color='C4', label='Deblended')
            ax2.plot(var3[idx4], var2[idx4], '.', alpha=alpha, color='C5', label='Other flags')
            plot_outline(var3[fidx], var2[fidx], 'r-', ax=ax2)#, label='Good')
            ax2.legend()

            ax3 = fig.add_subplot(223, sharex=ax1)
            ax3.plot(var1[idx0], var3[idx0], '.', alpha=alpha)
            ax3.plot(var1[idx1], var3[idx1], '.', alpha=alpha, color='C1', label='Saturated')
            ax3.plot(var1[idx2], var3[idx2], '.', alpha=alpha, color='C3', label='Cosmics')
            ax3.plot(var1[idx3], var3[idx3], '.', alpha=alpha, color='C4', label='Deblended')
            ax3.plot(var1[idx4], var3[idx4], '.', alpha=alpha, color='C5', label='Other flags')
            plot_outline(var1[fidx], var3[fidx], 'r-', ax=ax3)#, label='Good')
            ax3.legend()

            ax1.grid(alpha=0.2)
            ax2.grid(alpha=0.2)
            ax3.grid(alpha=0.2)

            ax1.set_xlabel(label1)
            ax1.set_ylabel(label2)

            ax2.set_xlabel(label3)
            ax2.set_ylabel(label2)

            ax3.set_xlabel(label1)
            ax3.set_ylabel(label3)

            ax1.axhline(fwhm, ls='--', color='gray')
            ax2.axhline(fwhm, ls='--', color='gray')

            ax1.axvline(fwhm/2, ls='--', color='gray')
            ax3.axvline(fwhm/2, ls='--', color='gray')

            ax4 = fig.add_subplot(224)
            ax4.axis('off')
            ax4.annotate(
                f"Isolation forest outlier detection\n"
                f"{len(obj)} objects\n"
                f"{np.sum(idx)} flagged\n"
                f"{np.sum(fidx)} good {np.sum(~fidx)} outliers\n"
                f"FWHM {fwhm:.2f} pixels",
                (0.0, 1.0), xycoords='axes fraction', va='top'
            )

        log("Pre-filtering diagnostic plot stored to file:prefilter.png")

    if config.get('rel_bg1') and config.get('rel_bg2'):
        rel_bkgann = [config['rel_bg1'], config['rel_bg2']]
    else:
        rel_bkgann = None

    # Forced photometry at objects positions
    obj = photometry.measure_objects(obj, image, mask=mask,
                                     fwhm=fwhm,
                                     aper=config.get('rel_aper', 1.0),
                                     bkgann=rel_bkgann,
                                     sn=config.get('sn', 5.0),
                                     bg_size=config.get('bg_size', 256),
                                     gain=config.get('gain', 1.0),
                                     verbose=verbose)

    log(f"{len(obj)} objects properly measured, {np.sum(obj['flags'] == 0)} unflagged")
    if np.sum(obj['flags'] == 0) < 0.5*len(obj):
        log("Warning: more than half of objects are flagged!")

    obj.write(os.path.join(basepath, 'objects.vot'), format='votable', overwrite=True)
    log("Measured objects stored to file:objects.vot")

    # Plot detected objects
    with plots.figure_saver(os.path.join(basepath, 'objects.png'), figsize=(8, 6), show=show,) as fig:
        ax = fig.add_subplot(1, 1, 1)
        idx = obj['flags'] == 0
        ax.plot(obj['x'][idx], obj['y'][idx], '.', label='Unflagged')
        ax.plot(obj['x'][~idx], obj['y'][~idx], '.', label='Flagged')
        ax.set_aspect(1)
        ax.set_xlim(0, image.shape[1])
        ax.set_ylim(0, image.shape[0])
        ax.legend()
        ax.set_title(f"Detected objects: {np.sum(idx)} unmasked, {np.sum(~idx)} masked")

    log("\n---- Initial astrometry ----\n")

    # Get initial WCS
    if config['blind_match_wcs']:
        # Blind match WCS
        log("Will try blind matching for WCS solution")

        # Get lowest S/N where we have at least 20 stars
        sn_vals = obj['flux']/obj['fluxerr']
        for sn0 in range(20, 1, -1):
            if np.sum(sn_vals >= sn0) > 20:
                break

        log(f"SN0 = {sn0:.1f}, N0 = {np.sum(sn_vals >= sn0)}")

        if np.sum(sn_vals >= sn0) < 10:
            raise RuntimeError('Too few good objects for blind matching')

        if config.get('blind_match_center'):
            center = resolve.resolve(config.get('blind_match_center'))
            center_ra = center.ra.deg
            center_dec = center.dec.deg
        else:
            center_ra = None
            center_dec = None

        # Exclude pre-filtered detections and limit list size
        obj_bm = obj[(obj['flags'] & 0x800) == 0]
        obj_bm = obj_bm[:500]

        wcs = astrometry.blind_match_objects(
            obj_bm,
            center_ra=center_ra,
            center_dec=center_dec,
            radius=config.get('blind_match_sr0'),
            scale_lower=config.get('blind_match_ps_lo'),
            scale_upper=config.get('blind_match_ps_up'),
            sn=sn0,
            verbose=verbose,
            _tmpdir=settings.STDPIPE_TMPDIR,
            _exe=settings.STDPIPE_SOLVE_FIELD,
            config=settings.STDPIPE_SOLVE_FIELD_CONFIG
        )

        if wcs is not None and wcs.is_celestial:
            astrometry.store_wcs(os.path.join(basepath, "image.wcs"), wcs)
            astrometry.clear_wcs(header)
            header += wcs.to_header(relax=True)
            config['blind_match_wcs'] = False
            config['refine_wcs'] = True # We need to do it as we got SIP solution
            log("Blind matched WCS stored to file:image.wcs")

            # Save field centre of the (possibly refined) WCS for API consumers
            if wcs and wcs.is_celestial:
                ra_cen, dec_cen, sr_cen = astrometry.get_frame_center(
                    wcs=wcs, width=image.shape[1], height=image.shape[0]
                )
                config['field_ra'] = float(ra_cen)
                config['field_dec'] = float(dec_cen)
                config['field_sr'] = float(sr_cen)
        else:
            log("Blind matching failed")

    else:
        wcs = get_wcs(filename, header=header, verbose=verbose)

    if wcs is None or not wcs.is_celestial:
        raise RuntimeError('No WCS astrometric solution')

    obj['ra'],obj['dec'] = wcs.all_pix2world(obj['x'], obj['y'], 0)

    log("\n---- Reference catalogue ----\n")

    # Get reference catalogue
    ra0,dec0,sr0 = astrometry.get_frame_center(wcs=wcs, width=image.shape[1], height=image.shape[0])
    pixscale = astrometry.get_pixscale(wcs=wcs)

    log(f"Field center is at {ra0:.3f} {dec0:.3f}, radius {sr0:.2f} deg, scale {3600*pixscale:.2f} arcsec/pix")

    if config.get('cat_name') not in supported_catalogs:
        raise RuntimeError("Unsupported or not specified catalogue")

    filters = {}
    if supported_catalogs[config['cat_name']].get('limit') and config.get('cat_limit'):
        filters[supported_catalogs[config['cat_name']].get('limit')] = f"<{config['cat_limit']}"

    # Round the coordinates a bit to optimize consecutive calls to Vizier after WCS refinement
    ra00,dec00,sr00 = round_coords_to_grid(ra0, dec0, sr0)
    cat = catalogs.get_cat_vizier(ra00, dec00, sr00, config['cat_name'], filters=filters, verbose=verbose)

    if not cat or not len(cat):
        raise RuntimeError('Cannot get catalogue stars')

    log(f"Got {len(cat)} catalogue stars from {config['cat_name']}")

    cat.write(os.path.join(basepath, 'cat.vot'), format='votable', overwrite=True)
    log("Catalogue written to file:cat.vot")

    if config.get('filter_blends', True):
        # TODO: merge blended stars, not remove them!
        blend_radius = config.get('blend_radius', 2.0)
        cat_filtered = filter_catalogue_blends(cat, blend_radius*fwhm*pixscale)
        log(f"{len(cat_filtered)} catalogue stars after blend filtering with {blend_radius*3600*fwhm*pixscale:.1f} arcsec radius")
        # cat.write(os.path.join(basepath, 'cat_filtered.vot'), format='votable', overwrite=True)
        # log("Filtered catalogue written to file:cat_filtered.vot")
    else:
        cat_filtered = cat

    # Catalogue settings
    config['cat_col_mag'],config['cat_col_mag_err'] = guess_catalogue_mag_columns(
        config['filter'],
        cat
    )

    if config['cat_col_mag'] in ['Umag', 'Bmag', 'Vmag', 'Rmag', 'Imag']:
        config['cat_col_color_mag1'] = 'Bmag'
        config['cat_col_color_mag2'] = 'Vmag'
    elif config['cat_col_mag'] in ['umag', 'gmag', 'rmag', 'imag']:
        config['cat_col_color_mag1'] = 'gmag'
        config['cat_col_color_mag2'] = 'rmag'
    elif config['cat_col_mag'] in ['zmag']:
        config['cat_col_color_mag1'] = 'rmag'
        config['cat_col_color_mag2'] = 'imag'
    elif config['cat_col_mag'] in ['Gmag', 'BPmag', 'RPmag']:
        config['cat_col_color_mag1'] = 'BPmag'
        config['cat_col_color_mag2'] = 'RPmag'
    else:
        raise RuntimeError(f"Cannot guess magnitude columns for {config.get('cat_name')} and filter {config.get('filter')}")

    log(f"Will use catalogue column {config['cat_col_mag']} as primary magnitude ")
    log(f"Will use catalogue columns {config['cat_col_color_mag1']} and {config['cat_col_color_mag2']} for color")

    if not (config['cat_col_mag'] in cat.colnames and
            (not config.get('cat_col_color_mag1') or config['cat_col_color_mag1'] in cat.colnames) and
            (not config.get('cat_col_color_mag2') or config['cat_col_color_mag2'] in cat.colnames)):
        raise RuntimeError('Catalogue does not have required magnitudes')

    # Astrometric refinement. SCAMP (order 3) can return a "successful"
    # degenerate TPV that is much worse than a good header SIP WCS — keep
    # the original when that happens, and skip SCAMP when the original
    # already matches the catalogue tightly.
    if config.get('refine_wcs', False):
        log("\n---- Astrometric refinement ----\n")

        cat_col_ra, cat_col_dec = guess_catalogue_radec_columns(cat_filtered)
        sr_ast = fwhm * pixscale
        n_cur, med_cur = _wcs_match_count(
            obj, cat_filtered, wcs, sr_ast, cat_col_ra, cat_col_dec
        )
        log(f"Current WCS: {_fmt_match_stats(n_cur, med_cur)} within {sr_ast * 3600:.1f} arcsec")

        wcs_fits = None
        try:
            wcs_fits = WCS(fits.getheader(filename, -1), naxis=2)
        except Exception:
            wcs_fits = None
        n_fits, med_fits = _wcs_match_count(
            obj, cat_filtered, wcs_fits, sr_ast, cat_col_ra, cat_col_dec
        )
        if wcs_fits is not None and getattr(wcs_fits, 'is_celestial', False):
            log(f"FITS header WCS: {_fmt_match_stats(n_fits, med_fits)} within {sr_ast * 3600:.1f} arcsec")
            header_better = n_fits > n_cur and (
                n_cur < 15 or n_fits >= 2 * max(n_cur, 1)
            )
            if header_better:
                log("FITS header WCS matches the catalogue better — using it as baseline")
                wcs = wcs_fits
                n_cur, med_cur = n_fits, med_fits
                obj['ra'], obj['dec'] = wcs.all_pix2world(obj['x'], obj['y'], 0)

        skip_scamp = n_cur >= 30 and med_cur is not None and med_cur < 0.5
        wcs1 = None
        if skip_scamp:
            log("Original WCS already has a dense, tight catalogue match — "
                "skipping SCAMP to avoid a degenerate re-fit")
        else:
            obj_ast = obj[(obj['flags'] & 0x800) == 0]
            wcs1 = pipeline.refine_astrometry(
                obj_ast, cat_filtered, sr_ast,
                wcs=wcs, order=3, method='scamp',
                cat_col_mag=config.get('cat_col_mag'),
                cat_col_mag_err=config.get('cat_col_mag_err'),
                verbose=verbose,
                _tmpdir=settings.STDPIPE_TMPDIR,
                _exe=settings.STDPIPE_SCAMP,
            )
            if wcs1 is None or not getattr(wcs1, 'is_celestial', False):
                log("Warning: WCS refinement failed (SCAMP chi2 too high or no convergence). "
                    "Continuing with existing WCS.")
                wcs1 = None
            else:
                n_new, med_new = _wcs_match_count(
                    obj, cat_filtered, wcs1, sr_ast, cat_col_ra, cat_col_dec
                )
                log(f"SCAMP WCS: {_fmt_match_stats(n_new, med_new)} within {sr_ast * 3600:.1f} arcsec")
                degenerate = n_new < 15 or (n_cur >= 15 and n_new < 0.5 * n_cur)
                if degenerate:
                    log("Warning: SCAMP solution is degenerate or worse than the original WCS — keeping the original")
                    wcs1 = None

        if wcs1 is not None:
            wcs = wcs1
            log("Refined WCS stored to file:image.wcs")
        else:
            wcs = _wcs_as_tpv(
                wcs, obj=obj, cat=cat_filtered, sr_deg=sr_ast,
                cat_col_ra=cat_col_ra, cat_col_dec=cat_col_dec, log=log,
            )
            log("Original WCS kept (stored to file:image.wcs)")

        obj['ra'], obj['dec'] = wcs.all_pix2world(obj['x'], obj['y'], 0)
        astrometry.store_wcs(os.path.join(basepath, "image.wcs"), wcs)
        astrometry.clear_wcs(header)
        header += wcs.to_header(relax=True)
        config['refine_wcs'] = False
        if wcs and wcs.is_celestial:
            ra_cen, dec_cen, sr_cen = astrometry.get_frame_center(
                wcs=wcs, width=image.shape[1], height=image.shape[0]
            )
            config['field_ra'] = float(ra_cen)
            config['field_dec'] = float(dec_cen)
            config['field_sr'] = float(sr_cen)

    log("\n---- Photometric calibration ----\n")

    sr = config.get('sr_override')
    if sr:
        sr /= 3600 # Arcseconds to degrees

    # Photometric calibration
    m = pipeline.calibrate_photometry(
        obj, cat_filtered, sr=sr, pixscale=pixscale,
        cat_col_mag=config.get('cat_col_mag'),
        cat_col_mag_err=config.get('cat_col_mag_err'),
        cat_col_mag1=config.get('cat_col_color_mag1'),
        cat_col_mag2=config.get('cat_col_color_mag2'),
        use_color=config.get('use_color', True),
        order=config.get('spatial_order', 0),
        bg_order=config.get('bg_order', None),
        robust=True, scale_noise=True,
        accept_flags=0x02, max_intrinsic_rms=0.01,
        verbose=verbose
    )

    if m is None:
        raise RuntimeError('Photometric match failed')

    # Check photometric correlation (instrumental vs catalogue)
    if True:
        a0, b0 = (m['omag']+m['zero_model'])[m['idx0']], m['cmag'][m['idx0']]
        c0 = np.corrcoef(a0, b0)[0, 1]

        b1 = np.array(b0.copy())
        cs = []

        for i in range(10000):
            np.random.shuffle(b1)
            cs.append(np.corrcoef(a0, b1)[0, 1])

        from scipy import stats
        qval = stats.percentileofscore(np.abs(cs), np.abs(c0))
        pval = 1 - 0.01*qval

        log(f"Instr / Cat correlation is {c0:.2f} which corresponds to p-value {pval:.2g}")
        if pval > 0.05:
            log(f"Warning: the correlation is not significant, probably the astrometry is wrong!")

    # Store photometric solution
    pickle_to_file(os.path.join(basepath, 'photometry.pickle'), m)
    log("Photometric solution stored to photometry.pickle")

    # Plot photometric solution.
    # Matplotlib may occasionally fail inside celery worker shutdown/signal handlers
    # (e.g. billiard SystemExit). Do not fail the full photometry task for diagnostics.
    def _run_plot(plot_name, draw_fn, figsize=(8, 6)):
        try:
            with plots.figure_saver(os.path.join(basepath, plot_name), figsize=figsize, show=show) as fig:
                draw_fn(fig)
        except BaseException as e:
            log(f"Warning: failed to generate {plot_name}: {type(e).__name__}: {e}")

    _run_plot('photometry.png', lambda fig: (
        (lambda ax: plots.plot_photometric_match(m, mode='mag', ax=ax))(fig.add_subplot(2, 1, 1)),
        (lambda ax: plots.plot_photometric_match(m, mode='color', ax=ax))(fig.add_subplot(2, 1, 2))
    ))

    _run_plot('photometry_unmasked.png', lambda fig: (
        (lambda ax: (plots.plot_photometric_match(m, mode='mag', show_masked=False, ax=ax), ax.set_ylim(-0.4, 0.4)))(fig.add_subplot(2, 1, 1)),
        (lambda ax: (plots.plot_photometric_match(m, mode='color', show_masked=False, ax=ax), ax.set_ylim(-0.4, 0.4)))(fig.add_subplot(2, 1, 2))
    ))

    _run_plot('photometry_zeropoint.png', lambda fig: (
        (lambda ax: (
            plots.plot_photometric_match(m, mode='zero', show_dots=True, bins=8, ax=ax,
                                         range=[[0, image.shape[1]], [0, image.shape[0]]]),
            ax.set_aspect(1), ax.set_xlim(0, image.shape[1]), ax.set_ylim(0, image.shape[0])
        ))(fig.add_subplot(1, 1, 1))
    ))

    _run_plot('photometry_model.png', lambda fig: (
        (lambda ax: (
            plots.plot_photometric_match(m, mode='model', show_dots=True, bins=8, ax=ax,
                                         range=[[0, image.shape[1]], [0, image.shape[0]]]),
            ax.set_aspect(1), ax.set_xlim(0, image.shape[1]), ax.set_ylim(0, image.shape[0])
        ))(fig.add_subplot(1, 1, 1))
    ))

    _run_plot('photometry_residuals.png', lambda fig: (
        (lambda ax: (
            plots.plot_photometric_match(m, mode='residuals', show_dots=True, bins=8, ax=ax,
                                         range=[[0, image.shape[1]], [0, image.shape[0]]]),
            ax.set_aspect(1), ax.set_xlim(0, image.shape[1]), ax.set_ylim(0, image.shape[0])
        ))(fig.add_subplot(1, 1, 1))
    ))

    _run_plot('astrometry_dist.png', lambda fig: (
        (lambda ax: (
            plots.plot_photometric_match(m, mode='dist', show_dots=True, bins=8, ax=ax,
                                         range=[[0, image.shape[1]], [0, image.shape[0]]]),
            ax.set_aspect(1), ax.set_xlim(0, image.shape[1]), ax.set_ylim(0, image.shape[0])
        ))(fig.add_subplot(1, 1, 1))
    ))

    # Apply photometry to objects
    # (It should already be done in calibrate_photometry(), but let's be verbose
    zp = m['zero_fn'](obj['x'], obj['y'], obj['mag'])
    zp_err = m['zero_fn'](obj['x'], obj['y'], obj['mag'], get_err=True)
    obj['mag_calib'] = obj['mag'] + zp
    obj['mag_calib_err'] = np.hypot(obj['magerr'], zp_err)

    obj['mag_filter_name'] = m['cat_col_mag']

    if 'cat_col_mag1' in m.keys() and 'cat_col_mag2' in m.keys():
        obj['mag_color_name'] = '%s - %s' % (m['cat_col_mag1'], m['cat_col_mag2'])
    if m['color_term'] is not None:
        obj['mag_color_term'] = [m['color_term']]*len(obj)

    log(f"Mean zero point is {np.mean(zp):.3f}, estimated error {np.mean(zp_err):.2g}")

    obj.write(os.path.join(basepath, 'objects.vot'), format='votable', overwrite=True)
    log("Measured objects stored to file:objects.vot")

    # Check the filter
    if (config.get('use_color', True) and np.any(np.abs(m['color_term']) > 0.5)) or config.get('diagnose_color'):
        if config.get('diagnose_color'):
            log("Running color term diagnostics for all possible filters")
        else:
            log("Warning: color term is too large, checking whether other filters would work better")

        for fname in supported_catalogs[config['cat_name']].get('filters', []):
            m1 = pipeline.calibrate_photometry(
                obj, cat, pixscale=pixscale,
                cat_col_mag=fname + 'mag',
                cat_col_mag_err='e_' + fname + 'mag',
                cat_col_mag1=config.get('cat_col_color_mag1'),
                cat_col_mag2=config.get('cat_col_color_mag2'),
                use_color=config.get('use_color', True),
                order=config.get('spatial_order', 0),
                robust=True, scale_noise=True,
                accept_flags=0x02, max_intrinsic_rms=0.01,
                verbose=False)

            if m1 is not None:
                log(f"filter {fname}: color term {photometry.format_color_term(m1['color_term'])}")
            else:
                log(f"filter {fname}: match failed")

    # Detection limits
    log("\n---- Global detection limit ----\n")
    sns = [10, 5, 3]
    if config.get('sn', 5) not in sns:
        sns.append(config.get('sn', 5))
    for sn in sns:
        mag0 = pipeline.get_detection_limit(obj, sn=sn, verbose=False)
        if mag0 is not None:
            log(f"Detection limit at S/N={sn:.0f} level is {mag0:.2f}")
        else:
            log(f"Detection limit at S/N={sn:.0f} level could not be computed",)

    mag0 = pipeline.get_detection_limit(obj, sn=config.get('sn'), verbose=False)
    # Store result only if available
    config['mag_limit'] = mag0 if mag0 is not None else np.nan

    if 'bg_fluxerr' in obj.colnames and np.any(obj['bg_fluxerr'] > 0):
        fluxerr = obj['bg_fluxerr']
        sn = config.get('sn', 5)
        maglim = -2.5*np.log10(sn*fluxerr) + m['zero_fn'](obj['x'], obj['y'], obj['mag'])
        maglim = maglim[np.isfinite(maglim)] # Remove Inf and NaN
        log(f"Local background RMS detection limit (S/N={sn:.0f}) is {np.nanmedian(maglim):.2f} +/- {np.nanstd(maglim):.2f}")

    # Plot detection limit estimators
    with plots.figure_saver(os.path.join(basepath, 'limit_hist.png'), figsize=(8, 6), show=show) as fig:
        ax = fig.add_subplot(1, 1, 1)
        # Filter out catalogue stars outside the image
        cx,cy = wcs.all_world2pix(cat['RAJ2000'], cat['DEJ2000'], 0)
        cat_idx = (cx > 0) & (cy > 0) & (cx < image.shape[1]) & (cy < image.shape[0])
        plots.plot_mag_histogram(obj, cat[cat_idx], cat_col_mag=config['cat_col_mag'], sn=config.get('sn'), ax=ax)

    with plots.figure_saver(os.path.join(basepath, 'limit_sn.png'), figsize=(8, 6), show=show) as fig:
        ax = fig.add_subplot(1, 1, 1)
        plots.plot_detection_limit(obj, mag_name=config['cat_col_mag'], sn=config.get('sn', 3), ax=ax)

    # Target forced photometry
    if config.get('targets'):
        log("\n---- Primary and secondary targets forced photometry ----\n")

        target_obj = Table({
            'ra': [_['ra'] for _ in config['targets']],
            'dec': [_['dec'] for _ in config['targets']]
        })

        # Pre-check: ensure the primary target is within the image field before
        # calling all_world2pix — a target far outside the field causes the WCS
        # SIP distortion solver to fail to converge with a cryptic error.
        field_ra  = config.get('field_ra',  wcs.wcs.crval[0])
        field_dec = config.get('field_dec', wcs.wcs.crval[1])
        field_sr  = config.get('field_sr',  0.5)
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        field_center  = SkyCoord(field_ra, field_dec, unit='deg')
        primary_coord = SkyCoord(target_obj['ra'][0], target_obj['dec'][0], unit='deg')
        separation_deg = field_center.separation(primary_coord).deg
        if separation_deg > field_sr * 2:
            raise RuntimeError(
                f"Primary target ({target_obj['ra'][0]:.4f}, {target_obj['dec'][0]:.4f}) "
                f"is {separation_deg:.2f}° away from the image center "
                f"({field_ra:.4f}, {field_dec:.4f}) — image field radius is only "
                f"{field_sr:.3f}°. Please check that the correct image was uploaded "
                f"and that the target coordinates match the field."
            )

        try:
            target_obj['x'],target_obj['y'] = wcs.all_world2pix(target_obj['ra'], target_obj['dec'], 0)
        except Exception as e:
            raise RuntimeError(
                f"Could not project target ({target_obj['ra'][0]:.4f}, {target_obj['dec'][0]:.4f}) "
                f"onto image WCS (field center {field_ra:.4f}, {field_dec:.4f}): {e}"
            ) from e

        # Filter out targets outside the image so that photometry routine does not crash
        def is_inside(x, y):
            return (x > 0 and x < image.shape[1] and y > 0 and y < image.shape[0])

        if not is_inside(target_obj['x'][0], target_obj['y'][0]):
            raise RuntimeError("Primary target is outside the image")

        log(f"Primary target position is {target_obj['ra'][0]:.3f} {target_obj['dec'][0]:.3f} -> {target_obj['x'][0]:.1f} {target_obj['y'][0]:.1f}")

        target_obj = photometry.measure_objects(target_obj, image, mask=mask,
                                                fwhm=fwhm,
                                                aper=config.get('rel_aper', 1.0),
                                                bkgann=rel_bkgann,
                                                sn=0,
                                                bg_size=config.get('bg_size', 256),
                                                gain=config.get('gain', 1.0),
                                                centroid_iter=5 if config.get('centroid_targets') else 0,
                                                verbose=verbose)

        target_obj['mag_calib'] = target_obj['mag'] + m['zero_fn'](target_obj['x'],
                                                                   target_obj['y'],
                                                                   target_obj['mag'])

        target_obj['mag_calib_err'] = np.hypot(target_obj['magerr'],
                                               m['zero_fn'](target_obj['x'],
                                                            target_obj['y'],
                                                            target_obj['mag'],
                                                            get_err=True))

        if config.get('centroid_targets'):
            # Centroiding might change target pixel positions - let's update sky positions too
            target_obj['ra'],target_obj['dec'] = wcs.all_pix2world(target_obj['x'], target_obj['y'], 0)

        # Local detection limit from background rms, if available
        if 'bg_fluxerr' in target_obj.colnames and np.any(target_obj['bg_fluxerr'] > 0):
            fluxerr = target_obj['bg_fluxerr']
        else:
            fluxerr = target_obj['fluxerr']
        target_obj['mag_limit'] = -2.5*np.log10(config.get('sn', 5)*fluxerr) + m['zero_fn'](
            target_obj['x'],
            target_obj['y'],
            target_obj['mag']
        )

        target_obj['mag_filter_name'] = m['cat_col_mag']

        if 'cat_col_mag1' in m.keys() and 'cat_col_mag2' in m.keys():
            target_obj['mag_color_name'] = '%s - %s' % (m['cat_col_mag1'], m['cat_col_mag2'])
            target_obj['mag_color_term'] = [m['color_term']]*len(target_obj)

        target_obj.write(os.path.join(basepath, 'target.vot'), format='votable', overwrite=True)
        log("Measured targets stored to file:target.vot")

        # Quality checks on photometry result
        check_photometry_quality(basepath, config, m, target_obj, log=log)

        # Create the cutouts from image based on the targets
        for i,tobj in enumerate(target_obj):
            cutout_name = f"targets/target_{i:04d}.cutout"
            target_title = "Primary target" if i == 0 else f"Secondary target {i}"

            if (not np.isfinite(tobj['x']) or not np.isfinite(tobj['y']) or
                tobj['x'] < 0 or tobj['y'] < 0 or
                tobj['x'] > image.shape[1] or tobj['y'] > image.shape[0]):
                log(f"{target_title} is outside image, skipping")
                continue

            cutout = cutouts.get_cutout(
                image, tobj, 30,
                mask=mask,
                header=header,
                time=time,
                # filtered=fimg if config.get('initial_r0') else None,
            )
            # Cutout from relevant HiPS survey
            cutout['template'] = templates.get_hips_image(
                guess_hips_survey(tobj['ra'], tobj['dec'], config['filter']),
                header=cutout['header'],
                get_header=False
            )

            try:
                os.makedirs(os.path.join(basepath, 'targets'))
            except OSError:
                pass

            cutouts.write_cutout(cutout, os.path.join(basepath, cutout_name))
            log(f"{target_title} cutouts stored to file:{cutout_name}")

            log(f"{target_title} flux is {tobj['flux']:.1f} +/- {tobj['fluxerr']:.1f} ADU")
            if tobj['flux'] > 0:
                mag_string = tobj['mag_filter_name']
                if 'mag_color_name' in target_obj.colnames and 'mag_color_term' in target_obj.colnames and tobj['mag_color_term'] is not None:
                    mag_string += ' ' + photometry.format_color_term(tobj['mag_color_term'], color_name=tobj['mag_color_name'])

                log(f"{target_title} magnitude is {mag_string} = {tobj['mag_calib']:.2f} +/- {tobj['mag_calib_err']:.2f}")
                log(f"{target_title} detected with S/N = {1/tobj['mag_calib_err']:.2f}")

            else:
                log(f"{target_title} not detected")


def transients_simple_image(filename, config, verbose=True, show=False):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    # Cleanup stale plots and files
    cleanup_paths(cleanup_transients_simple, basepath=basepath)

    # Image
    image,header = fits.getdata(filename, -1).astype(np.double), fits.getheader(filename, -1)

    fix_header(header)

    # Ensure all necessary files exist
    for _ in [
        'mask.fits', 'segmentation.fits', 'filtered.fits',
        'objects.vot', 'cat.vot', 'photometry.pickle',
    ]:
        if not os.path.exists(os.path.join(basepath, _)):
            raise RuntimeError(f"{_} not found, please rerun photometric calibration")

    # Mask
    mask = fits.getdata(os.path.join(basepath, 'mask.fits'), -1) > 0

    # Segmentation map
    segm = fits.getdata(os.path.join(basepath, 'segmentation.fits'), -1)

    # Filtered detection image
    fimg = fits.getdata(os.path.join(basepath, 'filtered.fits'), -1)

    # Objects
    obj = Table.read(os.path.join(basepath, 'objects.vot'))
    log(f"{len(obj)} objects loaded from file:objects.vot")

    # Reference catalogue from photometric calibration
    cat = Table.read(os.path.join(basepath, 'cat.vot'))

    # WCS
    wcs = get_wcs(filename, header=header, verbose=verbose)

    if wcs is None or not wcs.is_celestial:
        raise RuntimeError('No WCS astrometric solution')

    ra0,dec0,sr0 = astrometry.get_frame_center(wcs=wcs, width=image.shape[1], height=image.shape[0])
    pixscale = astrometry.get_pixscale(wcs=wcs)
    log(f"Field center is at {ra0:.3f} {dec0:.3f}, radius {sr0:.2f} deg, scale {3600*pixscale:.2f} arcsec/pix")

    fwhm = config.get('fwhm', 1.0)
    log(f"FWHM is {fwhm:.1f} pixels, or {3600*fwhm*pixscale:.2f} arcsec")

    # Time
    time = Time(config.get('time')) if config.get('time') else None
    if time is not None:
        log(f"Time is {time}")
        log(f"MJD is {Time(time).mjd}")

    log("\n---- Simple catalogue-based transient detection ----\n")

    # Restrict to the cone if center and radius are provided
    if config.get('simple_center') and config.get('simple_sr0'):
        center = resolve.resolve(config.get('simple_center'))
        sr0 = config.get('simple_sr0')
        log(f"Restricting the search to {sr0:.3f} deg around RA={center.ra.deg:.4f} Dec={center.dec.deg:.4f}")
        dist = astrometry.spherical_distance(obj['ra'], obj['dec'], center.ra.deg, center.dec.deg)
        obj = obj[dist < sr0]
        log(f"{len(obj)} objects inside the region")

    # Cross-check with objects detected in other tasks
    if config.get('simple_others'):
        log("Cross-checking with the objects detected in other tasks")
        for other in config.get('simple_others', '').split():
            if other.isdigit():
                otherpath = os.path.join(basepath, '..', other, 'objects.vot')
                if not os.path.exists(otherpath):
                    log(f"Task {other} has no detected objects")
                else:
                    obj1 = Table.read(otherpath)
                    oidx,_,__ = astrometry.spherical_match(obj['ra'], obj['dec'], obj1['ra'], obj1['dec'], 0.5*fwhm*pixscale)
                    idx = np.in1d(obj['NUMBER'], obj['NUMBER'][oidx])
                    obj = obj[idx]
                    log(f"Task {other}: {len(obj1)} objects, {len(obj)} matches")

        log(f"{len(obj)} objects after cross-checking")

    # Vizier catalogues to check
    vizier = guess_vizier_catalogues(ra0, dec0)
    log(f"Will check Vizier catalogues: {' '.join(vizier)}")

    # Filter based on flags and Vizier catalogs
    flagmask = 0x7fff - 0x0100 - 0x02 # Allow deblended and isophotal masked
    if not config.get('simple_prefilter'):
        flagmask -= 0x800 # Allow pre-filtered
    else:
        log("Will reject pre-filtered detections")

    if config.get('simple_mag_diff'):
        log(f"Will only keep matches brighter than catalogue by {config.get('simple_mag_diff'):.2f} mags")
    else:
        log("Will reject all positional matches")

    # Cross-match checker
    def checker_fn(xobj, xcat, catname):
        xidx = np.ones_like(xobj, dtype=bool)

        if config.get('simple_mag_diff'):
            # Get filter used for photometric calibration
            fname = config.get('cat_col_mag')
            if fname.endswith('mag'):
                fname = fname[:-3]

            cat_col_mag, cat_col_mag_err = guess_catalogue_mag_columns(fname, xcat)

            if cat_col_mag is not None:
                mag = xobj['mag_calib']
                if fname in ['U', 'B', 'V', 'R', 'I'] and cat_col_mag not in ['Umag', 'Bmag', 'Vmag', 'Rmag', 'Imag']:
                    # Convert to AB mags if using AB reference catalogue
                    mag += filter_ab_offset.get(fname, 0)

                diff = mag - xcat[cat_col_mag]

                if len(diff[np.isfinite(diff)]) > 10:
                    # Adjust zeropoint
                    diff -= np.nanmedian(diff)

                # TODO: take errors into account?..
                xidx = diff > -config.get('simple_mag_diff', 2.0)

        return xidx

    def rank_simple_candidates(xcand, xobj):
        # Rank candidates by a mixed score robust against bright artefacts:
        # S/N + morphology consistency with field stars + lightweight flag penalties.
        if len(xcand) == 0:
            return xcand

        score = np.zeros(len(xcand), dtype=np.float64)

        if 'flux' in xcand.colnames and 'fluxerr' in xcand.colnames:
            flux = np.array(xcand['flux'], dtype=np.float64)
            fluxerr = np.array(xcand['fluxerr'], dtype=np.float64)
            sn = flux / np.maximum(fluxerr, 1e-9)
            score += np.log10(1 + np.clip(sn, 0, None))

        if 'mag_calib' in xcand.colnames:
            mag = np.array(xcand['mag_calib'], dtype=np.float64)
            mag0 = np.nanmedian(mag)
            # Keep brightness contribution weak to avoid over-prioritizing saturated artefacts.
            score += 0.2*np.clip((mag0 - mag)/2.0, -2, 2)

        if 'fwhm' in xcand.colnames and 'fwhm' in xobj.colnames:
            ref_fwhm = np.array(xobj['fwhm'], dtype=np.float64)
            if 'flags' in xobj.colnames:
                ref_idx = np.array(xobj['flags']) == 0
            else:
                ref_idx = np.ones(len(xobj), dtype=bool)

            ref = ref_fwhm[ref_idx]
            ref = ref[np.isfinite(ref) & (ref > 0)]

            if len(ref) >= 10:
                ref_med = np.nanmedian(ref)
                ref_sigma = max(0.3, 0.5*(np.nanpercentile(ref, 84) - np.nanpercentile(ref, 16)))
                cfwhm = np.array(xcand['fwhm'], dtype=np.float64)
                score += 2.5*np.exp(-0.5*((cfwhm - ref_med)/ref_sigma)**2)
                # Reject extremely sharp detections often associated with hot pixels/cosmics.
                score -= 0.8*(cfwhm < 0.6*ref_med)

        if 'flags' in xcand.colnames:
            flags = np.array(xcand['flags'])
            # Keep deblended sources but with a mild penalty.
            score -= 0.2*((flags & 0x02) > 0)
            # Strongly penalize suspicious flags beyond deblended/isophotal masked bits.
            score -= 1.0*((flags & (0x7fff - 0x0100 - 0x02)) > 0)

        xcand = xcand.copy()
        xcand['transient_score'] = score
        xcand.sort('transient_score', reverse=True)

        log("Simple candidates ranked by transient_score (S/N + morphology + flags)")

        return xcand

    # Local catalogue pre-filter fallback.
    # This avoids relying solely on external CDS XMatch availability in crowded fields.
    if len(obj):
        cat_col_ra, cat_col_dec = guess_catalogue_radec_columns(cat)
        if cat_col_ra is None:
            log("Cannot guess local catalogue coordinate columns, skipping local pre-filter")
        else:
            sr_match = 0.5 * fwhm * pixscale
            oidx, cidx, _ = astrometry.spherical_match(
                obj['ra'], obj['dec'],
                cat[cat_col_ra], cat[cat_col_dec],
                sr_match
            )

            if len(oidx):
                remove_idx = oidx
                if config.get('simple_mag_diff'):
                    # Keep only matches that are not significantly brighter than catalogue.
                    # This mirrors checker_fn logic used for remote catalogue cross-matches.
                    cat_col_mag = config.get('cat_col_mag')
                    if cat_col_mag in cat.colnames and 'mag_calib' in obj.colnames:
                        diff = obj['mag_calib'][oidx] - cat[cat_col_mag][cidx]
                        if len(diff[np.isfinite(diff)]) > 10:
                            diff -= np.nanmedian(diff)
                        keep_match = diff > -config.get('simple_mag_diff', 2.0)
                        remove_idx = oidx[keep_match]
                    else:
                        log("Local catalogue pre-filter: magnitude columns missing, using positional-only match")

                if len(remove_idx):
                    keep = np.ones(len(obj), dtype=bool)
                    keep[remove_idx] = False
                    obj = obj[keep]
                    log(f"{len(obj)} remains after matching with local reference catalogue")

    candidates = pipeline.filter_transient_candidates(
        obj,
        sr=0.5*fwhm*pixscale,
        vizier=vizier,
        # Filter out all masked objects except for isophotal masked, and deblended
        flagged=True, flagmask=flagmask,
        # SkyBoT?..
        time=time,
        skybot=config.get('simple_skybot', True),
        vizier_checker_fn=checker_fn,
        verbose=verbose
    )

    # Additional filtering for blended stars
    # TODO: somehow integrate it into `filter_transient_candidates` proper
    if len(candidates) > 0 and config.get('simple_blends'):
        candidates = filter_vizier_blends(
            candidates,
            sr=0.5*fwhm*pixscale,
            sr_blend=2*fwhm*pixscale,
            vizier=vizier,
            fname=config.get('filter'),
            vizier_checker_fn=checker_fn,
            verbose=verbose
        )

    candidates = rank_simple_candidates(candidates, obj)

    # Restrict to 100 highest-ranked ones if there are too many
    if len(candidates) > 100:
        candidates = candidates[:100]
        log(f"Warning: too many candidates, limiting to first {len(candidates)}")

    cutout_names = []

    for cand in candidates:
        cutout = cutouts.get_cutout(
            image.astype(np.double),
            cand,
            30,
            header=header,
            mask=mask,
            footprint=(segm==cand['NUMBER']) if segm is not None else None,
            filtered=fimg if config.get('initial_r0') else None,
        )

        # Cutout from relevant HiPS survey
        cutout['template'] = templates.get_hips_image(
            guess_hips_survey(cand['ra'], cand['dec'], config['filter']),
            header=cutout['header'],
            get_header=False
        )

        jname = utils.make_jname(cand['ra'], cand['dec'])
        cutout_name = os.path.join('candidates_simple', jname + '.cutout')
        cutout_names.append(cutout_name)

        try:
            os.makedirs(os.path.join(basepath, 'candidates_simple'))
        except OSError:
            pass

        cutouts.write_cutout(cutout, os.path.join(basepath, cutout_name))

    log("\n---- Final list of candidates ----\n")

    if len(candidates):
        candidates['cutout_name'] = cutout_names

        log(f"{len(candidates)} candidates in total")

        candidates.write(os.path.join(basepath, 'candidates_simple.vot'), format='votable', overwrite=True)
        log("Candidates written to file:candidates_simple.vot")

        write_ds9_regions(
            os.path.join(basepath, 'candidates_simple.reg'),
            candidates,
            radius=config.get('rel_aper', 1.0)*pixscale*fwhm
        )
        log("Candidates written to file:candidates_simple.reg")

    else:
        log("No candidates found")



def subtract_image(filename, config, verbose=True, show=False):
    # Simple wrapper around print for logging in verbose mode only
    log = (verbose if callable(verbose) else print) if verbose else lambda *args,**kwargs: None

    basepath = os.path.dirname(filename)

    if settings.STDPIPE_PS1_CACHE:
        _cachedir = settings.STDPIPE_PS1_CACHE
    else:
        # Task-local
        _cachedir = os.path.join(basepath, 'cache')

    sub_verbose = verbose if config.get('sub_verbose') else False
    subtraction_mode = config.get('subtraction_mode') or 'detection'
    subtraction_method = config.get('subtraction_method') or 'hotpants'

    # Normalize config: replace None (from optional form fields) with safe defaults
    _coerce_sn(config)
    config['initial_aper'] = config.get('initial_aper') or 3
    config['initial_r0'] = config.get('initial_r0') if config.get('initial_r0') is not None else 0
    config['rel_aper'] = config.get('rel_aper') or 1
    config['rel_bg1'] = config.get('rel_bg1') or 5
    config['rel_bg2'] = config.get('rel_bg2') or 7
    config['spatial_order'] = config.get('spatial_order') if config.get('spatial_order') is not None else 2
    config['minarea'] = config.get('minarea') or 5
    config['hotpants_extra'] = config.get('hotpants_extra') or {'ko':0, 'bgo':0}
    config['sub_size'] = config.get('sub_size') or 1000
    config['sub_overlap'] = config.get('sub_overlap') if config.get('sub_overlap') is not None else 50

    # Cleanup stale plots and files
    cleanup_paths(cleanup_subtraction, basepath=basepath)

    # Image
    image,header = fits.getdata(filename, -1).astype(np.double), fits.getheader(filename, -1)

    fix_header(header)

    # Mask
    mask = fits.getdata(os.path.join(basepath, 'mask.fits'), -1) > 0

    # Photometric solution
    m = pickle_from_file(os.path.join(basepath, 'photometry.pickle'))

    # Objects
    obj = Table.read(os.path.join(basepath, 'objects.vot'))

    # Catalogue
    cat = Table.read(os.path.join(basepath, 'cat.vot'))

    # WCS
    wcs = get_wcs(filename, header=header, verbose=verbose)

    if wcs is None or not wcs.is_celestial:
        raise RuntimeError('No WCS astrometric solution')

    pixscale = astrometry.get_pixscale(wcs=wcs)

    # Time
    time = Time(config.get('time')) if config.get('time') else None

    log("\n---- Template selection ----\n")

    tname = config.get('template', 'ps1')
    tconf = supported_templates.get(tname)

    if tname == 'custom':
        log("Using custom template from custom_template.fits")

        if not os.path.exists(os.path.join(basepath, 'custom_template.fits')):
            raise RuntimeError("Custom template not found")

        custom_template = fits.getdata(os.path.join(basepath, 'custom_template.fits'), -1).astype(np.double)
        custom_header = fits.getheader(os.path.join(basepath, 'custom_template.fits'), -1)
        custom_wcs = WCS(custom_header)

        template_gain = config.get('custom_template_gain', 10000)
        template_saturation = config.get('custom_template_saturation', None)

        custom_mask = np.isnan(custom_template)
        if template_saturation:
            custom_mask |= custom_template >= template_saturation
    else:
        if tconf is None:
            raise RuntimeError(f"Unsupported template: {tname}")

        tfilter = None
        for _ in filter_mappings[config['filter']]:
            if _ in tconf['filters']:
                tfilter = _
                break

        template_gain = 10000 # Assume effectively noise-less

        # Allow manual override of template filter band
        tfilter_override = config.get('template_filter')
        if tfilter_override and tfilter_override in tconf.get('filters', {}):
            tfilter = tfilter_override
            log(f"Using {tconf['name']} in filter {tfilter} as a template (manual override)")
        else:
            log(f"Using {tconf['name']} in filter {tfilter} as a template")

        # Quick coverage check before heavy processing
        ra_c, dec_c, _ = astrometry.get_frame_center(wcs=wcs, width=image.shape[1], height=image.shape[0])
        if tname in ('ps1', 'ls'):
            check_fn = templates.point_in_ps1 if tname == 'ps1' else templates.point_in_ls
            if not check_fn(ra_c, dec_c):
                raise RuntimeError(
                    f"No {tconf['name']} coverage at RA={ra_c:.3f} Dec={dec_c:.3f} — "
                    f"try a different survey"
                )
        elif tname != 'custom' and isinstance(tconf.get('filters'), dict):
            hips_id = tconf['filters'].get(tfilter)
            if hips_id:
                log(f"Checking {tconf['name']} {tfilter}-band coverage...")
                probe = templates.get_hips_image(
                    hips_id, ra=ra_c, dec=dec_c,
                    width=64, height=64, fov=pixscale * 200,
                    get_header=False, normalize=False, verbose=False
                )
                if probe is None or not np.any(np.isfinite(probe)):
                    raise RuntimeError(
                        f"No {tconf['name']} {tfilter}-band coverage at "
                        f"RA={ra_c:.3f} Dec={dec_c:.3f} — try a different survey or band"
                    )
                log(f"Coverage confirmed")

    sub_size = config.get('sub_size', 1000)
    sub_overlap = config.get('sub_overlap', 50)

    classifier = None

    if subtraction_mode == 'detection':
        log('Transient detection mode activated')
        # We will split the image into nx x ny blocks
        nx = max(1, int(np.round(image.shape[1] / sub_size)))
        ny = max(1, int(np.round(image.shape[0] / sub_size)))
        log(f"Will split the image into {nx} x {ny} sub-images")
        split_fn = partial(pipeline.split_image, get_index=True, overlap=sub_overlap, nx=nx, ny=ny)

    elif config.get('target_ra') is not None:
        log('Forced photometry mode activated')
        # We will just crop the image
        nx, ny = 1, 1
        x0,y0 = wcs.all_world2pix(config['target_ra'], config['target_dec'], 0)
        log(f"Will crop the sub-image centered at {x0:.1f} {y0:.1f}")
        def split_fn(image, *args, **kwargs):
            result = pipeline.get_subimage_centered(image, *args, x0=x0, y0=y0, width=sub_size, **kwargs)

            yield [0] + result

    else:
        log('No target provided and transient detection is disabled, nothing to do')
        return

    if subtraction_method == 'zogy':
        log(f"\n---- Science image PSF ----\n")
        # Get global PSF model and object list with large aperture for flux normalization
        image_psf, image_psf_obj = psf.run_psfex(
            image, mask=mask,
            # Use spatially varying PSF if we have enough stars
            order=0 if len(obj[obj['flags'] == 0]) < 100 else 2,
            aper=2.0*config.get('fwhm', 3.0),
            gain=config.get('gain', 1.0),
            minarea=config.get('minarea', 3),
            r0=config.get('initial_r0', 0.0),
            sex_extra={'BACK_SIZE': config.get('bg_size', 256)},
            verbose=verbose,
            get_obj=True,
            _tmpdir=settings.STDPIPE_TMPDIR,
            _sex_exe=settings.STDPIPE_SEXTRACTOR,
            _exe=settings.STDPIPE_PSFEX)

        image_psf_obj = image_psf_obj[image_psf_obj['flags'] == 0]

    else:
        image_psf, image_psf_obj = None, None

    all_candidates = []
    cutout_names = []

    for i, x0, y0, image1, mask1, header1, wcs1, obj1, cat1, image_psf1, image_psf_obj1 in split_fn(
            image, mask, header, wcs, obj, cat, image_psf, image_psf_obj,
            get_origin=True, verbose=False):

        log(f"\n---- Sub-image {i}: {x0} {y0} - {x0 + image1.shape[1]} {y0 + image1.shape[0]} ----\n")

        fits.writeto(os.path.join(basepath, 'sub_image.fits'), image1, header1, overwrite=True)
        fits_write(os.path.join(basepath, 'sub_mask.fits'), mask1.astype(np.int8), header1, compress=True)

        # Get the template
        if tname == 'ps1' or tname == 'ls':
            log(f"Getting the template from original {tconf['name']} archive")
            tmpl,tmask = templates.get_survey_image_and_mask(
                tfilter, survey=tname, wcs=wcs1, shape=image1.shape,
                _cachedir=_cachedir, _cache_downscale = 1 if pixscale*3600 < 0.6 else 2,
                _tmpdir=settings.STDPIPE_TMPDIR,
                _exe=settings.STDPIPE_SWARP,
                verbose=sub_verbose)
            if tmask is None or tmpl is None:
                log(f"Warning: no template coverage from {tconf['name']} for sub-image {i} "
                    f"({x0},{y0})-({x0+image1.shape[1]},{y0+image1.shape[0]}) — skipping")
                continue

            if tname == 'ps1':
                tmask = tmask > 0
            elif tname == 'ls':
                # Bitmask for a given band, as described at https://www.legacysurvey.org/dr10/bitmasks/
                imask = 0x0000
                imask |= 0x0001 # not primary brick area
                # imask |= 0x0002 # bright star nearby
                # imask |= 0x0100 # WISE W1 (all masks)
                # imask |= 0x0200 # WISE W2 (all masks)
                imask |= 0x0400 # Bailed out processing
                # imask |= 0x0800 # medium-bright star
                # imask |= 0x1000 # SGA large galaxy
                # imask |= 0x2000 # Globular cluster

                if tfilter == 'g':
                    imask |= 0x0004 # g band saturated
                    imask |= 0x0020 # any ALLMASK_G bit set
                elif tfilter == 'r':
                    imask |= 0x0008 # r band saturated
                    imask |= 0x0040 # any ALLMASK_R bit set
                elif tfilter == 'i':
                    imask |= 0x4000 # i band saturated
                    imask |= 0x8000 # any ALLMASK_I bit set
                elif tfilter == 'z':
                    imask |= 0x0010 # z band saturated
                    imask |= 0x0080 # any ALLMASK_Z bit set

                tmask = (tmask & imask) > 0

            tmask |= np.isnan(tmpl)

            # Check that the template has enough valid (non-NaN) pixels to be usable
            valid_frac = np.mean(~tmask)
            if valid_frac < 0.1:
                log(f"Warning: template from {tconf['name']} for sub-image {i} has only "
                    f"{valid_frac:.1%} valid pixels — field likely not covered by this survey, skipping")
                continue

        elif tname == 'custom':
            log("Re-projecting custom template onto sub-image")

            tmpl,fp = reproject.reproject_adaptive((custom_template, custom_wcs), wcs1, image1.shape)
            tmask,fp = reproject.reproject_adaptive((custom_mask.astype(np.double), custom_wcs), wcs1, image1.shape)

            tmask = tmask > 0.5
            tmask |= fp < 0.5

        else:
            log("Getting the template from HiPS server")
            tmpl = templates.get_hips_image(tconf['filters'][tfilter], wcs=wcs1, shape=image1.shape,
                                            get_header=False,
                                            verbose=sub_verbose)
            if tmpl is None:
                log(f"Warning: no template coverage from {tconf['name']} for sub-image {i} "
                    f"({x0},{y0})-({x0+image1.shape[1]},{y0+image1.shape[0]}) — skipping")
                continue
            tmask = np.isnan(tmpl)
            if np.all(tmask):
                log(f"Warning: template from {tconf['name']} ({tfilter}-band) is all NaN for sub-image {i} — no coverage, skipping")
                continue

        # Estimate template FWHM
        tobj,tsegm = photometry.get_objects_sextractor(
            tmpl, mask=tmask, sn=5,
            extra_params=['NUMBER'],
            checkimages=['SEGMENTATION'],
            _tmpdir=settings.STDPIPE_TMPDIR,
            _exe=settings.STDPIPE_SEXTRACTOR
        )

        # Mask the footprints of masked objects
        # for _ in tobj[(tobj['flags'] & 0x100) > 0]:
        #     tmask |= tsegm == _['NUMBER']
        tobj = tobj[tobj['flags'] == 0]
        template_fwhm = np.median(tobj['fwhm'])

        fits.writeto(os.path.join(basepath, 'sub_template.fits'), tmpl, header1, overwrite=True)
        fits_write(os.path.join(basepath, 'sub_template_mask.fits'), tmask.astype(np.int8), header1, compress=True)

        if subtraction_method == 'zogy':
            # ZOGY

            # Estimate template PSF
            template_psf, template_psf_obj = psf.run_psfex(
                tmpl, mask=tmask,
                # Use spatially varying PSF?..
                order=0,
                aper=2.0*template_fwhm,
                gain=template_gain,
                minarea=config.get('minarea', 3),
                r0=config.get('initial_r0', 0.0),
                sex_extra={'BACK_SIZE': config.get('bg_size', 256)},
                verbose=sub_verbose,
                get_obj=True,
                _tmpdir=settings.STDPIPE_TMPDIR,
                _sex_exe=settings.STDPIPE_SEXTRACTOR,
                _exe=settings.STDPIPE_PSFEX)

            # Do the subtraction
            diff, S_corr, Fpsf, Fpsf_err = subtraction.run_zogy(
                image1, tmpl,
                mask=mask1, template_mask=tmask,
                image_gain=config.get('gain', 1.0),
                template_gain=template_gain,
                image_psf=image_psf1, template_psf=template_psf,
                image_obj=image_psf_obj1,
                template_obj=template_psf_obj,
                fit_scale=True, fit_shift=True,
                get_Fpsf=True,
                verbose=verbose
            )

            fits.writeto(os.path.join(basepath, 'sub_diff.fits'), diff, header1, overwrite=True)
            fits.writeto(os.path.join(basepath, 'sub_scorr.fits'), S_corr, header1, overwrite=True)
            fits.writeto(os.path.join(basepath, 'sub_fpsf.fits'), Fpsf, header1, overwrite=True)
            fits.writeto(os.path.join(basepath, 'sub_fpsferr.fits'), Fpsf_err, header1, overwrite=True)

            # Combined mask on the sub-image
            fullmask1 = mask1 | tmask

            conv = S_corr
            ediff = None
        else:
            # HOTPANTS
            fwhm = config.get('fwhm', 3.0)

            log(f"Using template FWHM = {template_fwhm:.1f} pix and image FWHM = {fwhm:.1f} pix")

            bg = sep.Background(
                image1.astype(np.double), mask=mask1,
                bw=32 if fwhm < 4 else 64,
                bh=32 if fwhm < 4 else 64
            )
            tbg = sep.Background(
                tmpl.astype(np.double), mask=tmask,
                bw=32 if template_fwhm < 4 else 64,
                bh=32 if template_fwhm < 4 else 64
            )

            res = subtraction.run_hotpants(
                image1 - bg.back(),
                tmpl - tbg.back(),
                mask=mask1,
                template_mask=tmask,
                get_convolved=True,
                get_scaled=True,
                get_noise=True,
                verbose=verbose,
                image_fwhm=fwhm,
                template_fwhm=template_fwhm,
                image_gain=config.get('gain', 1.0),
                template_gain=template_gain,
                err=True,
                extra=config.get('hotpants_extra', {'ko':0, 'bgo':0}),
                obj=obj1[obj1['flags']==0],
                _exe=settings.STDPIPE_HOTPANTS
            )

            if res is not None:
                diff,conv,sdiff,ediff = res
            else:
                # raise RuntimeError('Subtraction failed')
                log("Warning: Subtraction failed")
                continue

            dmask = diff == 1e-30 # Bad pixels

            # Combined mask on the sub-image
            fullmask1 = mask1 | tmask | dmask

            diff1 = diff.copy()
            diff1[fullmask1] = 0.0

            fits.writeto(os.path.join(basepath, 'sub_diff.fits'), diff1, header1, overwrite=True)
            fits.writeto(os.path.join(basepath, 'sub_sdiff.fits'), sdiff, header1, overwrite=True)
            fits.writeto(os.path.join(basepath, 'sub_conv.fits'), conv, header1, overwrite=True)
            fits.writeto(os.path.join(basepath, 'sub_ediff.fits'), ediff, header1, overwrite=True)

        # Post-subtraction steps

        if config.get('rel_bg1') and config.get('rel_bg2'):
            rel_bkgann = [config['rel_bg1'], config['rel_bg2']]
        else:
            rel_bkgann = None

        if config.get('target_ra') is not None and config.get('target_dec') is not None and subtraction_mode == 'target':
            # Target forced photometry
            log(f"\n---- Target forced photometry ----\n")

            target_obj = Table({'ra':[config['target_ra']], 'dec':[config['target_dec']]})
            target_obj['x'],target_obj['y'] = wcs1.all_world2pix(target_obj['ra'], target_obj['dec'], 0)

            if not (target_obj['x'] > 0 and target_obj['x'] < image1.shape[1] and
                    target_obj['y'] > 0 and target_obj['y'] < image1.shape[0]):
                raise RuntimeError("Target is outside the sub-image")

            log(f"Target position is {target_obj['ra'][0]:.3f} {target_obj['dec'][0]:.3f}"
                f" -> "
                f"{target_obj['x'][0]:.1f} {target_obj['y'][0]:.1f}")

            target_obj = photometry.measure_objects(
                target_obj, diff, mask=fullmask1,
                # FWHM should match the one used for calibration
                fwhm=config.get('fwhm'),
                aper=config.get('rel_aper', 1.0),
                bkgann=rel_bkgann,
                sn=0,
                # We assume no background
                bg=None,
                # ..and known error model
                err=ediff,
                gain=config.get('gain', 1.0),
                centroid_iter=5 if config.get('centroid_targets') else 0,
                verbose=sub_verbose
            )

            if config.get('centroid_targets'):
                # Centroiding might change target pixel positions - let's update sky positions too
                target_obj['ra'],target_obj['dec'] = wcs1.all_pix2world(target_obj['x'], target_obj['y'], 0)

            target_obj['mag_calib'] = target_obj['mag'] + m['zero_fn'](
                target_obj['x'] + x0,
                target_obj['y'] + y0,
                target_obj['mag']
            )

            target_obj['mag_calib_err'] = np.hypot(
                target_obj['magerr'],
                m['zero_fn'](
                    target_obj['x'] + x0,
                    target_obj['y'] + y0,
                    target_obj['mag'],
                    get_err=True
                )
            )

            # Local detection limit from background rms, if available
            if 'bg_fluxerr' in target_obj.colnames and np.any(target_obj['bg_fluxerr'] > 0):
                fluxerr = target_obj['bg_fluxerr']
            else:
                fluxerr = target_obj['fluxerr']
            target_obj['mag_limit'] = -2.5*np.log10(config.get('sn', 5)*fluxerr) + m['zero_fn'](
                target_obj['x'],
                target_obj['y'],
                target_obj['mag']
            )

            target_obj['mag_filter_name'] = m['cat_col_mag']

            if 'cat_col_mag1' in m.keys() and 'cat_col_mag2' in m.keys():
                target_obj['mag_color_name'] = '%s - %s' % (m['cat_col_mag1'], m['cat_col_mag2'])
                target_obj['mag_color_term'] = [m['color_term']]*len(target_obj)

            target_obj.write(os.path.join(basepath, 'sub_target.vot'), format='votable', overwrite=True)
            log("Measured target stored to file:sub_target.vot")

            # Quality checks on subtraction photometry result
            check_photometry_quality(basepath, config, m, target_obj, log=log)

            # Create the cutout from image based on the candidate
            cutout = cutouts.get_cutout(
                image1, target_obj[0], 30,
                mask=fullmask1,
                header=header1,
                time=time,
                diff=diff,
                template=tmpl,
                convolved=conv,
                err=ediff
            )
            cutouts.write_cutout(cutout, os.path.join(basepath, 'sub_target.cutout'))
            log("Target cutouts stored to file:sub_target.cutout")

            log(f"Target flux is {target_obj['flux'][0]:.1f} +/- {target_obj['fluxerr'][0]:.1f} ADU")
            if target_obj['flux'][0] > 0:
                mag_string = target_obj['mag_filter_name'][0]
                if 'mag_color_name' in target_obj.colnames and 'mag_color_term' in target_obj.colnames and target_obj['mag_color_term'][0] is not None:
                    sign = '-' if target_obj['mag_color_term'][0] > 0 else '+'
                    mag_string += f" {sign} {np.abs(target_obj['mag_color_term'][0]):.2f}*({target_obj['mag_color_name'][0]})"

                log(f"Target magnitude is {mag_string} = {target_obj['mag_calib'][0]:.2f} +/- {target_obj['mag_calib_err'][0]:.2f}")
                log(f"Target detected with S/N = {1/target_obj['mag_calib_err'][0]:.2f}")
            else:
                log("Target not detected")

        elif subtraction_mode == 'detection':
            # Transient detection mode
            log(f"Starting transient detection using edge size {sub_overlap}")

            if subtraction_method == 'zogy':
                # ZOGY
                sobj,segm = photometry.get_objects_sextractor(
                    S_corr,
                    mask=fullmask1,
                    thresh=config.get('sn', 5.0),
                    wcs=wcs1, edge=sub_overlap,
                    minarea=config.get('minarea', 1),
                    extra_params=['NUMBER'],
                    extra={
                        'ANALYSIS_THRESH': config.get('sn', 5.0),
                        'THRESH_TYPE': 'ABSOLUTE',
                        'BACK_TYPE': 'MANUAL',
                        'BACK_VALUE': 0,
                    },
                    checkimages=['SEGMENTATION'],
                    verbose=sub_verbose,
                    _tmpdir=settings.STDPIPE_TMPDIR,
                    _exe=settings.STDPIPE_SEXTRACTOR
                )
            else:
                # HOTPANTS
                sobj,segm = photometry.get_objects_sextractor(
                    diff,
                    mask=fullmask1,
                    err=ediff,
                    wcs=wcs1, edge=sub_overlap,
                    aper=config.get('initial_aper', 3.0),
                    gain=config.get('gain', 1.0),
                    sn=config.get('sn', 5.0),
                    minarea=config.get('minarea', 3),
                    extra_params=['NUMBER', 'MAG_AUTO', 'ISOAREA_IMAGE'],
                    extra={'BACK_SIZE': config.get('bg_size', 256)},
                    checkimages=['SEGMENTATION'],
                    verbose=sub_verbose,
                    _tmpdir=settings.STDPIPE_TMPDIR,
                    _exe=settings.STDPIPE_SEXTRACTOR
                )

            sobj = photometry.measure_objects(
                sobj, diff, mask=fullmask1,
                # FWHM should match the one used for calibration
                fwhm=config.get('fwhm'),
                aper=config.get('rel_aper', 1.0),
                bkgann=rel_bkgann,
                sn=config.get('sn', 5.0),
                # We assume no background
                bg=None,
                # ..and known error model
                err=ediff,
                gain=config.get('gain', 1.0),
                verbose=sub_verbose
            )

            # Ensure flags are 32-bit ints to avoid OverflowError during bitmask operations
            if 'flags' in sobj.colnames and sobj['flags'].dtype != np.int32:
                sobj['flags'] = sobj['flags'].astype(np.int32)

            if len(sobj):
                sobj['mag_calib'] = sobj['mag'] + m['zero_fn'](
                    sobj['x'] + x0,
                    sobj['y'] + y0,
                    sobj['mag']
                )
                sobj['mag_calib_err'] = np.hypot(
                    sobj['magerr'],
                    m['zero_fn'](
                        sobj['x'] + x0,
                        sobj['y'] + y0,
                        sobj['mag'],
                        get_err=True
                    )
                )

                # TODO: Improve limiting mag estimate
                sobj['mag_limit'] = -2.5*np.log10(config.get('sn', 5)*sobj['fluxerr']) + m['zero_fn'](
                    sobj['x'],
                    sobj['y'],
                    sobj['mag']
                )

                sobj['mag_filter_name'] = m['cat_col_mag']

                if 'cat_col_mag1' in m.keys() and 'cat_col_mag2' in m.keys():
                    sobj['mag_color_name'] = '%s - %s' % (m['cat_col_mag1'], m['cat_col_mag2'])
                    sobj['mag_color_term'] = [m['color_term']]*len(sobj)

            log(f"{len(sobj)} transient candidates found in difference image")

            # Restrict to the cone if center and radius are provided
            if config.get('filter_center') and config.get('filter_sr0'):
                # TODO: resolve the center only once
                center = resolve.resolve(config.get('filter_center'))
                sr0 = config.get('filter_sr0')
                log(f"Restricting the search to {sr0:.3f} deg around RA={center.ra.deg:.4f} Dec={center.dec.deg:.4f}")
                dist = astrometry.spherical_distance(sobj['ra'], sobj['dec'], center.ra.deg, center.dec.deg)
                sobj = sobj[dist < sr0]
                log(f"{len(sobj)} candidates inside the region")

            # Pre-filter detections if requested
            if config.get('filter_prefilter') and len(sobj):
                if classifier is None:
                    # Prepare the classifier based on SExtractor shape parameters
                    classifier = filter_sextractor_detections(obj, verbose=False, return_classifier=True)

                fidx = filter_sextractor_detections(sobj, verbose=verbose, classifier=classifier)
                sobj = sobj[fidx]
                log(f"{len(sobj)} candidates left after pre-filtering")

            vizier = ['gaiaedr3', 'ps1', 'skymapper', ] if config.get('filter_vizier') else []

            # Filter out catalogue objects
            candidates = pipeline.filter_transient_candidates(
                sobj,
                cat=None, # cat,
                sr=0.5*pixscale*config.get('fwhm', 1.0),
                pixscale=pixscale,
                vizier=vizier,
                # Filter out any flags except for 0x100 which is isophotal masked
                flagged=True, flagmask=0xfe00,
                time=time,
                skybot=config.get('filter_skybot', False),
                verbose=verbose
            )

            # diff[fullmask1] = np.nan # For better visuals

            Ngood = 0
            for cand in candidates:
                cutout = cutouts.get_cutout(
                    image1, cand, 30,
                    mask=fullmask1,
                    diff=diff,
                    template=tmpl,
                    convolved=conv,
                    err=ediff,
                    footprint=(segm==cand['NUMBER']) if segm is not None else None,
                    time=time,
                    header=header1
                )

                if config.get('filter_adjust'):
                    # Try to apply some sub-pixel adjustments to fix dipoles etc
                    if cutouts.adjust_cutout(
                            cutout, max_shift=1, max_scale=1.3,
                            inner=int(np.ceil(2.0*config.get('fwhm'))),
                            normalize=False, verbose=False
                    ):
                        if cutout['meta']['adjust_pval'] > 0.01:
                            continue
                        if cutout['meta']['adjust_chi2'] < 0.33*cutout['meta']['adjust_chi2_0']:
                            continue

                jname = utils.make_jname(cand['ra'], cand['dec'])
                cutout_name = os.path.join(basepath, 'candidates', jname + '.cutout')

                try:
                    os.makedirs(os.path.join(basepath, 'candidates'))
                except OSError:
                    pass

                cutouts.write_cutout(cutout, cutout_name)

                all_candidates.append(cand)
                cutout_names.append(os.path.join('candidates', jname + '.cutout'))

                Ngood += 1

            if config.get('filter_adjust'):
                log(f"{Ngood} candidates remaining after sub-pixel adjustment routine")

    if subtraction_mode == 'detection':
        log("\n---- Final list of candidates ----\n")

        if len(all_candidates):
            all_candidates = vstack(all_candidates)
            all_candidates['cutout_name'] = cutout_names

            log(f"{len(all_candidates)} candidates in total")

            all_candidates.write(os.path.join(basepath, 'candidates.vot'), format='votable', overwrite=True)
            log("Candidates written to file:candidates.vot")

            write_ds9_regions(
                os.path.join(basepath, 'candidates.reg'),
                candidates,
                radius=config.get('rel_aper', 1.0)*pixscale*config.get('fwhm')
            )
            log("Candidates written to file:candidates.reg as DS9 regions")

        else:
            log("No candidates found")
