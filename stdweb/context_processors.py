from django.conf import settings


def expose_settings(request):
    return {
        'settings': settings,
    }


def acquisition_seo(request):
    from stdweb import acquisition_seo as seo

    return seo.seo_context(request)
