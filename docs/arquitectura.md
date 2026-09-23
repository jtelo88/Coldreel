# Arquitectura (fase 1)

## Datos de entrada (solo lectura)

| Ruta (bajo `archive_root`) | Qué es | Quién la escribe |
|---|---|---|
| `ubv/AAAA/MM/DD/<MAC>_<canal>_<tipo>_<inicio_ms>.ubv` | vídeo crudo, 1 GiB, N particiones | nvr-backup |
| `meta/ledger/ledger-AAAA-MM-DD.jsonl` | registro encadenado (`nvr-ledger/1`) | nvr-backup |
| `meta/ubvinfo/<rel>.json.xz` | salida completa de `ubv-info --json` | nvr-backup `inspect` |
| `meta/tools/remux-*/bin/{remux,ubv-info}` | herramientas | operador |

Registros que Coldreel interpreta: `ingest.copy` / `variant.copy` con `success` (→ `files`) e
`inspect` con `success` (→ `partitions`, fuente `ledger-inspect`). El resto se ignora pero se
verifica (hash y cadena).

## Índice (`data_dir/coldreel.db`, SQLite WAL)

- `files(id, db_id, variant, mac, camera, channel, type, start_ms, end_ms, size, sha256, rel, dst,
  copied_at, indexed_at, index_source, video_track, partitions_n)` — `UNIQUE(db_id, variant)`.
- `partitions(file_id, ordinal, t_first_ms, t_last_ms, byte_offset, bytes_used, video_track,
  frames_video, frames_audio, keyframes, smart_events, sequence_gaps)`.
- `cameras(mac, name, first_ms, last_ms, files, bytes, video_track)` — agregado por MAC, nombre del
  fichero más reciente (los nombres se reutilizan entre cámaras; la MAC no).
- `state(k, v)` — `ledger_cursor` = `{file, offset, last_hash}`.

Tiempos siempre en **epoch ms UTC**; la zona horaria es cosa del cliente (`tz` en `/api/coverage`).

## Particiones

`ubv.summarize()` recorre `partitions[].entries[]`: `t_first/t_last` = min/max del reloj de pared
de los frames de vídeo (`wc / clock_rate`), con `ClockSync` como respaldo; `video_track` por
`track_id` (7 = H.264, 1003 = HEVC, 1004 = AV1); `keyframes`, `smart_events`, `sequence_gaps`.
`ubv.from_ledger_summary()` traduce el resumen más pobre que deja `nvr-backup inspect`.

## Caché de vídeo (`cache_dir/<file_id>/`)

`manifest.json` (`sha256` del origen, `clips: {ordinal: nombre.mp4}`, `unmatched_mp4`, tiempos) +
los MP4. `RemuxCache.ensure()` serializa por fichero (candado) y limita en global (semáforo
`remux_jobs`); el directorio se escribe en `.tmp-*` y se renombra al final. `gc()` borra los
menos usados (mtime del manifiesto = último acceso) hasta `cache_max_bytes`.

## Concurrencia

Un proceso `uvicorn`; los endpoints son síncronos (hilo del pool de Starlette). Una sola conexión
SQLite protegida por `RLock` (lecturas y escrituras). Trabajos externos (`ubv-info`, `remux`) con
`nice`/`ionice`. Sincronización inicial antes de servir y periódica cada `sync_interval_s`.

## Fases siguientes (del diseño de referencia)

2. Eventos y miniaturas (volcado de Protect → Postgres) con salto al segundo exacto y HLS diario.
3. Personas/vehículos: grupos de caras de Protect, embeddings (pgvector), etiquetado propio.
4. Transcodificación en caché para clientes sin HEVC/AV1, exportar clips, búsqueda por texto.
