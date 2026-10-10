"""POST with patience: API providers are sometimes briefly overloaded."""
from __future__ import annotations

import time

import requests

BUSY = {429, 500, 502, 503, 504}     # rate limit or temporary server trouble
WAITS = (2, 5, 10)                   # seconds before the 2nd, 3rd and 4th attempt


def post_with_retry(url: str, **kwargs) -> requests.Response:
    """Try up to 4 times while the service says it is busy. Other errors return at once."""
    response = None
    for wait in (0,) + WAITS:
        if wait:
            time.sleep(wait)
        try:
            response = requests.post(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            response = None
            continue
        if response.status_code not in BUSY:
            return response
    if response is None:
        raise RuntimeError(f"Could not reach {url}: {last_error}")
    return response
