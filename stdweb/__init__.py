# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app

__all__ = ('celery_app',)

# -----------------------------------------------------------------------------
# Networking hygiene: enforce a sensible default timeout for *all* outgoing
# HTTP requests performed via the popular `requests` library.  This prevents
# our Celery workers from hanging indefinitely when an external service (e.g.
# CDS Sesame, Fink portal) becomes unresponsive.
# -----------------------------------------------------------------------------

import os

try:
    import requests  # The library is optional at runtime but usually present.

    # Only patch once to avoid stacking wrappers during Django's autoreloads.
    if not getattr(requests, "_stdweb_patched", False):

        _DEFAULT_TIMEOUT = float(os.getenv("STDWEB_REQUEST_TIMEOUT", "10"))

        _orig_request = requests.request

        def _request_with_timeout(method, url, **kwargs):  # type: ignore[override]
            """Wrapper around requests.request that injects a default timeout."""
            kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
            return _orig_request(method, url, **kwargs)

        requests.request = _request_with_timeout  # type: ignore[assignment]
        requests._stdweb_patched = True  # type: ignore[attr-defined]

except ModuleNotFoundError:
    # `requests` may be absent in some minimal test environments; skip patch.
    pass
