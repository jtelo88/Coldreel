"""CLI: `coldreel {sync,index,serve,stats,cache-gc,inspect}`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from . import __version__, service
from .config import load_settings
from .index import Index
from .remux import RemuxCache


def _ms(date: str) -> int:
    """`AAAA-MM-DD` (UTC) o ISO 8601 con zona → epoch ms."""
    d = datetime.fromisoformat(date)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def cmd_sync(s, args):
    idx = Index(s.db_path)
    print(json.dumps(service.sync(idx, s, verify=not args.no_verify), indent=1))


def cmd_stats(s, args):
    idx = Index(s.db_path)
    out = idx.stats()
    out["cameras_list"] = idx.cameras()
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_index(s, args):
    idx = Index(s.db_path)
    where, params = ["indexed_at IS NULL", "type='rotating'", "channel=?"], [args.channel]
    if args.mac:
        where.append("mac=?"); params.append(args.mac.upper())
    if getattr(args, "from"):
        where.append("end_ms > ?"); params.append(_ms(getattr(args, "from")))
    if args.to:
        where.append("start_ms < ?"); params.append(_ms(args.to))
    rows = [dict(r) for r in idx.db.execute(
        f"SELECT * FROM files WHERE {' AND '.join(where)} ORDER BY start_ms LIMIT ?", (*params, args.limit)).fetchall()]
    print(f"{len(rows)} ficheros por indexar", file=sys.stderr)
    if not rows:
        return
    print(json.dumps(service.index_files(idx, s, rows, jobs=args.jobs), indent=1, ensure_ascii=False))


def cmd_inspect(s, args):
    idx = Index(s.db_path)
    f = idx.file(args.file_id)
    if not f:
        sys.exit(f"no existe el fichero {args.file_id}")
    if f["indexed_at"] is None or args.force:
        print(json.dumps(service.index_file(idx, s, f), indent=1), file=sys.stderr)
    f["partitions"] = idx.partitions(args.file_id)
    print(json.dumps(f, indent=1, ensure_ascii=False))


def cmd_cache_gc(s, args):
    c = RemuxCache(s)
    print(json.dumps(c.gc(args.max_bytes), indent=1))


def cmd_serve(s, args):
    import uvicorn

    from .api import create_app

    app = create_app(s)
    uvicorn.run(app, host=args.host or s.host, port=args.port or s.port, log_level="info", access_log=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coldreel", description="Visor web sobre el respaldo del NVR")
    ap.add_argument("--config", help="fichero TOML (por defecto $COLDREEL_CONFIG o ./coldreel.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"coldreel {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="leer los registros nuevos de nvr-backup"); p.add_argument("--no-verify", action="store_true")
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("stats", help="estado del índice"); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("index", help="inspeccionar particiones (ubv-info) de ficheros sin indexar")
    p.add_argument("--mac"); p.add_argument("--from", dest="from"); p.add_argument("--to")
    p.add_argument("--channel", type=int, default=0); p.add_argument("--jobs", type=int); p.add_argument("--limit", type=int, default=500)
    p.set_defaults(fn=cmd_index)
    p = sub.add_parser("inspect", help="particiones de un fichero (lo indexa si hace falta)")
    p.add_argument("file_id", type=int); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("cache-gc", help="recortar la caché de MP4 al límite"); p.add_argument("--max-bytes", type=int)
    p.set_defaults(fn=cmd_cache_gc)
    p = sub.add_parser("serve", help="servidor web"); p.add_argument("--host"); p.add_argument("--port", type=int)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings(args.config)
    args.fn(s, args)


if __name__ == "__main__":
    main()
