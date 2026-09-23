import base64

import pytest
from fastapi.testclient import TestClient

from coldreel.api import create_app
from coldreel.config import Settings, load_settings
from conftest import MAC_A, MAC_B, T0


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_health_and_cameras(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["index"]["files"] == 3 and h["index"]["cameras"] == 2
    cams = client.get("/api/cameras").json()
    assert {c["mac"] for c in cams} == {MAC_A, MAC_B}
    a = next(c for c in cams if c["mac"] == MAC_A)
    assert a["name"] == "Cocina" and a["files"] == 2 and a["first_ms"] == T0


def test_coverage_and_timeline_and_index(client):
    cov = client.get(f"/api/coverage?mac={MAC_A}&tz=UTC").json()
    assert cov == [{"date": "2025-01-28", "files": 2, "indexed": 0, "bytes": 2 * 1073741824}]
    assert client.get(f"/api/coverage?mac={MAC_A}&tz=Nope/Nowhere").status_code == 400
    tl = client.get(f"/api/timeline?mac={MAC_A}&from_ms={T0 - 1000}&to_ms={T0 + 86400000}").json()
    assert len(tl["files"]) == 2 and not tl["files"][0]["indexed"]
    # El fichero 3 (otra cámara) vino ya indexado por el `inspect` del registro.
    tl_b = client.get(f"/api/timeline?mac={MAC_B}&from_ms={T0 - 1000}&to_ms={T0 + 86400000}").json()
    assert tl_b["files"][0]["indexed"] and tl_b["files"][0]["index_source"] == "ledger-inspect"
    assert [p["ordinal"] for p in tl_b["files"][0]["partitions"]] == [0, 1]
    r = client.post(f"/api/index?mac={MAC_A}&from_ms={T0 - 1000}&to_ms={T0 + 86400000}").json()
    assert r["indexed"] == 2 and r["partitions"] == 4 and r["errors"] == []
    tl = client.get(f"/api/timeline?mac={MAC_A}&from_ms={T0 - 1000}&to_ms={T0 + 86400000}").json()
    p = tl["files"][0]["partitions"][0]
    assert p["clip_url"] == "/clip/1/0.mp4" and p["cached"] is False and p["codec"] == "h264"
    assert client.get(f"/api/timeline?mac={MAC_A}&from_ms=5&to_ms=1").status_code == 400


def test_clip_remux_on_demand_and_range(client):
    r = client.get("/clip/1/1.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content.startswith(b"\x00\x00\x00\x18ftyp")
    r = client.get("/clip/1/1.mp4", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"\x00\x00\x00\x18"
    h = client.head("/clip/1/0.mp4")
    assert h.status_code == 200 and h.content == b"" and h.headers["content-type"] == "video/mp4"
    assert client.get("/clip/1/9.mp4").status_code == 404
    assert client.get("/clip/99/0.mp4").status_code == 404
    d = client.get("/api/files/1").json()
    assert d["manifest"]["mp4_count"] == 2 and d["partitions_n"] == 2


def test_clip_unsupported_codec_reports_501(client, settings, archive):
    # Simula un fichero AV1 nuevo: remux falla con el mensaje real → 501 con explicación.
    (archive["root"] / "ubv" / "2025/01/28" / f"{MAC_B}_0_rotating_FAIL_{T0}.ubv").write_bytes(b"x")
    idx = client.app.state.index
    idx.db.execute("UPDATE files SET dst=? WHERE id=3", (str(archive["root"] / "ubv" / "2025/01/28" / f"{MAC_B}_0_rotating_FAIL_{T0}.ubv"),))
    idx.db.commit()
    r = client.get("/clip/3/0.mp4")
    assert r.status_code == 501 and "AV1" in r.json()["detail"]


def test_basic_auth(settings):
    s = Settings(**{**settings.__dict__, "auth": "ana:secreto"})
    with TestClient(create_app(s)) as c:
        assert c.get("/api/health").status_code == 401
        ok = c.get("/api/health", headers={"Authorization": "Basic " + base64.b64encode(b"ana:secreto").decode()})
        assert ok.status_code == 200


def test_settings_precedence(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('port = 9000\nhost = "0.0.0.0"\ncache_max_bytes = 5\n')
    s = load_settings(cfg, env={"COLDREEL_PORT": "9100"})
    assert s.port == 9100 and s.host == "0.0.0.0" and s.cache_max_bytes == 5
    cfg.write_text("desconocida = 1\n")
    with pytest.raises(ValueError):
        load_settings(cfg, env={})
