"""Particiones de un `.ubv`: a partir de `ubv-info --json` (o de su copia en `meta/ubvinfo/`).

Un `.ubv` de ~1 GiB contiene N particiones (sesiones de grabación continuas). En cámaras que
graban por detección, cada partición es una grabación de movimiento. `remux` produce un MP4 por
partición, así que la partición es la unidad natural de la línea de tiempo y de la reproducción.

⚠️ El campo `index` de la cabecera de partición NO es único dentro de un fichero (se ha visto
`0, 0, 1, 2, …`). Coldreel identifica cada partición por su **posición** (`ordinal`) en el
fichero, que sí es estable y coincide con el orden en que `remux` las emite.
"""

from __future__ import annotations

import json
import lzma
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

# Pistas observadas (wiki del formato + inventario del NVR).
VIDEO_TRACKS = {7: "h264", 1003: "hevc", 1004: "av1"}
AUDIO_TRACKS = {1000: "aac"}
SMART_EVENT_TRACK = 10


def codec_name(track_id: int | None) -> str | None:
    if track_id is None:
        return None
    return VIDEO_TRACKS.get(track_id, f"track{track_id}")


@dataclass(frozen=True)
class Partition:
    ordinal: int
    t_first_ms: int | None
    t_last_ms: int | None
    byte_offset: int | None
    bytes_used: int | None
    video_track: int | None
    frames_video: int
    frames_audio: int
    keyframes: int
    smart_events: int
    sequence_gaps: int

    @property
    def duration_ms(self) -> int | None:
        if self.t_first_ms is None or self.t_last_ms is None:
            return None
        return max(0, self.t_last_ms - self.t_first_ms)

    @property
    def codec(self) -> str | None:
        return codec_name(self.video_track)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        d["codec"] = self.codec
        return d


def _wc_ms(frame: dict) -> int | None:
    """Reloj de pared de un frame en ms: `wc` viene en unidades de `clock_rate`."""
    rate = frame.get("clock_rate") or 0
    wc = frame.get("wc")
    if not rate or wc is None:
        return None
    return (wc * 1000) // rate


def summarize(info: dict) -> list[Partition]:
    """Resumen por partición de la salida completa de `ubv-info --json`.

    Tiempos: se usan los frames de vídeo (min/max de su reloj de pared), que cubren la partición
    entera; los `ClockSync` son periódicos y el último puede quedar antes del final. Si no hay
    frames con reloj, se cae a los `ClockSync`.
    """
    out: list[Partition] = []
    for ordinal, p in enumerate(info.get("partitions", [])):
        entries = p.get("entries", [])
        header = p.get("header") or {}
        sync = [e["ClockSync"]["wc_ms"] for e in entries if "ClockSync" in e]
        first = last = None
        fv = fa = kf = gaps = 0
        end = 0
        video_track = None
        last_seq: dict[int, int] = {}
        for e in entries:
            f = e.get("Frame")
            if not f:
                continue
            t = f["track_id"]
            if f.get("type_char") == "V":
                fv += 1
                if f.get("keyframe"):
                    kf += 1
                if t in VIDEO_TRACKS:
                    video_track = t
                elif video_track is None:
                    video_track = t
                ms = _wc_ms(f)
                if ms is not None:
                    first = ms if first is None else min(first, ms)
                    last = ms if last is None else max(last, ms)
            elif f.get("type_char") == "A":
                fa += 1
            seq = f.get("sequence")
            if seq is not None:
                prev = last_seq.get(t)
                if prev is not None and seq != (prev + 1) % 65536:
                    gaps += 1
                last_seq[t] = seq
            end = max(end, int(f.get("data_offset", 0)) + int(f.get("data_size", 0)))
        if first is None and sync:
            first, last = min(sync), max(sync)
        offset = header.get("file_offset")
        # `end` es un offset absoluto en el fichero; el tamaño de la partición es relativo a su inicio.
        used = (end - offset) if (end and offset is not None and end >= offset) else (end or None)
        out.append(
            Partition(
                ordinal=ordinal,
                t_first_ms=first,
                t_last_ms=last,
                byte_offset=offset,
                bytes_used=used,
                video_track=video_track,
                frames_video=fv,
                frames_audio=fa,
                keyframes=kf,
                smart_events=sum(1 for e in entries if "SmartEvent" in e),
                sequence_gaps=gaps,
            )
        )
    return out


def from_ledger_summary(parts: list[dict]) -> list[Partition]:
    """Particiones a partir del resumen que `nvr-backup inspect` deja en el registro.

    Ese resumen (`index`, `t_first_ms`, `t_last_ms`, `tracks`, `sequence_gaps`, `bytes_used`,
    `smart_events`) usa los `ClockSync` para los tiempos y no trae offsets; sirve para no releer
    1 GiB cuando el respaldo ya inspeccionó el fichero.
    """
    out = []
    for ordinal, p in enumerate(parts):
        tracks = {int(k): int(v) for k, v in (p.get("tracks") or {}).items()}
        video = [t for t in tracks if t in VIDEO_TRACKS] or [t for t in tracks if t not in AUDIO_TRACKS and t != SMART_EVENT_TRACK]
        vt = video[0] if video else None
        out.append(
            Partition(
                ordinal=ordinal,
                t_first_ms=p.get("t_first_ms"),
                t_last_ms=p.get("t_last_ms"),
                byte_offset=None,
                bytes_used=p.get("bytes_used"),
                video_track=vt,
                frames_video=sum(v for t, v in tracks.items() if t == vt),
                frames_audio=sum(v for t, v in tracks.items() if t in AUDIO_TRACKS),
                keyframes=0,
                smart_events=int(p.get("smart_events") or 0),
                sequence_gaps=int(p.get("sequence_gaps") or 0),
            )
        )
    return out


def low_priority_prefix(nice: int, ionice_idle: bool) -> list[str]:
    cmd = ["nice", "-n", str(nice)]
    if ionice_idle:
        cmd += ["ionice", "-c", "3"]
    return cmd


def cached_info_path(ubvinfo_dir: Path, rel: str) -> Path:
    return ubvinfo_dir / (rel + ".json.xz")


def load_cached_info(ubvinfo_dir: Path, rel: str) -> dict | None:
    p = cached_info_path(ubvinfo_dir, rel)
    if not p.is_file():
        return None
    with lzma.open(p, "rb") as fh:
        return json.load(fh)


def run_ubv_info(ubvinfo_bin: Path, ubv_path: Path, nice: int = 19, ionice_idle: bool = True, timeout: int = 600) -> dict:
    cmd = low_priority_prefix(nice, ionice_idle) + [str(ubvinfo_bin), "--json", str(ubv_path)]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ubv-info falló ({p.returncode}) en {ubv_path}: {p.stderr.decode(errors='replace')[-400:]}")
    return json.loads(p.stdout)


def inspect_file(ubvinfo_bin: Path, ubvinfo_dir: Path, rel: str, ubv_path: Path, nice: int = 19, ionice_idle: bool = True) -> tuple[list[Partition], str]:
    """Particiones de un fichero: primero la copia en `meta/ubvinfo/`, si no, `ubv-info` en vivo."""
    info = load_cached_info(ubvinfo_dir, rel)
    if info is not None:
        return summarize(info), "ubvinfo-cache"
    if not os.path.isfile(ubv_path):
        raise FileNotFoundError(ubv_path)
    return summarize(run_ubv_info(ubvinfo_bin, ubv_path, nice, ionice_idle)), "ubv-info"
