"""Command line: generate | load | validate."""
from __future__ import annotations

import argparse
import sys

from .config import DATA_DIR, load_config
from .pipeline import generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generator", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("generate", help="write every source format from config + seed into data/<format>/<dataset>/")

    p = sub.add_parser("load", help="load generated data into SQL Server / PostgreSQL / MongoDB "
                                    "(docker compose, or your own servers with --host)")
    p.add_argument("targets", nargs="*", choices=["all", "postgresql", "sqlserver", "mongodb"], default=None,
                   help="what to load (default: all)")
    p.add_argument("--host", action="store_true",
                   help="run the scripts with psql/sqlcmd/mongoimport on this machine against the URLs in .env "
                        "(default: inside the docker compose containers)")
    p.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")

    p = sub.add_parser("validate", help="check integrity, key invariants and value sanity of a generated dataset")
    p.add_argument("--format", choices=["parquet", "csv", "json"], default="parquet")

    args = parser.parse_args(argv)
    cfg = load_config()

    if args.command == "generate":
        print(f"generating {cfg['dataset']} {cfg['period']['start']} -> {cfg['period']['end']} (seed {cfg['seed']})")
        counts = generate(cfg)
        print("\n".join(f"  {name:20s} {rows:>12,d}" for name, rows in counts.items()))
    elif args.command == "load":
        from .loader import load
        return load(args.targets or ["all"], cfg["dataset"], host=args.host, dry_run=args.dry_run)
    elif args.command == "validate":
        from .validate import report, validate
        return 0 if report(validate(cfg, DATA_DIR, args.format)) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
