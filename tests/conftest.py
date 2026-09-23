"""Fixtures: un archivo falso (registro encadenado + herramientas simuladas) para probar sin el NVR."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from coldreel.config import Settings
from coldreel.ledger import GENESIS, canonical

MAC_A, MAC_B = "AABBCCDDEE01", "AABBCCDDEE02"
T0 = 1738025402529  # 2025-01-28T00:50:02.529Z


def make_record(prev: str, **fields) -> dict:
    rec = {"schema": "nvr-ledger/1", "record_id": f"{fields.get('ts', 'x')}-{len(prev) % 7}", **fields, "prev_record_sha256": prev}
    rec["record_sha256"] = hashlib.sha256(canonical(rec)).hexdigest()
    return rec


def copy_record(prev, db_id, mac, camera, rel, start, end, dst, ts="2026-09-22T10:00:00.000Z", size=1073741824, channel=0, type_="rotating"):
    return make_record(
        prev, event_type="ingest.copy", event_outcome="success", ts=ts, run_id="r1", tool="nvr-backup 1.0.0",
        src={"db_id": db_id, "file": rel.split("/")[-1], "rel": rel, "mac": mac, "camera": camera, "channel": channel,
             "type": type_, "start": start, "end": end},
        bytes=size, hash_src=hashlib.sha256(db_id.encode()).hexdigest(), hash_dst_stream=hashlib.sha256(db_id.encode()).hexdigest(),
        dst_path=dst, src_stat_post={"mtime": 1}, variant=1,
    )


@pytest.fixture
def archive(tmp_path: Path) -> dict:
    root = tmp_path / "archive"
    (root / "meta" / "ledger").mkdir(parents=True)
    (root / "meta" / "ubvinfo").mkdir(parents=True)
    ubv_dir = root / "ubv" / "2025" / "01" / "28"
    ubv_dir.mkdir(parents=True)
    files = [
        ("id-1", MAC_A, "Cocina", f"2025/01/28/{MAC_A}_0_rotating_{T0}.ubv", T0, T0 + 3_600_000),
        ("id-2", MAC_A, "Cocina", f"2025/01/28/{MAC_A}_0_rotating_{T0 + 3_600_000}.ubv", T0 + 3_600_000, T0 + 7_200_000),
        ("id-3", MAC_B, "Garaje", f"2025/01/28/{MAC_B}_0_rotating_{T0}.ubv", T0, T0 + 1_000_000),
    ]
    prev = GENESIS
    lines = []
    rec = make_record(prev, event_type="run.start", event_outcome="success", ts="2026-09-22T09:59:00.000Z", run_id="r1", tool="nvr-backup 1.0.0")
    lines.append(rec); prev = rec["record_sha256"]
    for db_id, mac, cam, rel, start, end in files:
        dst = str(root / "ubv" / rel)
        Path(dst).write_bytes(b"UBV" + db_id.encode())
        rec = copy_record(prev, db_id, mac, cam, rel, start, end, dst)
        lines.append(rec); prev = rec["record_sha256"]
    # Un `inspect` del registro para el fichero 3 (resumen de particiones tal como lo deja nvr-backup).
    rec = make_record(prev, event_type="inspect", event_outcome="success", ts="2026-09-22T10:05:00.000Z", tool="nvr-backup 1.0.0",
                      dst_path=str(root / "ubv" / files[2][3]), ubvinfo_path="x", json_bytes=10,
                      partitions=[{"index": 0, "t_first_ms": T0, "t_last_ms": T0 + 60_000, "tracks": {"7": 900, "1000": 400}, "sequence_gaps": 0, "bytes_used": 5000, "smart_events": 1},
                                  {"index": 0, "t_first_ms": T0 + 500_000, "t_last_ms": T0 + 620_000, "tracks": {"7": 1800, "1000": 800}, "sequence_gaps": 1, "bytes_used": 9000, "smart_events": 0}])
    lines.append(rec); prev = rec["record_sha256"]
    with open(root / "meta" / "ledger" / "ledger-2026-09-22.jsonl", "wb") as fh:
        for r in lines:
            fh.write(canonical(r) + b"\n")

    # Herramientas simuladas: ubv-info devuelve 2 particiones (tiempos según el nombre del fichero);
    # remux crea un MP4 por partición con el nombre real de remux.
    tools = tmp_path / "tools"; tools.mkdir()
    ubvinfo = tools / "ubv-info"
    ubvinfo.write_text(f"""#!{sys.executable}
import json, sys, re
path = sys.argv[-1]
start = int(re.search(r'_(\\d+)\\.ubv$', path).group(1))
def part(t0, n, off):
    fr = [{{"Frame": {{"type_char": "V", "track_id": 7, "data_offset": off + i * 100, "data_size": 100, "dts": 0,
             "clock_rate": 90000, "sequence": i, "keyframe": i % 30 == 0, "cts": 0, "wc": (t0 + i * 100) * 90, "packet_position": "Single"}}}} for i in range(n)]
    fr.append({{"Frame": {{"type_char": "A", "track_id": 1000, "data_offset": off, "data_size": 10, "dts": 0, "clock_rate": 16000, "sequence": 0, "keyframe": True, "cts": 0, "wc": t0 * 16, "packet_position": "Single"}}}})
    return {{"index": 0, "header": {{"file_offset": off}}, "entries": [{{"ClockSync": {{"sc_dts": 0, "sc_rate": 1000, "wc_ms": t0, "wc_seconds": t0 // 1000, "wc_nanoseconds": 0}}}}] + fr}}
print(json.dumps({{"partitions": [part(start, 300, 0), part(start + 1_800_000, 600, 40000)]}}))
""")
    ubvinfo.chmod(ubvinfo.stat().st_mode | stat.S_IEXEC)
    remux = tools / "remux"
    remux.write_text(f"""#!{sys.executable}
import sys, re, os, datetime, json
args = sys.argv[1:]
out = [a.split('=', 1)[1] for a in args if a.startswith('--output-folder=')][0]
src = args[-1]
base = os.path.basename(src)[:-4]
start = int(re.search(r'_(\\d+)$', base).group(1))
if 'FAIL' in src:
    sys.stderr.write('Error remuxing partition #1: NAL unit extends beyond frame boundary (track=1004)\\n'); sys.exit(1)
for t0 in (start, start + 1_800_000):
    iso = datetime.datetime.fromtimestamp(t0 // 1000, datetime.timezone.utc).strftime('%Y-%m-%dT%H.%M.%SZ')
    open(os.path.join(out, f'{{base.rsplit("_", 1)[0]}}_{{iso}}.mp4'), 'wb').write(b'\\x00\\x00\\x00\\x18ftypisom' + str(t0).encode())
""")
    remux.chmod(remux.stat().st_mode | stat.S_IEXEC)
    return {"root": root, "tools": tools, "files": files, "records": lines}


@pytest.fixture
def settings(archive, tmp_path) -> Settings:
    return Settings(
        archive_root=archive["root"], data_dir=tmp_path / "var", cache_dir=tmp_path / "var" / "cache",
        cache_max_bytes=10**9, remux_bin=archive["tools"] / "remux", ubvinfo_bin=archive["tools"] / "ubv-info",
        ionice_idle=False, nice=0, sync_interval_s=0,
    )
