"""Coldreel — visor web sobre el respaldo del NVR de UniFi Protect.

El archivo (`/nvr/video/ubv/` + `meta/`) lo escribe `nvr-backup` y es inmutable para Coldreel:
aquí solo se lee. Coldreel construye su propio índice (SQLite) y derivados regenerables
(MP4 por partición) en un directorio aparte.
"""

__version__ = "0.1.0"
