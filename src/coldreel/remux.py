"""MP4 por partición, bajo demanda y con caché LRU.

`remux` (petergeneric/unifi-protect-remux) convierte un `.ubv` entero en un MP4 por partición
sin recodificar (~3–5 s por GiB con `--fast-start`). No admite elegir una partición, así que
Coldreel remuxea el fichero completo la primera vez y guarda los MP4 en
`cache_dir/<file_id>/`, junto a un `manifest.json` con el mapeo ordinal → MP4.

Mapeo: `remux` nombra cada MP4 `<base>_<AAAA-MM-DDTHH.MM.SSZ>.mp4` con el inicio de la
partición en UTC truncado a segundos. Se empareja cada partición del índice con el MP4 cuyo
segundo coincide (tolerancia de 2 s) y, si el número de MP4 coincide con el de particiones, se
comprueba además el orden.

Límites conocidos: `remux` v4.2.2 falla con el AV1 de firmware nuevo (pista 1004). Esos ficheros
se marcan como no soportados hasta tener un `remux` compilado de `main`.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .ubv import low_priority_prefix

MP4_TS = re.compile(r"_(\d{4}-\d{2}-\d{2}T\d{2})\.(\d{2})\.(\d{2})Z\.mp4$")
AV1_NEW_TRACK = 1004


class RemuxError(Exception):
    pass


class UnsupportedCodec(RemuxError):
    pass


def parse_mp4_epoch(name: str) -> int | None:
    m = MP4_TS.search(name)
    if not m:
        return None
    iso = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def match_partitions(partitions: list[dict], mp4s: list[Path], tolerance_s: int = 2) -> dict[int, Path]:
    """Empareja ordinal → MP4 por segundo de inicio; devuelve solo las emparejadas."""
    stamped = sorted(((parse_mp4_epoch(p.name), p) for p in mp4s if parse_mp4_epoch(p.name) is not None), key=lambda x: x[0])
    used: set[Path] = set()
    out: dict[int, Path] = {}
    for part in partitions:
        t = part.get("t_first_ms")
        if t is None:
            continue
        sec = t // 1000
        best = None
        for ep, path in stamped:
            if path in used:
                continue
            d = abs(ep - sec)
            if d <= tolerance_s and (best is None or d < best[0]):
                best = (d, path)
        if best:
            used.add(best[1])
            out[part["ordinal"]] = best[1]
    return out


class RemuxCache:
    def __init__(self, settings: Settings):
        self.s = settings
        self.root = Path(settings.cache_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._sem = threading.BoundedSemaphore(max(1, settings.remux_jobs))

    def _lock(self, file_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(file_id, threading.Lock())

    def dir_for(self, file_id: int) -> Path:
        return self.root / str(file_id)

    def manifest(self, file_id: int) -> dict | None:
        p = self.dir_for(file_id) / "manifest.json"
        if not p.is_file():
            return None
        with open(p) as fh:
            return json.load(fh)

    def clip_path(self, file_id: int, ordinal: int) -> Path | None:
        m = self.manifest(file_id)
        if not m:
            return None
        name = m["clips"].get(str(ordinal))
        if not name:
            return None
        p = self.dir_for(file_id) / name
        if not p.is_file():
            return None
        try:
            os.utime(self.dir_for(file_id) / "manifest.json")  # LRU: último acceso
        except OSError:
            pass
        return p

    def ensure(self, file_row: dict, partitions: list[dict]) -> dict:
        """Garantiza los MP4 del fichero. Devuelve el manifiesto. Serializa por fichero y limita en global."""
        file_id = int(file_row["id"])
        m = self.manifest(file_id)
        if m and m.get("sha256") == file_row["sha256"]:
            return m
        vt = file_row.get("video_track")
        if vt == AV1_NEW_TRACK:
            raise UnsupportedCodec("AV1 de firmware nuevo (pista 1004): remux v4.2.2 no lo convierte; hace falta remux compilado de main")
        with self._lock(file_id):
            m = self.manifest(file_id)
            if m and m.get("sha256") == file_row["sha256"]:
                return m
            with self._sem:
                return self._remux(file_row, partitions)

    def _remux(self, file_row: dict, partitions: list[dict]) -> dict:
        file_id = int(file_row["id"])
        src = Path(file_row["dst"])
        if not src.is_file():
            raise RemuxError(f"no existe el .ubv: {src}")
        final = self.dir_for(file_id)
        tmp = self.root / f".tmp-{file_id}-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        cmd = low_priority_prefix(self.s.nice, self.s.ionice_idle) + [
            str(self.s.remux_bin), "--fast-start=true", f"--output-folder={tmp}", str(src),
        ]
        t0 = time.monotonic()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        elapsed = time.monotonic() - t0
        mp4s = sorted(tmp.glob("*.mp4"))
        if p.returncode != 0 and not mp4s:
            shutil.rmtree(tmp, ignore_errors=True)
            err = p.stderr[-600:]
            if "NAL unit extends beyond frame boundary" in err or "track=1004" in err:
                raise UnsupportedCodec("AV1 de firmware nuevo (pista 1004): remux v4.2.2 no lo convierte")
            raise RemuxError(f"remux falló ({p.returncode}): {err}")
        clips = match_partitions(partitions, mp4s)
        manifest = {
            "file_id": file_id,
            "sha256": file_row["sha256"],
            "src": str(src),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "remux_seconds": round(elapsed, 2),
            "remux_returncode": p.returncode,
            "mp4_count": len(mp4s),
            "partition_count": len(partitions),
            "clips": {str(o): path.name for o, path in clips.items()},
            "unmatched_mp4": [q.name for q in mp4s if q not in clips.values()],
            "stderr_tail": p.stderr[-2000:] if p.returncode != 0 else "",
        }
        with open(tmp / "manifest.json", "w") as fh:
            json.dump(manifest, fh, indent=1)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        self.gc()
        return manifest

    # ── caché LRU ────────────────────────────────────────────────────────────────────────
    def usage(self) -> tuple[int, list[tuple[float, Path, int]]]:
        entries = []
        total = 0
        for d in self.root.iterdir():
            if not d.is_dir() or d.name.startswith(".tmp-"):
                continue
            size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
            mf = d / "manifest.json"
            atime = mf.stat().st_mtime if mf.is_file() else d.stat().st_mtime
            entries.append((atime, d, size))
            total += size
        return total, sorted(entries)

    def gc(self, max_bytes: int | None = None) -> dict:
        """Borra los ficheros menos usados hasta caber en `cache_max_bytes`. Nunca el que se está sirviendo ahora mismo (candado)."""
        limit = self.s.cache_max_bytes if max_bytes is None else max_bytes
        with open(self.root / ".gc.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            total, entries = self.usage()
            freed = 0
            removed = []
            for _, d, size in entries:
                if total - freed <= limit:
                    break
                lock = self._lock(int(d.name)) if d.name.isdigit() else None
                if lock and not lock.acquire(blocking=False):
                    continue
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    freed += size
                    removed.append(d.name)
                finally:
                    if lock:
                        lock.release()
            return {"bytes_before": total, "bytes_after": total - freed, "removed": removed, "limit": limit}
