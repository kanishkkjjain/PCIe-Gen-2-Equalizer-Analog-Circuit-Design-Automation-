"""A durable, append-only record of every SPICE evaluation.

The motivation is measured, not hypothetical: roughly 1 650 labelled
(candidate, corner) evaluations from earlier runs of this project were thrown
away, because ``Objective._cache`` is an in-memory dict that dies with the
process.  At ~4.4 s each that is about two hours of simulation discarded.  A
surrogate model needs exactly those labels, so from here on nothing is
discarded.

Format is JSONL -- one self-contained JSON object per line -- chosen over a
database or a pickle for one reason: it is the only format where a process
killed mid-write damages at most the final line, and the reader can simply drop
that line and keep every record before it.

Durability rules, all of which exist because the alternative loses work:

* Only the PARENT process ever opens the file for writing.  Worker processes
  return their results and the parent appends them; concurrent appends from 16
  workers would interleave partial lines.
* One ``write()`` of a fully-built line, then ``flush()`` then ``os.fsync()``,
  per record.  At 4.4 s per simulation the fsync is free, and it is what makes
  "the machine lost power" cost one record instead of one buffer.
* Records carry the ``MeasCond`` they were measured under.  Comparing or
  training on records taken at different measurement conditions is the bug
  class that already bit this project once (``tx_swing_pp_v`` recorded in the
  report at a value the simulation never used), so it is checked at read time
  rather than trusted.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "results" / "evals"

#: rounding used to form a candidate key -- must match ``Objective._key`` or a
#: design simulated by the search will not be recognised as already done here.
KEY_ROUND = 5


def key_of(u) -> tuple:
    """Canonical candidate key. Identical to ``env.Objective._key``."""
    return tuple(round(float(x), KEY_ROUND) for x in u)


def git_sha() -> str:
    """Short SHA of the working tree, or "" outside a repo.

    Recorded on every artifact so a record can always be traced to the code
    that produced it -- a mismatch is then detected rather than discovered.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def meas_dict(meas) -> dict:
    """MeasCond -> plain dict, tolerating either a dataclass or a dict."""
    if meas is None:
        return {}
    if isinstance(meas, dict):
        return dict(meas)
    return dataclasses.asdict(meas)


def _meas_agrees(a: dict, b: dict, tol: float = 1e-12) -> bool:
    """Do two MeasCond dicts describe the same measurement conditions?"""
    if not a or not b:
        return True            # nothing to compare against; caller decides
    for k in set(a) | set(b):
        x, y = a.get(k), b.get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if abs(float(x) - float(y)) > tol * max(1.0, abs(float(x))):
                return False
        elif x != y:
            return False
    return True


class EvalLog:
    """Append-only JSONL writer for (candidate, corner) evaluations.

    Usage is deliberately blunt::

        with EvalLog("collect") as log:
            log.append(key, corner, metrics, meas, params=..., launches=...)

    ``append`` returns only after the record is on the platter.
    """

    def __init__(self, tag: str, directory: Path | None = None,
                 extra: dict | None = None):
        self.tag = tag
        self.dir = Path(directory) if directory else EVAL_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{tag}.jsonl"
        self.extra = dict(extra or {})
        self._sha = git_sha()
        self._fh = None
        self.n_written = 0

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> "EvalLog":
        if self._fh is None:
            self._heal_torn_tail()
            # line-buffered is not enough; we fsync explicitly per record
            self._fh = open(self.path, "a", encoding="utf-8")
        return self

    def _heal_torn_tail(self) -> None:
        """Terminate a partial final line before appending after it.

        A process killed between ``write`` and ``fsync`` leaves a final line
        with no newline.  Appending straight after it CONCATENATES the next
        record onto the fragment, producing one unparseable line -- so the
        resumed run would silently lose its first result as well as the
        interrupted one.  Measured: after a simulated kill, a 4-record file
        plus one append loaded back as 4 records, not 5.

        Writing the missing newline turns the fragment into its own line, which
        the reader then skips on its own, and the append lands intact.
        """
        try:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return
            with open(self.path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) == b"\n":
                    return
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> "EvalLog":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -----------------------------------------------------------
    def append(self, key, corner, metrics, meas=None, params: dict | None = None,
               launches: int = 0, note: str = "") -> dict:
        """Append one evaluation. Durable by the time this returns."""
        m = metrics.as_dict() if hasattr(metrics, "as_dict") else dict(metrics)
        rec = {
            "key": [float(x) for x in key],
            "corner": corner.name if hasattr(corner, "name") else str(corner),
            "corner_fields": {
                "process": getattr(corner, "process", None),
                "vdd": getattr(corner, "vdd", None),
                "temp": getattr(corner, "temp", None),
                "rc": getattr(corner, "rc", None),
            },
            "params": params or {},
            "meas": meas_dict(meas),
            "metrics": m,
            "launches": int(launches),
            "tag": self.tag,
            "git_sha": self._sha,
            "t": time.time(),
        }
        if note:
            rec["note"] = note
        if self.extra:
            rec["run"] = self.extra
        self.write_record(rec)
        return rec

    def write_record(self, rec: dict) -> None:
        """Append a pre-built record. One write, one flush, one fsync.

        NaN is written as the bare ``NaN`` token (json's default) and read back
        by ``json.loads`` as ``float('nan')`` -- which is what we want, since
        Metrics is full of NaN and NaN must stay distinguishable from None.
        ``allow_nan=False`` is right for report.json and wrong here.
        """
        if self._fh is None:
            self.open()
        line = json.dumps(rec, default=str) + "\n"
        self._fh.write(line)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self.n_written += 1


@dataclasses.dataclass
class LoadResult:
    """What ``load`` found, including what it had to throw away."""
    records: dict                 # (key_tuple, corner_name) -> record
    n_lines: int = 0
    n_skipped_bad: int = 0        # unparseable (a killed-mid-write tail)
    n_skipped_meas: int = 0       # measured under different conditions
    n_duplicate: int = 0          # same pair seen twice; the LAST one wins
    files: tuple = ()

    def __len__(self) -> int:
        return len(self.records)

    def summary(self) -> str:
        return (f"{len(self.records)} records from {len(self.files)} file(s), "
                f"{self.n_lines} lines, {self.n_skipped_bad} unparseable, "
                f"{self.n_skipped_meas} meas-mismatch, "
                f"{self.n_duplicate} duplicate")


def load(tags=None, directory: Path | None = None, meas=None,
         allow_meas_mismatch: bool = False, want_ok_only: bool = False
         ) -> LoadResult:
    """Read evaluation records back.

    A line that fails to parse is skipped and counted, never raised: the only
    way to produce one is to be killed between ``write`` and ``fsync``, so it
    can only ever be the final line of a file, and losing it costs one
    simulation.  Raising instead would cost the entire run.
    """
    d = Path(directory) if directory else EVAL_DIR
    if not d.exists():
        return LoadResult(records={})
    if tags is None:
        files = sorted(d.glob("*.jsonl"))
    else:
        if isinstance(tags, (str, Path)):
            tags = [tags]
        files = [d / f"{t}.jsonl" for t in tags]

    want = meas_dict(meas)
    out = LoadResult(records={}, files=tuple(f.name for f in files))
    for f in files:
        if not f.exists():
            continue
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.n_lines += 1
                try:
                    rec = json.loads(line)
                    pair = (tuple(rec["key"]), rec["corner"])
                except Exception:
                    out.n_skipped_bad += 1
                    continue
                if not allow_meas_mismatch and want:
                    if not _meas_agrees(rec.get("meas", {}), want):
                        out.n_skipped_meas += 1
                        continue
                if want_ok_only and not rec.get("metrics", {}).get("ok"):
                    continue
                if pair in out.records:
                    out.n_duplicate += 1
                out.records[pair] = rec
    return out


def write_atomic(path: Path, text: str) -> None:
    """Write a file so a crash mid-write cannot leave it half-written.

    Used for the LATEST pointers. A plain text file replaced by ``os.replace``
    is preferred over a symlink: rename is atomic on the same filesystem, so a
    reader either sees the whole old content or the whole new content, never a
    dangling link into a half-saved model directory.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
