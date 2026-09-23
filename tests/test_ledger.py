import json

import pytest

from coldreel.ledger import Cursor, LedgerError, canonical, file_record_fields, iter_records, record_hash
from conftest import GENESIS, make_record


def test_iterates_and_verifies_chain(archive):
    recs = list(iter_records(archive["root"] / "meta" / "ledger", Cursor()))
    assert len(recs) == len(archive["records"])
    assert [r["record_sha256"] for r, _ in recs] == [r["record_sha256"] for r in archive["records"]]
    last_cursor = recs[-1][1]
    assert last_cursor.file == "ledger-2026-09-22.jsonl"
    assert last_cursor.last_hash == archive["records"][-1]["record_sha256"]


def test_incremental_and_partial_line(archive):
    ledger_dir = archive["root"] / "meta" / "ledger"
    recs = list(iter_records(ledger_dir, Cursor()))
    cur = recs[-1][1]
    assert list(iter_records(ledger_dir, cur)) == []
    # Nuevo registro a medias (sin salto de línea) → no se consume; completo → sí.
    new = make_record(cur.last_hash, event_type="run.end", event_outcome="success", ts="2026-09-23T00:00:00.000Z")
    path = ledger_dir / "ledger-2026-09-23.jsonl"
    path.write_bytes(canonical(new)[:-10])
    assert list(iter_records(ledger_dir, cur)) == []
    path.write_bytes(canonical(new) + b"\n")
    got = list(iter_records(ledger_dir, cur))
    assert len(got) == 1 and got[0][0]["event_type"] == "run.end"
    assert got[0][1].file == "ledger-2026-09-23.jsonl"


def test_broken_chain_detected(archive):
    ledger_dir = archive["root"] / "meta" / "ledger"
    path = ledger_dir / "ledger-2026-09-22.jsonl"
    lines = path.read_bytes().splitlines()
    rec = json.loads(lines[1])
    rec["bytes"] = 1  # manipulado: el hash ya no cuadra
    lines[1] = canonical(rec)
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(LedgerError):
        list(iter_records(ledger_dir, Cursor()))
    assert len(list(iter_records(ledger_dir, Cursor(), verify=False))) == len(lines)


def test_record_hash_matches_nvr_backup_canonicalization(archive):
    r = archive["records"][0]
    assert record_hash(r) == r["record_sha256"]
    assert r["prev_record_sha256"] == GENESIS


def test_file_record_fields(archive):
    rec = archive["records"][1]
    f = file_record_fields(rec)
    assert f["db_id"] == "id-1" and f["channel"] == 0 and f["type"] == "rotating"
    assert f["start_ms"] < f["end_ms"] and f["size"] == 1073741824
    assert file_record_fields(archive["records"][0]) is None
