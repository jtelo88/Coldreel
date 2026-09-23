"""Índice propio de Coldreel (SQLite): ficheros del archivo, particiones y cámaras.

Se alimenta del registro de nvr-backup (incremental, con verificación de la cadena) y de la
inspección de particiones (`ubv-info`). Es derivado y regenerable: borrar `coldreel.db` y
volver a sincronizar reconstruye todo (las particiones se reinspeccionan bajo demanda).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ledger as ledger_mod
from .ubv import Partition, from_ledger_summary

SCHEMA = """
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY,
  db_id TEXT NOT NULL, variant INTEGER NOT NULL DEFAULT 1,
  mac TEXT NOT NULL, camera TEXT NOT NULL, channel INTEGER NOT NULL, type TEXT NOT NULL,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, copied_at TEXT NOT NULL,
  indexed_at TEXT, index_source TEXT, video_track INTEGER, partitions_n INTEGER,
  UNIQUE(db_id, variant));
CREATE INDEX IF NOT EXISTS files_mac_time ON files(mac, channel, type, start_ms, end_ms);
CREATE INDEX IF NOT EXISTS files_rel ON files(rel);
CREATE TABLE IF NOT EXISTS partitions(
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  t_first_ms INTEGER, t_last_ms INTEGER, byte_offset INTEGER, bytes_used INTEGER,
  video_track INTEGER, frames_video INTEGER, frames_audio INTEGER, keyframes INTEGER,
  smart_events INTEGER, sequence_gaps INTEGER,
  PRIMARY KEY(file_id, ordinal));
CREATE INDEX IF NOT EXISTS partitions_time ON partitions(t_first_ms, t_last_ms);
CREATE TABLE IF NOT EXISTS cameras(
  mac TEXT PRIMARY KEY, name TEXT NOT NULL, first_ms INTEGER, last_ms INTEGER,
  files INTEGER, bytes INTEGER, video_track INTEGER);
"""

FILE_COLS = (
    "id", "db_id", "variant", "mac", "camera", "channel", "type", "start_ms", "end_ms", "size",
    "sha256", "rel", "dst", "copied_at", "indexed_at", "index_source", "video_track", "partitions_n",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Index:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # ── estado ───────────────────────────────────────────────────────────────────────────
    def get_state(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set_state(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO state(k, v) VALUES(?, ?)", (k, json.dumps(v)))

    # ── sincronización desde el registro ──────────────────────────────────────────────────
    def sync_from_ledger(self, ledger_dir: Path, verify: bool = True) -> dict:
        """Aplica los registros nuevos del ledger. Devuelve contadores."""
        stats = {"records": 0, "files": 0, "partitions_from_ledger": 0, "last_ts": None}
        with self.lock:
            cursor = ledger_mod.Cursor.from_dict(self.get_state("ledger_cursor"))
            last = cursor
            for rec, cur in ledger_mod.iter_records(ledger_dir, cursor, verify=verify):
                stats["records"] += 1
                stats["last_ts"] = rec.get("ts")
                f = ledger_mod.file_record_fields(rec)
                if f:
                    self._upsert_file(f)
                    stats["files"] += 1
                elif rec.get("event_type") == "inspect" and rec.get("event_outcome") == "success" and rec.get("partitions") is not None:
                    row = self.db.execute("SELECT id FROM files WHERE dst=?", (rec["dst_path"],)).fetchone()
                    if row:
                        self.store_partitions(row["id"], from_ledger_summary(rec["partitions"]), "ledger-inspect", commit=False)
                        stats["partitions_from_ledger"] += 1
                last = cur
                if stats["records"] % 2000 == 0:
                    self.set_state("ledger_cursor", last.to_dict())
                    self.db.commit()
            self.set_state("ledger_cursor", last.to_dict())
            if stats["files"]:
                self.refresh_cameras(commit=False)
            self.db.commit()
        return stats

    def _upsert_file(self, f: dict):
        self.db.execute(
            """INSERT INTO files(db_id, variant, mac, camera, channel, type, start_ms, end_ms, size,
                                 sha256, rel, dst, copied_at)
               VALUES(:db_id, :variant, :mac, :camera, :channel, :type, :start_ms, :end_ms, :size,
                      :sha256, :rel, :dst, :copied_at)
               ON CONFLICT(db_id, variant) DO UPDATE SET
                 mac=excluded.mac, camera=excluded.camera, channel=excluded.channel, type=excluded.type,
                 start_ms=excluded.start_ms, end_ms=excluded.end_ms, size=excluded.size,
                 sha256=excluded.sha256, rel=excluded.rel, dst=excluded.dst, copied_at=excluded.copied_at""",
            f,
        )

    def refresh_cameras(self, commit: bool = True):
        """Agregado por MAC. El nombre es el del fichero más reciente (los nombres cambian; la MAC no)."""
        with self.lock:
            self.db.execute("DELETE FROM cameras")
            self.db.execute(
                """INSERT INTO cameras(mac, name, first_ms, last_ms, files, bytes, video_track)
                   SELECT f.mac,
                          (SELECT camera FROM files x WHERE x.mac=f.mac AND x.type='rotating' AND x.channel=0
                             ORDER BY start_ms DESC LIMIT 1),
                          MIN(start_ms), MAX(end_ms), COUNT(*), SUM(size),
                          (SELECT video_track FROM files x WHERE x.mac=f.mac AND x.video_track IS NOT NULL
                             ORDER BY start_ms DESC LIMIT 1)
                   FROM files f WHERE f.type='rotating' AND f.channel=0 GROUP BY f.mac"""
            )
            if commit:
                self.db.commit()

    # ── particiones ──────────────────────────────────────────────────────────────────────
    def store_partitions(self, file_id: int, parts: list[Partition], source: str, commit: bool = True):
        with self.lock:
            self.db.execute("DELETE FROM partitions WHERE file_id=?", (file_id,))
            self.db.executemany(
                """INSERT INTO partitions(file_id, ordinal, t_first_ms, t_last_ms, byte_offset, bytes_used,
                     video_track, frames_video, frames_audio, keyframes, smart_events, sequence_gaps)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (file_id, p.ordinal, p.t_first_ms, p.t_last_ms, p.byte_offset, p.bytes_used, p.video_track,
                     p.frames_video, p.frames_audio, p.keyframes, p.smart_events, p.sequence_gaps)
                    for p in parts
                ],
            )
            vt = next((p.video_track for p in parts if p.video_track is not None), None)
            self.db.execute(
                "UPDATE files SET indexed_at=?, index_source=?, video_track=?, partitions_n=? WHERE id=?",
                (now_iso(), source, vt, len(parts), file_id),
            )
            if commit:
                self.db.commit()

    def partitions(self, file_id: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM partitions WHERE file_id=? ORDER BY ordinal", (file_id,)).fetchall()
        return [self._partition_dict(r) for r in rows]

    @staticmethod
    def _partition_dict(r: sqlite3.Row) -> dict:
        p = Partition(
            ordinal=r["ordinal"], t_first_ms=r["t_first_ms"], t_last_ms=r["t_last_ms"], byte_offset=r["byte_offset"],
            bytes_used=r["bytes_used"], video_track=r["video_track"], frames_video=r["frames_video"] or 0,
            frames_audio=r["frames_audio"] or 0, keyframes=r["keyframes"] or 0, smart_events=r["smart_events"] or 0,
            sequence_gaps=r["sequence_gaps"] or 0,
        )
        return p.to_dict()

    # ── consultas ────────────────────────────────────────────────────────────────────────
    def file(self, file_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(r) if r else None

    def cameras(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM cameras ORDER BY name, mac").fetchall()
        return [dict(r) for r in rows]

    def files_in_window(self, mac: str, from_ms: int, to_ms: int, channel: int = 0, type_: str = "rotating") -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM files WHERE mac=? AND channel=? AND type=? AND start_ms < ? AND end_ms > ?
                   ORDER BY start_ms""",
                (mac, channel, type_, to_ms, from_ms),
            ).fetchall()
        return [dict(r) for r in rows]

    def timeline(self, mac: str, from_ms: int, to_ms: int, channel: int = 0) -> list[dict]:
        """Ficheros de la ventana con sus particiones (si están indexadas)."""
        out = []
        for f in self.files_in_window(mac, from_ms, to_ms, channel):
            f["indexed"] = f["indexed_at"] is not None
            f["partitions"] = self.partitions(f["id"]) if f["indexed"] else []
            out.append(f)
        return out

    def coverage(self, mac: str, tz: str, channel: int = 0) -> list[dict]:
        """Días (en la zona `tz`) con algún fichero de esa cámara, con conteo y estado de indexado."""
        zone = ZoneInfo(tz)
        days: dict[str, dict] = {}
        with self.lock:
            rows = self.db.execute(
                "SELECT start_ms, end_ms, size, indexed_at FROM files WHERE mac=? AND channel=? AND type='rotating'",
                (mac, channel),
            ).fetchall()
        for r in rows:
            d = datetime.fromtimestamp(r["start_ms"] / 1000, zone).date()
            end = datetime.fromtimestamp(max(r["end_ms"], r["start_ms"]) / 1000, zone).date()
            while d <= end:
                k = d.isoformat()
                e = days.setdefault(k, {"date": k, "files": 0, "indexed": 0, "bytes": 0})
                e["files"] += 1
                e["bytes"] += r["size"]
                if r["indexed_at"]:
                    e["indexed"] += 1
                d += timedelta(days=1)
        return [days[k] for k in sorted(days)]

    def stats(self) -> dict:
        with self.lock:
            f = self.db.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) b, SUM(indexed_at IS NOT NULL) i FROM files").fetchone()
            p = self.db.execute("SELECT COUNT(*) n FROM partitions").fetchone()
            c = self.db.execute("SELECT COUNT(*) n FROM cameras").fetchone()
            cur = self.get_state("ledger_cursor") or {}
        return {
            "files": f["n"], "bytes": f["b"], "files_indexed": f["i"] or 0, "partitions": p["n"], "cameras": c["n"],
            "ledger_file": cur.get("file"), "ledger_offset": cur.get("offset"),
        }
