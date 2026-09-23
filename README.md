# Coldreel

Visor web sobre el **respaldo del NVR de UniFi Protect**: línea de tiempo por cámara y día,
construida a partir de los `.ubv` crudos que archiva `nvr-backup`, con reproducción MP4 bajo
demanda. No toca el NVR ni el archivo: solo lee y construye derivados regenerables.

Estado: **fase 1 (MVP)** funcional — cámaras, cobertura por día, línea de tiempo con las
grabaciones reales (particiones), reproducción con salto (`Range`), caché LRU de MP4.
Origen y diseño completo: `proyectos/2026-09-22-visor-web-respaldo-nvr.md` en el repo `techlab`.

## Cómo funciona

```
/nvr/video/ubv/…                 .ubv crudos (solo lectura)        ┐
/nvr/video/meta/ledger/*.jsonl   registro encadenado de nvr-backup ┼─► coldreel sync ─► índice SQLite propio
/nvr/video/meta/ubvinfo/         ubv-info ya calculado (si existe) ┘        (ficheros · particiones · cámaras)
                                                                                    │
   navegador ◄── MP4 (Range) ◄── FastAPI ◄── remux bajo demanda + caché LRU ◄───────┘
```

- **Registro → índice.** `coldreel sync` lee los registros nuevos del ledger (incremental,
  verificando la cadena de hashes) y mantiene `files` y `cameras`. Un archivo de 17 500 ficheros
  se sincroniza en ~2 s la primera vez; después, milisegundos.
- **Particiones.** Cada `.ubv` (1 GiB) contiene N particiones = grabaciones continuas. Se obtienen
  con `ubv-info --json` (~2 s por fichero, sin leerlo entero) o de la copia que `nvr-backup inspect`
  deja en `meta/ubvinfo/`. Se indexan **bajo demanda** (al abrir un día) o en bloque (`coldreel index`).
- **Vídeo.** `GET /clip/{fichero}/{partición}.mp4` remuxea el fichero entero con `remux` (~5 s por
  GiB, sin recodificar), guarda un MP4 por partición en la caché y sirve el pedido con `Range`.
- **Front.** HTML + JS planos, sin build. Día y horas en la zona horaria del navegador.

## Requisitos

- Python ≥ 3.13 y [`uv`](https://docs.astral.sh/uv/) (sin `pip` en el sistema).
- Binarios `remux` y `ubv-info` de [petergeneric/unifi-protect-remux](https://github.com/petergeneric/unifi-protect-remux)
  (el respaldo los guarda en `meta/tools/remux-v4.2.2/bin/`).
- Lectura de `/nvr/video` (ledger, ubvinfo y `.ubv`). No hace falta root.

## Uso

```sh
uv sync                                   # entorno + dependencias
cp deploy/coldreel.toml.ejemplo coldreel.toml   # ajustar rutas/puerto (ignorado por git)
uv run coldreel sync                      # leer el registro de nvr-backup
uv run coldreel stats                     # qué hay en el índice
uv run coldreel serve                     # http://127.0.0.1:8080
uv run coldreel index --mac <MAC> --from 2025-03-01 --to 2025-03-08 --jobs 4   # particiones en bloque
uv run coldreel cache-gc                  # recortar la caché al límite
uv run pytest                             # tests (con archivo y herramientas simulados)
```

Configuración: `coldreel.toml` (o `$COLDREEL_CONFIG`) y variables `COLDREEL_<CAMPO>`; campos en
`src/coldreel/config.py`. Por defecto escucha solo en `127.0.0.1`; para exponerla en la LAN, poner
`host` y **`auth = "usuario:clave"`** (las grabaciones son privadas).

### API

| Ruta | Qué devuelve |
|---|---|
| `GET /api/health` | estado del índice y de la caché |
| `GET /api/cameras` | cámaras por MAC (nombre más reciente, rango, ficheros, bytes) |
| `GET /api/coverage?mac&tz` | días con vídeo en la zona `tz`, con cuántos ficheros están indexados |
| `GET /api/timeline?mac&from_ms&to_ms` | ficheros de la ventana y sus particiones (`clip_url`, `cached`) |
| `POST /api/index?mac&from_ms&to_ms` | inspecciona los ficheros sin particiones de la ventana |
| `POST /api/sync` | relee el registro |
| `GET /api/files/{id}` | un fichero, sus particiones y el manifiesto de caché |
| `GET/HEAD /clip/{id}/{n}.mp4` | MP4 de la partición `n` (remux si hace falta; `Range` para seek) |

## Límites conocidos

- **AV1 de firmware nuevo (pista 1004)**: `remux` v4.2.2 no lo convierte. El índice y la línea de
  tiempo sí funcionan (`ubv-info` lo lee); la reproducción devuelve `501` hasta tener un `remux`
  compilado de `main`. Esas particiones se marcan en la interfaz.
- **HEVC/AV1 en el navegador**: se sirve el códec nativo. Chrome/Firefox reproducen AV1; HEVC
  depende del hardware (Safari sí). La transcodificación en caché es de la fase 4.
- **Eventos, miniaturas y caras** (fases 2–3) necesitan el volcado de la base de datos de Protect,
  que el respaldo genera al terminar la copia inicial.

## Estructura

```
src/coldreel/   config · ledger (lectura del registro) · ubv (particiones) · index (SQLite)
                remux (caché MP4) · service · api (FastAPI) · cli · web/ (front estático)
tests/          archivo y herramientas simulados; sin dependencia del NVR
docs/           arquitectura y decisiones (ADR)
deploy/         unidad systemd y configuración de ejemplo
```

## Principios

1. El archivo (`ubv/`, `meta/ledger/`) es **inmutable** para Coldreel. Nunca escribe ahí.
2. Todo lo que Coldreel genera (índice, MP4) es **regenerable**: se puede borrar `var/` sin perder nada.
3. Corre en el mismo nodo que el respaldo y **cede siempre**: `nice 19`, E/S `idle`, pocos hilos.
4. Sin secretos ni datos de la instalación en el repo (IPs, nombres): van en `coldreel.toml`.
