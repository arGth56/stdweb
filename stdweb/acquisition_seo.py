"""Optional acquisition / SEO (référencement). Enable with ACQUISITION_SEO_ENABLED in .env."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from xml.etree.ElementTree import Element, SubElement, tostring


def _public_base() -> str:
    return (getattr(settings, 'SITE_PUBLIC_BASE_URL', '') or '').rstrip('/')


def _page_robots(request) -> str:
    path = request.path.rstrip('/') or '/'
    indexable = {
        '/',
        '/login',
        '/register',
        '/upload',
        '/password/reset',
    }
    if path in indexable:
        return 'index, follow'
    if path.startswith('/password/reset/'):
        return 'noindex, follow'
    return 'noindex, nofollow'


def _page_meta(request):
    """Default title + description per public URL name."""
    name = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
    site = getattr(settings, 'SEO_SITE_NAME', 'STDWeb')
    default_desc = getattr(settings, 'SEO_DEFAULT_DESCRIPTION', '')
    pages = {
        'login': (
            f'Log in — {site} (free science-grade photometry)',
            default_desc,
        ),
        'register': (
            f'Free account — {site}',
            'Create a free STDWeb account: science-grade Gaia-calibrated photometry, astrometry, '
            'subtraction, and transient detection on the web — no install, powered by STDPipe.',
        ),
        'upload': (
            f'{site} — free science-grade photometry on the web',
            default_desc,
        ),
        'index': (
            f'{site} — free science-grade photometry on the web',
            default_desc,
        ),
        'password_reset': (
            f'Password reset — {site}',
            f'Reset your {site} account password.',
        ),
    }
    return pages.get(name, (site, default_desc))


def seo_context(request):
    if not getattr(settings, 'ACQUISITION_SEO_ENABLED', False):
        return {}

    base = _public_base()
    canonical = f'{base}{request.path}' if base else request.build_absolute_uri(request.path)
    page_title, page_description = _page_meta(request)
    og_image = getattr(settings, 'SEO_OG_IMAGE', '')

    return {
        'seo_enabled': True,
        'seo_robots': _page_robots(request),
        'seo_canonical': canonical,
        'seo_site_name': getattr(settings, 'SEO_SITE_NAME', 'STDWeb'),
        'seo_page_title': page_title,
        'seo_page_description': page_description,
        'seo_keywords': getattr(settings, 'SEO_KEYWORDS', ''),
        'seo_og_image': og_image,
        'seo_og_image_abs': f'{base}{og_image}' if base and og_image.startswith('/') else og_image,
        'seo_organization_name': getattr(settings, 'SEO_ORGANIZATION_NAME', '') or settings.HOSTING_SITE_NAME,
        'seo_organization_url': getattr(settings, 'SEO_ORGANIZATION_URL', '') or settings.HOSTING_SITE_URL,
        'seo_html_lang': getattr(settings, 'SEO_HTML_LANG', 'en'),
    }


def robots_txt(_request):
    if not getattr(settings, 'ACQUISITION_SEO_ENABLED', False):
        return HttpResponse("User-agent: *\nDisallow: /\n", content_type='text/plain')

    base = _public_base()
    lines = [
        'User-agent: *',
        'Disallow: /admin/',
        'Disallow: /api/',
        'Disallow: /tasks/',
        'Disallow: /files/',
        'Disallow: /queue/',
        'Disallow: /skyportal/',
        'Allow: /login/',
        'Allow: /register/',
        'Allow: /upload/',
        'Allow: /password/reset/',
        'Allow: /$',
    ]
    if base:
        lines.append(f'Sitemap: {base}/sitemap.xml')
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain')


def sitemap_xml(_request):
    if not getattr(settings, 'ACQUISITION_SEO_ENABLED', False):
        return HttpResponse('', status=404)

    base = _public_base()
    if not base:
        return HttpResponse('', status=404)

    url_names = ['index', 'login', 'register', 'upload', 'password_reset']
    if not getattr(settings, 'REGISTRATION_OPEN', False):
        url_names = [n for n in url_names if n != 'register']

    urlset = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for name in url_names:
        loc = f'{base}{reverse(name)}'
        url_el = SubElement(urlset, 'url')
        SubElement(url_el, 'loc').text = loc
        SubElement(url_el, 'changefreq').text = 'monthly'
        if name in ('index', 'upload'):
            SubElement(url_el, 'priority').text = '1.0'
        elif name == 'login':
            SubElement(url_el, 'priority').text = '0.9'
        else:
            SubElement(url_el, 'priority').text = '0.6'

    body = tostring(urlset, encoding='unicode', default_namespace='')
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    return HttpResponse(xml, content_type='application/xml')
