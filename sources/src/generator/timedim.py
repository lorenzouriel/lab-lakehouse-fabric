"""Daily time dimension (same columns and Portuguese labels as the original Dim_Tempo)."""
from __future__ import annotations

from datetime import date, timedelta

import pyarrow as pa

MONTHS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
          "Setembro", "Outubro", "Novembro", "Dezembro"]
WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]  # date.weekday()
ORDINALS = ["Primeiro", "Segundo", "Terceiro", "Quarto"]

SCHEMA = pa.schema([
    ("Cod_Dia", pa.string()), ("Data", pa.date32()), ("Cod_Semana", pa.int32()),
    ("Nome_Dia_Semana", pa.string()), ("Cod_Mes", pa.int32()), ("Nome_Mes", pa.string()),
    ("Cod_Mes_Ano", pa.string()), ("Nome_Mes_Ano", pa.string()), ("Cod_Trimestre", pa.int32()),
    ("Nome_Trimestre", pa.string()), ("Cod_Trimestre_Ano", pa.string()),
    ("Nome_Trimestre_Ano", pa.string()), ("Cod_Semestre", pa.int32()),
    ("Nome_Semestre", pa.string()), ("Cod_Semestre_Ano", pa.string()),
    ("Nome_Semestre_Ano", pa.string()), ("Ano", pa.string()), ("Tipo_Dia", pa.string()),
])


def build_dim_tempo(start: date, end: date) -> pa.Table:
    cols: dict[str, list] = {f.name: [] for f in SCHEMA}
    day = start
    while day <= end:
        quarter, semester = (day.month - 1) // 3 + 1, (day.month - 1) // 6 + 1
        year = day.year
        cols["Cod_Dia"].append(day.strftime("%Y%m%d"))
        cols["Data"].append(day)
        # Weeks are 7-day blocks counted from 1 January (not ISO weeks) - matches the original.
        cols["Cod_Semana"].append((day.timetuple().tm_yday - 1) // 7 + 1)
        cols["Nome_Dia_Semana"].append(WEEKDAYS[day.weekday()])
        cols["Cod_Mes"].append(day.month)
        cols["Nome_Mes"].append(MONTHS[day.month - 1])
        cols["Cod_Mes_Ano"].append(f"{day.month:02d}-{year}")
        cols["Nome_Mes_Ano"].append(f"{MONTHS[day.month - 1]} {year}")
        cols["Cod_Trimestre"].append(quarter)
        cols["Nome_Trimestre"].append(f"{ORDINALS[quarter - 1]} Trimestre")
        cols["Cod_Trimestre_Ano"].append(f"{quarter:02d}-{year}")
        cols["Nome_Trimestre_Ano"].append(f"{ORDINALS[quarter - 1]} Trimestre {year}")
        cols["Cod_Semestre"].append(semester)
        cols["Nome_Semestre"].append(f"{ORDINALS[semester - 1]} Semestre")
        cols["Cod_Semestre_Ano"].append(f"{semester:02d}-{year}")
        cols["Nome_Semestre_Ano"].append(f"{ORDINALS[semester - 1]} Semestre {year}")
        cols["Ano"].append(str(year))
        cols["Tipo_Dia"].append("Dia Útil" if day.weekday() < 5 else "Fim de Semana")
        day += timedelta(days=1)
    return pa.table(cols, schema=SCHEMA)
