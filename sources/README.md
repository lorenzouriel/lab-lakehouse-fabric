# sources

The six source systems the lakehouse ingests, all generated from one seeded model of the **Fruit Juice (Sucos de
Frutas)** dataset: 2013–2026, daily grain. Vendored and trimmed from `lab-sources`. The same seed always gives
byte-identical files, so every source carries **the same data in a different shape**.

```bash
uv sync
uv run generator generate        # all six formats -> data/<format>/fruit_juice/   (~2.5 min)
uv run generator validate        # integrity, key invariants, value sanity; exit 1 on any failure
uv run pytest

cp .env.example .env             # set ACCEPT_EULA=Y for SQL Server
docker compose up -d --wait      # SQL Server + PostgreSQL + MongoDB
uv run generator load            # load the generated data into all three (or: load postgresql | sqlserver | mongodb)
```

## Sources

| Source | Output (`data/<format>/fruit_juice/`) | Shape the lakehouse has to undo |
|---|---|---|
| `csv` | one UTF-8 file per table | everything is text; codes keep leading zeros only if read as strings |
| `parquet` | facts hive-partitioned `year=YYYY/` (zstd), dims single file | typed; partition column `year` added |
| `json` | JSON Lines, ISO-8601 dates, `null` for NULL | dates are strings |
| `sqlserver` | DDL + `BULK INSERT` scripts over the CSVs → database `labsources` | T-SQL types |
| `postgresql` | DDL + `\copy` scripts over the CSVs → database `labsources` | identifiers lower-cased |
| `mongodb` | Extended-JSON flat collections **+ denormalised `sales`** | `$date`, doubles, lower-case fields, nested `cliente` / `produto` / `fabrica` / `organizacional` |

MongoDB `sales` holds one document per `tab_fato001` row with the dimensions embedded and the row's `frete`;
`_id = "<COD_DIA>|<COD_CLIENTE>|<COD_PRODUTO>|<COD_FABRICA>"`.

## Tables

| Table | Grain | Rows |
|---|---|---|
| `tab_fato001` | day × client × product × factory: revenue, tax, variable cost, units, litres | 1,050,428 |
| `tab_fato002` | same keys as 001: freight | 1,050,428 |
| `tab_fato003` | day × factory: fixed cost | ~10k |
| `tab_fato004` | day × client × product × org: revenue target | ~1.02M |
| `tab_fato005` | day × product × factory: variable-cost target | ~257k |
| `dim_tempo` | day, Portuguese labels | 5,113 |
| `dim_cliente`, `dim_produto`, `dim_marca`, `dim_categoria`, `dim_fabrica`, `dim_organizacional` | fixed universe (`src/reference/`) | 57 / 35 / 9 / 3 / 3 / 19 |

Facts keep upper-case column names (`COD_DIA`, `FATURAMENTO`, …); dimensions use `Cod_Cliente`-style names.
Codes such as `COD_FABRICA = "002"` are strings. `dim_organizacional` is a nested-set hierarchy
(`Esquerda` / `Direita` / `Nivel`).

Key invariants, enforced by construction and by `validate`: 002 has one row per 001 row; 003 = (factory, day)
with sales; 004 = distinct (day, client, product, org) of 001; 005 = distinct (day, product, factory) of 001.

**Reconciliation reference**: total `FATURAMENTO` = 2,664,425,394.54 over 1,050,428 `tab_fato001` rows (seed 42).

## Databases

- `docker-compose.yml` runs SQL Server 2025 (host port 1440), PostgreSQL 17 (5432) and MongoDB 8 (27017), bound to
  `127.0.0.1`. `./data` is mounted read-only at `/lab`, so `generator load` runs the generated scripts inside the
  containers; nothing to install locally.
- `.env` (copy `.env.example`, git-ignored) holds hosts, ports and credentials for compose and the load scripts.
  `generator load --host` runs the same scripts with local `psql` / `sqlcmd` / `mongoimport` instead.
- Hosts default to `127.0.0.1`, not `localhost`: on Windows `localhost` resolves to IPv6 first and the ports are
  IPv4-only. Default passwords are for local lab use only.

## Model

Calibrated once from the original 2013–2015 data (`src/calibration/fruit_juice.json`, committed), then extended:
volume per (client, product, factory) with seasonality and decaying yearly growth; prices, tax and variable cost
step yearly; facts 002–005 are derived from 001 so keys line up. Randomness is drawn per (year, month), so the
data is fully reproducible. Model knobs live in `src/config/fruit_juice.toml`.

## Limits

- Dimensions are static; only facts and the calendar extend in time. Oct–Dec 2026 are future-dated.
- Targets (004/005) and fixed cost (003) exist only for days with sales.
- About 0.01% of rows have tax + variable cost above revenue.
