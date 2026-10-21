"""tests/scaling.py: the time guard every linear-time test uses. It judges how a call's time
grows as its input doubles — linear about 2x, quadratic about 4x — instead of a wall-clock limit
per call, which a loaded machine breaks (a linear pattern measured 0.20 s against 0.2 s
while other test suites ran)."""

import re
import time

from tests.scaling import growth_problem


def test_a_quadratic_call_is_caught_at_small_sizes() -> None:
    quadratic = re.compile(r"a*b")  # search restarts at every "a" and walks to the end
    # A size under 8_000 finishes under the 0.02 s floor, so it cannot be the judged step.
    # 8_000 to 32_000 still finishes under the 2 s cap.
    problem = growth_problem(quadratic.search, lambda n: "a" * n, (8_000, 16_000, 32_000))
    assert problem.startswith("grew ") and "x when its input doubled" in problem


def test_a_linear_call_passes_however_slow_its_steps() -> None:
    assert growth_problem(lambda text: text.count("a"), lambda n: "ab" * n, (50_000, 100_000)) == ""


def test_a_ratio_under_the_noise_floor_is_ignored() -> None:
    """0.005 s then 0.030 s is a 6x jump, and the smaller time is under the 0.02 s floor."""

    def pause(size: int) -> None:
        time.sleep(0.005 if size == 1 else 0.030)

    assert growth_problem(pause, lambda size: size, (1, 2)) == ""


def test_a_call_over_the_cap_fails_whatever_its_growth() -> None:
    problem = growth_problem(lambda _: sum(range(3_000_000)), lambda n: n, (1, 2), cap=0.001)
    assert problem.startswith("took ") and "the cap is 0.001 s" in problem
