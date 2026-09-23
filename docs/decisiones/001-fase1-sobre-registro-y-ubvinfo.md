# ADR-001: La fase 1 se construye sobre el registro de `nvr-backup` y `ubv-info`, con SQLite propio

**Estado:** ACEPTADA · **Fecha:** 2026-09-23 · **Contexto:** `techlab/proyectos/2026-09-22-visor-web-respaldo-nvr.md`

## Contexto

El diseño de referencia propone Python + FastAPI + **Postgres 16 con pgvector**, alimentado por el
volcado completo de la base de datos de Protect. El 2026-09-23, al arrancar el proyecto:

- El volcado de Protect **no existe todavía** (se genera al terminar la copia inicial, ~26-sep).
- En MIA-PVE-02 no hay Postgres, ffmpeg, Node ni pip; `rpool` está DEGRADED y no se reinicia.
- Sí existe todo lo que hace falta para una línea de tiempo real: el registro (`ledger`) con
  cada fichero, su cámara, MAC, canal y rango de tiempo; `ubv-info` para las particiones; `remux`.

## Decisión

1. **Fase 1 = línea de tiempo por particiones + reproducción**, sin Protect. La partición es la
   unidad de grabación real (verificado: 16 particiones = 16 MP4 = 16 grabaciones por movimiento).
2. **Índice propio en SQLite**, derivado y regenerable. Postgres entra en la fase 2, cuando haya
   que cargar `events`/`thumbnails`/caras (4 M de filas, 184 GB de miniaturas, vectores). El índice
   de la fase 1 se mantiene: Postgres no lo sustituye, lo complementa.
3. **Fuente de verdad = `meta/ledger/*.jsonl`**, no `ledger.sqlite` (que es el índice de nvr-backup
   y puede rehacerse). Lectura incremental por (fichero, offset) con verificación de la cadena.
4. **Particiones bajo demanda**: `ubv-info` cuesta ~2 s/fichero y casi nada de E/S; indexar un día
   al abrirlo es aceptable y evita leer 17 TB por adelantado mientras la copia sigue. Si
   `nvr-backup inspect` ya dejó `meta/ubvinfo/<rel>.json.xz`, se usa eso.
5. **`remux` del fichero entero, caché LRU.** `remux` no admite elegir partición; 5 s por GiB con
   `--fast-start` es barato. Mapeo partición → MP4 por segundo de inicio (nombre del MP4).
6. **Front sin toolchain** (HTML/JS planos): no hay Node en el nodo y no hace falta para esto.
7. **Identificar particiones por posición**, no por `index` de cabecera (se repite: `0, 0, 1, …`).

## Consecuencias

- Hasta la fase 2 no hay eventos, miniaturas ni caras: la línea de tiempo muestra grabaciones.
- El AV1 de pista 1004 se ve en la línea de tiempo pero no se reproduce hasta compilar `remux main`.
  **Resuelto el 2026-09-23:** build `remux-main-d09c3e942bef` (v4.2.2-13, FFmpeg 9) compilado por
  `.github/workflows/build-remux.yml`, verificado con un `.ubv` AV1 4K real (1 partición → 1 MP4) y
  sin regresión en H.264 (mismos nombres de MP4). Instalado en `meta/tools/remux-main-<sha>/`.
- Un solo proceso con una conexión SQLite serializada por candado: suficiente para un usuario
  doméstico; si hiciera falta más, pasar a una conexión por hilo.

## Revisar si…

- Aparece un release de `remux` con AV1 nuevo o con selección de partición.
- El esquema del registro (`nvr-ledger/1`) cambia.
