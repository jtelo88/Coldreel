# CLAUDE.md — repo `Coldreel`

Visor web sobre el respaldo del NVR (UniFi Protect). Diseño de origen y contexto de la
infraestructura: repo `techlab` (`proyectos/2026-09-22-visor-web-respaldo-nvr.md`, ADR-009,
`scripts/nvr-backup/`, `runbooks/respaldo-nvr.md`). Léelos antes de cambiar el modelo de datos.

## Reglas
- **Nunca escribir en `/nvr/video/`** (`ubv/`, `meta/ledger/`, `meta/ubvinfo/`). Coldreel solo lee.
  Todo lo suyo va a `data_dir`/`cache_dir` (por defecto `var/`, ignorado por git) y es regenerable.
- **No instalar nada en el NVR** ni hablar con él: la única fuente es el archivo local.
- **Ceder recursos**: el nodo copia el NVR y sirve PBS. Cualquier proceso pesado va con
  `nice 19` + `ionice -c3` (`ubv.low_priority_prefix`) y concurrencia acotada (`index_jobs`, `remux_jobs`).
- **Sin datos de la instalación en el repo**: ni IPs, ni MACs reales, ni nombres de cámaras/personas.
  Los tests usan MACs y nombres inventados. La configuración real vive en `coldreel.toml` (ignorado).
- **Particiones por posición (`ordinal`)**, no por el `index` de la cabecera (se repite).
- **Cambios de esquema** del índice: es derivado; basta borrar `var/coldreel.db` y `coldreel sync`,
  pero documenta el cambio en `docs/arquitectura.md`.

## Cómo trabajar
- Entorno: `uv sync` · tests: `uv run pytest` (archivo y herramientas simulados, no necesita el NVR).
- Probar en real: `uv run coldreel sync && uv run coldreel serve` en MIA-PVE-02 (ver `techlab`).
- Idioma: **código e identificadores en inglés; docs, comentarios y commits en español**
  (misma convención que `techlab`). Commits descriptivos, en imperativo.
- Antes de dar algo por hecho, probarlo contra el archivo real (`curl` a la API) además de los tests.

## Mapa
`config` → `ledger` (lectura incremental y verificación de la cadena) → `index` (SQLite: files,
partitions, cameras) ← `ubv` (ubv-info / meta/ubvinfo) · `remux` (MP4 por partición + caché LRU)
· `service` (operaciones compartidas) · `api` (FastAPI + `web/`) · `cli`.
