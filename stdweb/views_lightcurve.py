"""Public lightcurve URLs redirect to telegram for now."""
from django.http import HttpResponsePermanentRedirect
from django.urls import reverse


def lightcurve_slash(request):
    return HttpResponsePermanentRedirect(reverse('telegram'))


def index(request):
    return HttpResponsePermanentRedirect(reverse('telegram'))


def target(request, ra, dec):
    return HttpResponsePermanentRedirect(reverse('telegram'))


def target_json(request, ra, dec):
    return HttpResponsePermanentRedirect(reverse('telegram'))


def target_csv(request, ra, dec):
    return HttpResponsePermanentRedirect(reverse('telegram'))
