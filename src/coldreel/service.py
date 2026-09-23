"""Operaciones compartidas por la API y la CLI: sincronizar, indexar particiones, remuxear."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Settings
from .index import Index
from .ubv import inspect_file

log = logging.getLogger("coldreel")


def sync(index: Index, settings: Settings, verify: bool = True) -> dict:
    stats = index.sync_from_ledger(settings.ledger_dir, verify=verify)
    log.info("sync: %s registros, %s ficheros nuevos, %s particiones del registro", stats["records"], stats["files"], stats["partitions_from_ledger"])
    return stats


def index_file(index: Index, settings: Settings, file_row: dict) -> dict:
    """Inspecciona un fichero y guarda sus particiones. Idempotente."""
    parts, source = inspect_file(
        settings.ubvinfo_bin, settings.ubvinfo_dir, file_row["rel"], Path(file_row["dst"]),
        settings.nice, settings.ionice_idle,
    )
    index.store_partitions(int(file_row["id"]), parts, source)
    return {"file_id": file_row["id"], "partitions": len(parts), "source": source}


def index_files(index: Index, settings: Settings, rows: list[dict], jobs: int | None = None) -> dict:
    """Indexa varios ficheros en paralelo (por defecto `settings.index_jobs`)."""
    jobs = max(1, jobs or settings.index_jobs)
    done, errors = [], []

    def one(row):
        try:
            return index_file(index, settings, row), None
        except Exception as e:  # noqa: BLE001 — se reporta por fichero
            log.warning("index: fallo en %s: %s", row.get("rel"), e)
            return None, {"file_id": row.get("id"), "rel": row.get("rel"), "error": str(e)}

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for ok, err in ex.map(one, rows):
            if ok:
                done.append(ok)
            else:
                errors.append(err)
    if done:
        index.refresh_cameras()
    return {"indexed": len(done), "errors": errors, "partitions": sum(d["partitions"] for d in done)}


def unindexed_in_window(index: Index, mac: str, from_ms: int, to_ms: int, channel: int = 0, limit: int = 200) -> list[dict]:
    rows = index.files_in_window(mac, from_ms, to_ms, channel)
    return [r for r in rows if r["indexed_at"] is None][:limit]
