"""How a call's time grows with its input: the guard of every linear-time test.

A wall-clock limit per call (0.2 s) failed a linear pattern on a loaded machine: other test
suites ran beside this one, and the same call took 0.15 s or 0.20 s. What tells linear from
quadratic is not the time but its growth: when the input doubles, a linear call takes about
twice as long, a quadratic one about four times. `growth_problem()` times the call on inputs of
doubling sizes and flags a step that grew more than `RATIO` times — confirmed by the best of
five runs at both sizes, so one slow moment of the machine never fails a test. Times under
`FLOOR` are noise and never judged by their ratio; any time over `cap` fails outright (a
quadratic call on 200 KB takes minutes, a linear one milliseconds).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

RATIO = 3.0  # doubling the input: linear about 2x, quadratic about 4x
FLOOR = 0.02  # seconds: below this, a loaded machine's noise decides any ratio
CAP = 2.0  # seconds for one call at the largest size: generous for linear, hopeless for quadratic


def took[T](call: Callable[[T], object], subject: T) -> float:
    started = time.perf_counter()
    call(subject)
    return time.perf_counter() - started


def best[T](call: Callable[[T], object], subject: T, runs: int = 5) -> float:
    return min(took(call, subject) for _ in range(runs))


def growth_problem[T](
    call: Callable[[T], object],
    make: Callable[[int], T],
    sizes: Sequence[int],
    *,
    cap: float = CAP,
) -> str:
    """The empty string when `call(make(n))` grows at most linearly over `sizes` (each about
    double the one before) and stays under `cap`; otherwise what went wrong. Stops at the first
    bad step, so a quadratic call fails at the small sizes, in seconds."""
    previous: float | None = None
    for index, size in enumerate(sizes):
        subject = make(size)
        now = took(call, subject)
        grew = previous is not None and now > FLOOR and now > RATIO * previous
        if now > cap or grew:  # confirm before judging: the best of five, at both sizes
            now = best(call, subject)
            if previous is not None:
                previous = best(call, make(sizes[index - 1]))
            if now > cap:
                return f"took {now:.3f} s at size {size:,}: the cap is {cap} s"
            if previous is not None and now > FLOOR and now > RATIO * previous:
                return (
                    f"grew {now / max(previous, 1e-9):.1f}x when its input doubled: "
                    f"{previous:.3f} s at size {sizes[index - 1]:,}, {now:.3f} s at {size:,}"
                )
        previous = now
    return ""
