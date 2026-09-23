"""Configuración: valores por defecto ← fichero TOML ← variables de entorno `COLDREEL_*`.

Nada aquí contiene direcciones ni nombres concretos de la instalación: eso va en el
`coldreel.toml` local (ignorado por git) o en el entorno del servicio.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

DEFAULT_TOOLS = Path("/nvr/video/meta/tools/remux-v4.2.2/bin")


@dataclass(frozen=True)
class Settings:
    # Archivo escrito por nvr-backup (solo lectura para Coldreel).
    archive_root: Path = Path("/nvr/video")
    # Datos propios de Coldreel: índice SQLite y caché de MP4.
    data_dir: Path = Path("var")
    cache_dir: Path = Path("var/cache")
    cache_max_bytes: int = 50 * 2**30
    # Herramientas de petergeneric/unifi-protect-remux.
    remux_bin: Path = DEFAULT_TOOLS / "remux"
    ubvinfo_bin: Path = DEFAULT_TOOLS / "ubv-info"
    # Prioridad: el nodo también copia el NVR y sirve PBS; Coldreel cede siempre.
    nice: int = 19
    ionice_idle: bool = True
    # Concurrencia de trabajos pesados.
    index_jobs: int = 2
    remux_jobs: int = 2
    # Servidor HTTP.
    host: str = "127.0.0.1"
    port: int = 8080
    # Autenticación básica opcional: "usuario:clave". Sin ella, cualquiera en la red la ve.
    auth: str | None = None
    # Resincronización periódica desde el registro (segundos); 0 = solo al arrancar.
    sync_interval_s: int = 300

    @property
    def db_path(self) -> Path:
        return self.data_dir / "coldreel.db"

    @property
    def ledger_dir(self) -> Path:
        return self.archive_root / "meta" / "ledger"

    @property
    def ubvinfo_dir(self) -> Path:
        return self.archive_root / "meta" / "ubvinfo"


_PATH_FIELDS = {"archive_root", "data_dir", "cache_dir", "remux_bin", "ubvinfo_bin"}
_INT_FIELDS = {"cache_max_bytes", "nice", "index_jobs", "remux_jobs", "port", "sync_interval_s"}
_BOOL_FIELDS = {"ionice_idle"}


def _coerce(name: str, value):
    if value is None:
        return None
    if name in _PATH_FIELDS:
        return Path(str(value)).expanduser()
    if name in _INT_FIELDS:
        return int(value)
    if name in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    return str(value)


def load_settings(config_path: str | os.PathLike | None = None, env: dict | None = None) -> Settings:
    """Orden: defaults → TOML (`config_path`, `$COLDREEL_CONFIG` o `./coldreel.toml`) → `COLDREEL_<CAMPO>`."""
    env = dict(os.environ if env is None else env)
    s = Settings()
    path = config_path or env.get("COLDREEL_CONFIG") or "coldreel.toml"
    p = Path(path)
    if p.is_file():
        with open(p, "rb") as fh:
            data = tomllib.load(fh)
        known = {f.name for f in fields(Settings)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"claves desconocidas en {p}: {sorted(unknown)}")
        s = replace(s, **{k: _coerce(k, v) for k, v in data.items()})
    overrides = {}
    for f in fields(Settings):
        key = f"COLDREEL_{f.name.upper()}"
        if key in env and env[key] != "":
            overrides[f.name] = _coerce(f.name, env[key])
    if overrides:
        s = replace(s, **overrides)
    return s
