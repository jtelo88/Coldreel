"""Lectura del registro de `nvr-backup` (`meta/ledger/ledger-AAAA-MM-DD.jsonl`).

El registro es JSONL append-only encadenado por hash: cada registro lleva `prev_record_sha256`
(hash del anterior) y `record_sha256` (hash del propio registro canonizado sin ese campo).
Es la fuente de verdad del archivo; Coldreel lo lee de forma incremental (fichero + offset)
y verifica la cadena mientras avanza. Nunca lo escribe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

GENESIS = "0" * 64


class LedgerError(Exception):
    pass


def canonical(rec: dict) -> bytes:
    """Misma canonización que nvr-backup: claves ordenadas, sin espacios, UTF-8 sin escapar."""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def record_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return hashlib.sha256(canonical(body)).hexdigest()


def ledger_files(ledger_dir: Path) -> list[Path]:
    return sorted(ledger_dir.glob("ledger-*.jsonl"))


@dataclass
class Cursor:
    """Posición de lectura: fichero actual, offset consumido y hash del último registro."""

    file: str = ""
    offset: int = 0
    last_hash: str = GENESIS

    def to_dict(self) -> dict:
        return {"file": self.file, "offset": self.offset, "last_hash": self.last_hash}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Cursor":
        if not d:
            return cls()
        return cls(d.get("file", ""), int(d.get("offset", 0)), d.get("last_hash", GENESIS))


def iter_records(ledger_dir: Path, cursor: Cursor, verify: bool = True) -> Iterator[tuple[dict, Cursor]]:
    """Recorre los registros posteriores al cursor. Devuelve (registro, cursor tras consumirlo).

    Solo consume líneas terminadas en `\\n`: nvr-backup escribe cada registro con un `write` +
    `fsync`, pero una línea a medias (proceso escribiendo ahora mismo) se deja para la siguiente
    pasada. Si `verify`, comprueba el hash de cada registro y la continuidad de la cadena.
    """
    files = ledger_files(ledger_dir)
    if cursor.file and cursor.file not in {f.name for f in files}:
        raise LedgerError(f"el fichero del cursor ya no existe: {cursor.file}")
    cur = Cursor(cursor.file, cursor.offset, cursor.last_hash)
    for f in files:
        if cur.file and f.name < cur.file:
            continue
        start = cur.offset if f.name == cur.file else 0
        with open(f, "rb") as fh:
            fh.seek(start)
            pos = start
            for line in fh:
                if not line.endswith(b"\n"):
                    break  # línea incompleta: la retomará la próxima pasada
                pos += len(line)
                if line.strip() == b"":
                    continue
                rec = json.loads(line)
                if verify:
                    if rec.get("prev_record_sha256") != cur.last_hash:
                        raise LedgerError(
                            f"cadena rota en {f.name} registro {rec.get('record_id')}: "
                            f"prev={rec.get('prev_record_sha256')} esperado={cur.last_hash}"
                        )
                    if record_hash(rec) != rec.get("record_sha256"):
                        raise LedgerError(f"hash incorrecto en {f.name} registro {rec.get('record_id')}")
                cur = Cursor(f.name, pos, rec["record_sha256"])
                yield rec, cur
        # Un fichero sin registros nuevos no mueve el cursor: la próxima pasada lo relee desde
        # el mismo offset (barato) y sigue con los siguientes.


def file_record_fields(rec: dict) -> dict | None:
    """Campos de un registro `ingest.copy`/`variant.copy` con éxito, normalizados para el índice."""
    if rec.get("event_type") not in ("ingest.copy", "variant.copy") or rec.get("event_outcome") != "success":
        return None
    s = rec["src"]
    return {
        "db_id": s["db_id"],
        "variant": int(rec.get("variant", 1)),
        "mac": s["mac"],
        "camera": s["camera"],
        "channel": int(s["channel"]),
        "type": s["type"],
        "start_ms": int(s["start"]),
        "end_ms": int(s["end"]),
        "size": int(rec["bytes"]),
        "sha256": rec["hash_src"],
        "rel": s["rel"],
        "dst": rec["dst_path"],
        "copied_at": rec["ts"],
    }
