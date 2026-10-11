"""POST with patience: API providers are sometimes briefly overloaded, or simply slow.

Two defences:
  post_with_retry   retries busy answers, but never past a deadline, and never waits
                    minutes on one request (TIMEOUT).
  first_success     hedging: if an attempt is much slower than usual, start the next
                    one alongside it and take whichever succeeds first.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

import requests

BUSY = {429, 500, 502, 503, 504}     # rate limit or temporary server trouble
OVERLOADED = BUSY - {429}            # for single-model callers: wait out overload on the same model
# For callers with fallback models: a rate limit (429) or an overloaded model (503) usually
# lasts longer than our waits, so hand over to the next model at once; retry only plain
# server errors on the same model.
RETRY_SAME_MODEL = BUSY - {429, 503}
WAITS = (2, 5, 10)                   # seconds before the 2nd, 3rd and 4th attempt
TIMEOUT = (5, 30)                    # seconds to connect, seconds to wait for the answer
DEADLINE = 45                        # seconds one post_with_retry call may take in total


def post_with_retry(url: str, deadline: float | None = None, retry_on: set[int] = BUSY,
                    cancelled: threading.Event | None = None, **kwargs) -> requests.Response:
    """Try up to 4 times while the answer is in retry_on. Other answers return at once.

    deadline   seconds from now after which no new attempt starts (default DEADLINE).
    cancelled  once set, no new attempt starts (another attempt already won).
    """
    stop = time.monotonic() + (DEADLINE if deadline is None else deadline)
    kwargs.setdefault("timeout", TIMEOUT)
    response, last_error = None, None
    for wait_s in (0,) + WAITS:
        if wait_s:
            if time.monotonic() + wait_s >= stop:
                break
            time.sleep(wait_s)
        if cancelled is not None and cancelled.is_set():
            break
        try:
            response = requests.post(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            response = None
            continue
        if response.status_code not in retry_on:
            return response
    if response is None:
        raise RuntimeError(f"Could not reach {url}: {last_error}")
    return response


def first_success(attempts: list[tuple[str, Callable[[threading.Event], requests.Response]]],
                  hedge_after: float, on_failure: bool = True) -> tuple[requests.Response, list[str]]:
    """Run attempts in order until one returns a status below 400.

    Normally this is sequential: an attempt that fails quickly hands over to the next.
    An attempt still running after hedge_after seconds gets the next attempt started
    alongside it, and the first success wins. With on_failure=False the next attempt
    is only ever a hedge against slowness, never a retry after a failure.
    Each attempt is called with an Event that is set once the race is over, so a
    losing attempt stops retrying. Returns (response, problems seen).
    Raises RuntimeError listing every problem if all attempts fail.
    """
    queue = list(attempts)
    pending, problems, over = {}, [], threading.Event()
    pool = ThreadPoolExecutor(max_workers=len(attempts))

    def launch() -> None:
        label, call = queue.pop(0)
        pending[pool.submit(call, over)] = label

    launch()
    try:
        while pending:
            done, _ = wait(pending, timeout=hedge_after if queue else None, return_when=FIRST_COMPLETED)
            if not done:                     # slow, not failed: race the next attempt
                launch()
                continue
            for future in done:
                label = pending.pop(future)
                try:
                    response = future.result()
                except Exception as error:   # noqa: BLE001  (reported, then the next attempt runs)
                    problems.append(f"{label}: {error}")
                    continue
                if response.status_code < 400:
                    return response, problems
                problems.append(f"{label}: {response.status_code} {response.text[:300]}")
            if not pending and queue and on_failure:
                launch()
    finally:
        # Don't wait for a losing attempt: it stops retrying, and its timeout ends it.
        over.set()
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("\n".join(problems))
