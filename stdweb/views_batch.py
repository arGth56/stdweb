"""Forward /batch/ to the stdbatch origin (127.0.0.1:8001).

stdbatch is a separate process. This exists so https://stdweb.org.uk/batch/
shares the stdweb login cookie. Prefer an nginx `location /batch/` when sudo
is available; until then gunicorn (user pyl) can serve this proxy.
"""
from __future__ import annotations

import http.client

from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.views.decorators.csrf import csrf_exempt

STDBATCH_HOST = "127.0.0.1"
STDBATCH_PORT = 8001

_DROP_REQ = {"host", "content-length", "connection", "expect"}
_DROP_RESP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def batch_slash(request):
    qs = request.META.get("QUERY_STRING")
    url = "/batch/"
    if qs:
        url += "?" + qs
    return HttpResponsePermanentRedirect(url)


@csrf_exempt
def batch_proxy(request):
    path = request.get_full_path()
    headers = {}
    for key, value in request.headers.items():
        if key.lower() in _DROP_REQ:
            continue
        headers[key] = value
    # Keep Content-Length so stdbatch can size the upload; http.client will
    # recompute it from body if omitted, but a missing length can break some paths.
    if request.method not in ("GET", "HEAD") and request.META.get("CONTENT_LENGTH"):
        headers["Content-Length"] = request.META["CONTENT_LENGTH"]
    body = None if request.method in ("GET", "HEAD") else request.body
    conn = http.client.HTTPConnection(STDBATCH_HOST, STDBATCH_PORT, timeout=300)
    try:
        conn.request(request.method, path, body=body, headers=headers)
        upstream = conn.getresponse()
        payload = upstream.read()
        status = upstream.status
        content_type = upstream.getheader("content-type")
        out_headers = [
            (k, v)
            for k, v in upstream.getheaders()
            if k.lower() not in _DROP_RESP and k.lower() not in ("content-type", "content-length")
        ]
    finally:
        conn.close()

    response = HttpResponse(payload, status=status, content_type=content_type)
    for key, value in out_headers:
        response[key] = value
    return response
