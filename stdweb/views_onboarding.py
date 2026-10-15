from django.http import JsonResponse

from . import onboarding


def state(request):
    return JsonResponse(onboarding.page_payload(request))
