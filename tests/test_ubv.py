import json
import subprocess
from pathlib import Path

from coldreel.ubv import from_ledger_summary, inspect_file, summarize
from conftest import MAC_A, T0


def test_summarize_uses_frame_wall_clock_and_ordinal(archive):
    out = subprocess.run([str(archive["tools"] / "ubv-info"), "--json", f"/x/{MAC_A}_0_rotating_{T0}.ubv"], capture_output=True, check=True)
    parts = summarize(json.loads(out.stdout))
    assert [p.ordinal for p in parts] == [0, 1]  # aunque ambas cabeceras digan index=0
    assert parts[0].t_first_ms == T0 and parts[0].t_last_ms == T0 + 299 * 100
    assert parts[0].video_track == 7 and parts[0].codec == "h264"
    assert parts[0].frames_video == 300 and parts[0].frames_audio == 1 and parts[0].keyframes == 10
    assert parts[1].byte_offset == 40000 and parts[1].duration_ms == 599 * 100
    assert parts[1].bytes_used == 599 * 100 + 100  # relativo al inicio de la partición, no absoluto
    assert parts[0].sequence_gaps == 0


def test_from_ledger_summary():
    parts = from_ledger_summary([{"index": 0, "t_first_ms": 10, "t_last_ms": 20, "tracks": {"1004": 5, "1000": 2}, "bytes_used": 1, "smart_events": 3}])
    assert parts[0].video_track == 1004 and parts[0].codec == "av1" and parts[0].frames_audio == 2 and parts[0].smart_events == 3


def test_inspect_prefers_cached_ubvinfo(archive, settings):
    import lzma
    rel = archive["files"][0][3]
    cached = settings.ubvinfo_dir / (rel + ".json.xz")
    cached.parent.mkdir(parents=True)
    with lzma.open(cached, "wb") as fh:
        fh.write(json.dumps({"partitions": [{"index": 0, "header": {"file_offset": 0}, "entries": [{"ClockSync": {"sc_dts": 0, "sc_rate": 1000, "wc_ms": 5, "wc_seconds": 0, "wc_nanoseconds": 0}}]}]}).encode())
    parts, source = inspect_file(settings.ubvinfo_bin, settings.ubvinfo_dir, rel, Path("/no/existe.ubv"), 0, False)
    assert source == "ubvinfo-cache" and len(parts) == 1 and parts[0].t_first_ms == 5
    parts, source = inspect_file(settings.ubvinfo_bin, settings.ubvinfo_dir, archive["files"][1][3], Path(archive["files"][1][3]) if False else archive["root"] / "ubv" / archive["files"][1][3], 0, False)
    assert source == "ubv-info" and len(parts) == 2
