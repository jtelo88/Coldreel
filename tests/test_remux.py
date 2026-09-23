from pathlib import Path

import pytest

from coldreel import service
from coldreel.index import Index
from coldreel.remux import RemuxCache, UnsupportedCodec, match_partitions, parse_mp4_epoch


def test_parse_mp4_epoch():
    assert parse_mp4_epoch("70A7415F8397_0_rotating_2025-01-28T00.50.02Z.mp4") == 1738025402
    assert parse_mp4_epoch("x.mp4") is None


def test_match_partitions_by_second_with_tolerance():
    parts = [{"ordinal": 0, "t_first_ms": 1738025402529}, {"ordinal": 1, "t_first_ms": 1738027042552}, {"ordinal": 2, "t_first_ms": None}]
    mp4s = [Path("a_2025-01-28T01.17.23Z.mp4"), Path("a_2025-01-28T00.50.02Z.mp4"), Path("other.txt")]
    m = match_partitions(parts, mp4s)
    assert m[0].name.endswith("00.50.02Z.mp4") and m[1].name.endswith("01.17.23Z.mp4") and 2 not in m


def test_ensure_remux_and_gc(settings, archive):
    idx = Index(settings.db_path)
    service.sync(idx, settings)
    f = idx.file(1)
    service.index_file(idx, settings, f)
    f = idx.file(1)
    cache = RemuxCache(settings)
    man = cache.ensure(f, idx.partitions(1))
    assert man["mp4_count"] == 2 and set(man["clips"]) == {"0", "1"} and man["unmatched_mp4"] == []
    assert cache.clip_path(1, 0).is_file() and cache.clip_path(1, 5) is None
    assert cache.ensure(f, idx.partitions(1)) == man  # idempotente
    # GC: con límite 0 borra todo lo que no esté en uso.
    r = cache.gc(0)
    assert r["removed"] == ["1"] and cache.manifest(1) is None


def test_unsupported_av1_track(settings, archive):
    idx = Index(settings.db_path)
    service.sync(idx, settings)
    f = idx.file(2)
    f["video_track"] = 1004
    with pytest.raises(UnsupportedCodec):
        RemuxCache(settings).ensure(f, [])
