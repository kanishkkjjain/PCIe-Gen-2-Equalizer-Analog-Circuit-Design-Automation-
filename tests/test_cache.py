"""The durable evaluation cache: what it must survive.

Pure tests -- no PDK, no ngspice. They exist because the failure they guard
against is silent and expensive: roughly 1 650 labelled evaluations from
earlier runs of this project were lost to an in-memory-only cache, and a
surrogate model is worth exactly the labels it is trained on.
"""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import pytest

from eqrl import pdk
from eqrl.cache import EvalLog, key_of, load, write_atomic
from eqrl.spice import MeasCond, Metrics


@pytest.fixture
def d(tmp_path):
    return tmp_path


def _write(d, tag, n, meas, corners=None):
    corners = corners or pdk.robust_search_corners()
    with EvalLog(tag, d) as log:
        for i in range(n):
            log.append((0.1 * i, 0.2), corners[i % len(corners)],
                       Metrics(ok=True, peaking_db=5.5 + i,
                               f_peak=float("nan")), meas)


def test_round_trip_preserves_records_and_nan(d):
    meas = MeasCond()
    _write(d, "t", 4, meas)
    r = load("t", d, meas=meas)
    assert len(r) == 4 and r.n_skipped_bad == 0
    rec = next(iter(r.records.values()))
    assert rec["metrics"]["ok"] is True
    # NaN must survive the round trip AS NaN. json's default NaN token reads
    # back as float('nan'); allow_nan=False (correct for report.json) would
    # raise here, and mapping NaN to None would lose "measured but invalid"
    # versus "never measured".
    assert math.isnan(rec["metrics"]["f_peak"])


def test_a_killed_write_costs_exactly_one_record(d):
    """A process killed between write() and fsync() leaves a partial last line."""
    meas = MeasCond()
    _write(d, "t", 4, meas)
    p = d / "t.jsonl"
    p.write_text(p.read_text() + '{"key": [0.9, 0.2], "corner": "tt_1.8')

    r = load("t", d, meas=meas)
    assert len(r) == 4, "records written before the kill must all survive"
    assert r.n_skipped_bad == 1, "the torn line is skipped, not raised on"


def test_appending_after_a_torn_line_does_not_eat_the_next_record(d):
    """The regression that this bug actually is.

    A torn final line has no newline, so a naive append CONCATENATES onto the
    fragment and both records become one unparseable line -- the resumed run
    silently loses its first result too. Measured before the fix: 4 records
    plus one append loaded back as 4, not 5.
    """
    meas = MeasCond()
    _write(d, "t", 4, meas)
    p = d / "t.jsonl"
    p.write_text(p.read_text() + '{"key": [0.9, 0.2], "corner": "tt_1.8')

    with EvalLog("t", d) as log:                      # the resumed run
        log.append((0.5, 0.2), pdk.robust_search_corners()[0],
                   Metrics(ok=True), meas)

    r = load("t", d, meas=meas)
    assert len(r) == 5, "the append after a torn tail was swallowed"
    assert r.n_skipped_bad == 1


def test_records_measured_under_other_conditions_are_rejected(d):
    """The tx_swing_pp_v bug class, caught at read time rather than trusted."""
    meas = MeasCond()
    _write(d, "t", 4, meas)
    other = dataclasses.replace(meas, tx_swing_pp_v=0.800)

    r = load("t", d, meas=other)
    assert len(r) == 0 and r.n_skipped_meas == 4
    # ...but they are still readable when the caller says it knows
    assert len(load("t", d, meas=other, allow_meas_mismatch=True)) == 4


def test_key_matches_the_objective_key():
    """A design simulated by the search must be recognised as done here.

    If the two roundings ever drift, resume silently re-simulates everything
    and the cache grows duplicates instead of progress.
    """
    from eqrl.env import Objective
    u = [0.123456789, 0.98765432] + [0.5] * 9
    assert key_of(u) == Objective._key(u)


def test_reopening_appends_rather_than_truncates(d):
    meas = MeasCond()
    _write(d, "t", 2, meas)
    _write(d, "t", 2, meas)
    r = load("t", d, meas=meas)
    assert r.n_lines == 4
    # same two keys written twice -> the later record wins, counted as duplicate
    assert r.n_duplicate == 2


def test_write_atomic_never_leaves_a_partial_file(d):
    p = d / "sub" / "LATEST"
    write_atomic(p, "models/surrogate/20260914")
    assert p.read_text() == "models/surrogate/20260914"
    write_atomic(p, "models/surrogate/20260915")
    assert p.read_text() == "models/surrogate/20260915"
    assert not list(p.parent.glob("*.tmp")), "temp file left behind"


def test_the_robust_search_corners_have_one_definition():
    """This list was copy-pasted into design.py, baseline.py and
    seed_benchmark.py. A reward measured over a different corner set is not
    comparable to the one report.json quotes, and the same fact in three files
    is precisely how that drifts."""
    import inspect

    from eqrl import design
    cs = pdk.robust_search_corners()
    assert [c.name for c in cs] == ["tt_1.800V_27C", "ss_1.710V_125C",
                                    "ff_1.890V_0C", "tt_ll_1.800V_27C"]
    # the R/C axis must be present: sky130's named process corners move
    # MOSFETs only, and R/C moves f_peak more than process does
    assert any(c.rc == "ll" for c in cs)

    for mod in (design,):
        src = inspect.getsource(mod)
        assert "robust_search_corners()" in src, (
            f"{mod.__name__} no longer uses the shared definition")

    for script in ("baseline.py", "seed_benchmark.py"):
        src = (Path(__file__).resolve().parents[1] / "scripts" / script).read_text()
        assert "robust_search_corners()" in src, (
            f"scripts/{script} no longer uses the shared definition")


def test_objective_can_write_every_evaluation_to_a_log():
    """The hook that stops search results from dying with the process."""
    import inspect

    from eqrl.env import Objective
    src = inspect.getsource(Objective.__init__)
    assert "log" in inspect.signature(Objective.__init__).parameters
    assert "self.log = log" in src
    assert "self.log.append(" in inspect.getsource(Objective.evaluate_batch)
