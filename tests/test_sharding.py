"""Tests for shard rotation and slicing.

A sharding bug is invisible: the run succeeds, alerts still arrive, and one
slice of companies is simply never scanned. The first implementation derived
the shard from the clock (`hour // 6` with 4 shards). Against this project's
own cron — 04/08/12/16 UTC — that yields 0, 1, 2, 2, so a quarter of the
roster was never checked and nothing anywhere reported a problem.

Run: python -m pytest tests/test_sharding.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.main import select_shard  # noqa: E402
from src.models import Company  # noqa: E402
from src.state import RunMeta  # noqa: E402

ROSTER = [Company(name=f"Company {i}", ats="greenhouse", slug=f"c{i}") for i in range(97)]


def test_shards_partition_the_roster_exactly():
    for shards in (2, 3, 4, 7):
        seen: list[str] = []
        for i in range(shards):
            seen += [c.name for c in select_shard(ROSTER, shards, i)]
        assert sorted(seen) == sorted(c.name for c in ROSTER), f"shards={shards}"
        assert len(seen) == len(set(seen)), "a company appeared in two shards"


def test_shard_membership_is_stable_when_roster_grows():
    """Slicing by list position would reshuffle everyone when a company is
    added, silently changing who gets scanned when."""
    before = {c.name for c in select_shard(ROSTER, 4, 0)}
    grown = ROSTER + [Company(name="Newcomer", ats="lever", slug="new")]
    after = {c.name for c in select_shard(grown, 4, 0)}
    assert before <= after


def test_single_shard_is_a_passthrough():
    assert select_shard(ROSTER, 1, 0) == ROSTER


def test_rotation_visits_every_shard(tmp_path):
    """The clock-derived version failed exactly here."""
    p = tmp_path / "run_meta.json"
    shards, visited = 4, []
    for _ in range(shards):
        meta = RunMeta(p)                 # reload each run, as a real run does
        visited.append(meta.current_shard(shards))
        meta.advance(shards)
        meta.save()
    assert sorted(visited) == list(range(shards)), f"missed shards: {visited}"


def test_rotation_wraps(tmp_path):
    p = tmp_path / "run_meta.json"
    shards = 3
    visited = []
    for _ in range(shards * 2 + 1):
        meta = RunMeta(p)
        visited.append(meta.current_shard(shards))
        meta.advance(shards)
        meta.save()
    assert visited == [0, 1, 2, 0, 1, 2, 0]


def test_rotation_survives_a_corrupt_meta_file(tmp_path):
    p = tmp_path / "run_meta.json"
    p.write_text("{ not json", encoding="utf-8")
    assert RunMeta(p).current_shard(4) == 0


def test_no_rotation_when_sharding_disabled(tmp_path):
    p = tmp_path / "run_meta.json"
    meta = RunMeta(p)
    meta.advance(1)
    meta.save()
    assert RunMeta(p).current_shard(1) == 0
