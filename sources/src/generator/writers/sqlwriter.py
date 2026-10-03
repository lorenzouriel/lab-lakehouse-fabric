"""PostgreSQL / SQL Server writers: the generated ``load.sh`` and README around the scripts in sqlscripts.py.

Templates use ``@@NAME@@`` placeholders (not str.format) so the shell code can keep its own braces.
"""
from __future__ import annotations

from .base import FormatWriter
from .envfile import ENV_LOADER
from .sqlscripts import (Dialect, Postgres, SqlServer, constraints_script, load_script, rowcounts_script,
                         schema_script)

LOAD_SH = {
    "postgresql": r"""#!/usr/bin/env bash
# Create the database if needed, then the schema, load the CSVs and add the constraints.
# Connection: POSTGRES_* from the repo-root .env (or the environment); PG* variables win if set.
#   POSTGRES_HOST  POSTGRES_PORT  POSTGRES_USER  POSTGRES_PASSWORD  POSTGRES_DATABASE
# Needs psql on this machine; `uv run generator load postgresql` runs this inside the compose container instead.
set -euo pipefail
cd "$(dirname "$0")"
@@ENV@@

export PGHOST="${PGHOST:-${POSTGRES_HOST:-127.0.0.1}}"
export PGPORT="${PGPORT:-${POSTGRES_PORT:-5432}}"
export PGUSER="${PGUSER:-${POSTGRES_USER:-postgres}}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
export PGDATABASE="${PGDATABASE:-${POSTGRES_DATABASE:-labsources}}"

if ! psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$PGDATABASE'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$PGDATABASE\""
fi
psql -v ON_ERROR_STOP=1 -f 01_schema.sql -f 02_load.sql -f 03_constraints.sql
psql -v ON_ERROR_STOP=1 -f 04_rowcounts.sql
""",
    "sqlserver": r"""#!/usr/bin/env bash
# Create the database if needed, then the schema, load the CSVs and add the constraints.
# Connection: SQLSERVER_* from the repo-root .env (or the environment).
#   SQLSERVER_HOST  SQLSERVER_PORT  SQLSERVER_USER  SQLSERVER_PASSWORD  SQLSERVER_DATABASE
#   SQLSERVER_SERVER     optional full server name replacing HOST,PORT
#   SQLSERVER_DATA_DIR   the CSV folder AS SQL SERVER SEES IT (BULK INSERT reads on the server)
# An empty SQLSERVER_USER means Windows authentication. Needs sqlcmd on this machine;
# `uv run generator load sqlserver` runs this inside the compose container instead.
set -euo pipefail
cd "$(dirname "$0")"
@@ENV@@

SERVER="${SQLSERVER_SERVER:-${SQLSERVER_HOST:-127.0.0.1},${SQLSERVER_PORT:-1433}}"
DATABASE="${SQLSERVER_DATABASE:-labsources}"
if [ -n "${SQLSERVER_USER:-}" ]; then
  export SQLCMDPASSWORD="${SQLSERVER_PASSWORD:-}"      # sqlcmd reads it from here, so it stays out of `ps`
  SQLCMD=(sqlcmd -S "$SERVER" -U "$SQLSERVER_USER" -C -b)
else
  SQLCMD=(sqlcmd -S "$SERVER" -E -C -b)
fi
# Default: the CSV folder of this checkout (right when SQL Server runs on this machine).
LOCAL_CSV="$(cd ../../csv/@@DATASET@@ && (pwd -W 2>/dev/null || pwd))"
# Passed as an environment variable: classic sqlcmd rejects -v values that contain ':' or '/'.
export DataDir="${DATA_DIR:-${SQLSERVER_DATA_DIR:-$LOCAL_CSV}}"

"${SQLCMD[@]}" -Q "IF DB_ID(N'$DATABASE') IS NULL CREATE DATABASE [$DATABASE]"

# BULK INSERT needs CODEPAGE = '65001' on Windows to read the UTF-8 CSVs, and rejects the option on Linux.
PLATFORM="$("${SQLCMD[@]}" -h -1 -W -Q "SET NOCOUNT ON; SELECT CASE WHEN @@VERSION LIKE '%Linux%' THEN 'Linux' ELSE 'Windows' END" | tr -d '[:space:]')"
if [ "$PLATFORM" = "Linux" ]; then export CodePageOption="MAXERRORS = 0,"; else export CodePageOption="CODEPAGE = '65001',"; fi

"${SQLCMD[@]}" -d "$DATABASE" -i 01_schema.sql,02_load.sql,03_constraints.sql
"${SQLCMD[@]}" -d "$DATABASE" -i 04_rowcounts.sql
""",
}

README = """# @@DATASET@@ - @@TITLE@@

Generated files; do not edit. Regenerate with `uv run generator generate --formats @@ENGINE@@`
(this also writes `../../csv/@@DATASET@@/`, which the load script reads).

| File | Purpose |
|---|---|
| `01_schema.sql` | `@@DATASET@@` schema and the 12 tables (drops and recreates them) |
| `02_load.sql` | Bulk-loads `../../csv/@@DATASET@@/*.csv` |
| `03_constraints.sql` | Primary keys, then foreign keys |
| `04_rowcounts.sql` | One row count per table |
| `load.sh` | Creates the database if missing and runs 01-04, connecting as set in the repo-root `.env` |

@@HOW@@
"""

HOW = {
    "postgresql": """## Load

```bash
docker compose up -d --wait postgres     # from the repo root; skip if you use your own server
uv run generator load postgresql         # runs load.sh inside the container (no psql needed on your machine)
uv run generator load postgresql --host  # runs load.sh with psql on your machine, using the .env URL
```

Change the target in the repo-root `.env` (`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_DATABASE`); an environment variable of the same name wins. `\\copy` is client-side, so the CSV files only
need to be readable by the machine running `psql`. Column names are lower-cased (`cod_cliente`, `data_fato`).""",
    "sqlserver": """## Load

```bash
docker compose up -d --wait sqlserver    # from the repo root; skip if you use your own server
uv run generator load sqlserver          # runs load.sh inside the container
uv run generator load sqlserver --host   # runs load.sh with sqlcmd on your machine, using the .env URL
```

Change the target in the repo-root `.env` (`SQLSERVER_HOST`, `SQLSERVER_PORT`, `SQLSERVER_USER`, `SQLSERVER_PASSWORD`,
`SQLSERVER_DATABASE`, or a full `SQLSERVER_SERVER`; empty user = Windows authentication).

`BULK INSERT` reads the CSVs on the **server**, so `SQLSERVER_DATA_DIR` must be the CSV folder as the server sees it:
`/lab/csv/@@DATASET@@` for the compose container (which mounts `./data` at `/lab`). For another server, copy
`data/csv/@@DATASET@@` there and set `SQLSERVER_DATA_DIR`; on the same Windows machine use its full path.
Requires SQL Server 2017+ (`FORMAT = 'CSV'`). Column names keep the original case.""",
}

TITLES = {"postgresql": "PostgreSQL", "sqlserver": "SQL Server"}


class SqlScriptWriter(FormatWriter):
    dialect: Dialect
    requires = ("csv",)

    def write_dimension(self, name, table):
        pass   # the scripts load the CSV files; nothing per table to write

    def write_facts_year(self, year, tables):
        pass

    def close(self):
        d, schema, engine = self.dialect, self.dataset, self.name

        def fill(text: str) -> str:
            return (text.replace("@@ENV@@", ENV_LOADER).replace("@@DATASET@@", self.dataset)
                    .replace("@@TITLE@@", TITLES[engine]).replace("@@ENGINE@@", engine)
                    .replace("@@HOW@@", HOW[engine]))

        files = {
            "01_schema.sql": schema_script(d, schema),
            "02_load.sql": load_script(d, schema, self.dataset),
            "03_constraints.sql": constraints_script(d, schema),
            "04_rowcounts.sql": rowcounts_script(d, schema),
            "load.sh": fill(LOAD_SH[engine]),
            "README.md": fill(README),
        }
        for name, text in files.items():
            (self.root / name).write_text(text, encoding="utf-8", newline="\n")


class PostgresWriter(SqlScriptWriter):
    name = "postgresql"
    dialect = Postgres()


class SqlServerWriter(SqlScriptWriter):
    name = "sqlserver"
    dialect = SqlServer()
