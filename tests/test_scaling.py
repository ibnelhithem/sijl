"""tests/scaling.py: the time guard every linear-time test uses. It judges how a call's time
grows as its input doubles — linear about 2x, quadratic about 4x — instead of a wall-clock limit
per call, which a loaded machine breaks (a linear pattern measured 0.20 s against 0.2 s
while other test suites ran)."""

import re

from tests.scaling import growth_problem


def test_a_quadratic_call_is_caught_at_small_sizes() -> None:
    quadratic = re.compile(r"a*b")  # search restarts at every "a" and walks to the end
    problem = growth_problem(quadratic.search, lambda n: "a" * n, (1_000, 2_000, 4_000, 8_000))
    assert problem.startswith("grew ") and "x when its input doubled" in problem


def test_a_linear_call_passes_however_slow_its_steps() -> None:
    assert growth_problem(lambda text: text.count("a"), lambda n: "ab" * n, (50_000, 100_000)) == ""


def test_a_call_over_the_cap_fails_whatever_its_growth() -> None:
    problem = growth_problem(lambda _: sum(range(3_000_000)), lambda n: n, (1, 2), cap=0.001)
    assert problem.startswith("took ") and "the cap is 0.001 s" in problem
