#!/usr/bin/env python3
"""Time how long grading takes, with a fresh cache each run so every API call is real.

    python time_grade.py speech.m4a --runs 5
    python time_grade.py speech.m4a --runs 3 --slow-first 20

Every API call is listed with when it was sent and answered.
--slow-first N makes the first request to each API stall for N seconds, the way a
busy provider sometimes does, so you can watch the backup request take over.
"""
import argparse
import statistics
import tempfile
import threading
import time

from grader import grade, http


REAL_POST = http.requests.post


def watch_requests(clock: list, stall: float | None) -> None:
    """Log every API call with its timing; optionally stall the first call to each host."""
    stalled, lock = set(), threading.Lock()

    def post(url, **kwargs):
        host = url.split("/")[2]
        name = (kwargs.get("json") or {}).get("model") or host
        with lock:
            first = stall and host not in stalled
            stalled.add(host)
        began = time.perf_counter() - clock[0]
        if first:
            time.sleep(stall)
        try:
            response = REAL_POST(url, **kwargs)
            outcome = response.status_code
        except Exception as error:  # noqa: BLE001
            outcome = type(error).__name__
            raise
        finally:
            note = f"  (stalled {stall:g} s on purpose)" if first else ""
            print(f"    {name:<28} sent at {began:5.1f} s, answered at "
                  f"{time.perf_counter() - clock[0]:5.1f} s -> {outcome}{note}")
        return response

    http.requests.post = post


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", help="recording (webm, mp3, wav, m4a ...)")
    parser.add_argument("--topic", default="Should homework be banned in elementary school?")
    parser.add_argument("--side", choices=["yes", "no"], default="yes")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--slow-first", type=float, metavar="N", help="stall the first request to each API for N s")
    args = parser.parse_args()

    times = []
    for run in range(1, args.runs + 1):
        clock = [time.perf_counter()]
        watch_requests(clock, args.slow_first)
        started = clock[0]
        report = grade(args.audio, args.topic, args.side, cache_dir=tempfile.mkdtemp())
        times.append(time.perf_counter() - started)
        overall = report["overall"]
        print(f"run {run}: {times[-1]:5.1f} s   overall {overall['score']} {overall['letter']}")

    if len(times) > 1:
        print(f"\nfastest {min(times):.1f} s   median {statistics.median(times):.1f} s   slowest {max(times):.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
