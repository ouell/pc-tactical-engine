"""Relatórios pós-jogo sobre o DuckDB: resumo, mapa de calor e exportação.

python -m src.storage.post_match_reports                       # última partida
python -m src.storage.post_match_reports --match ID --export parquet
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import duckdb
import numpy as np
from numpy.typing import NDArray

from config.settings import DATA_DIR
from src.models import Team
from src.storage.telemetry_db import DB_FILE

type ExportFormat = Literal["parquet", "csv"]

TABLES = ("frames", "telemetry", "events")
SHADES = " .:-=+*#%@"


@dataclass(frozen=True, slots=True)
class MatchSummary:
    match_id: str
    frames: int
    duration_s: float
    control_a: float | None  # média da fração do campo dominada pelo time A
    possession_a: float | None  # fração dos frames com posse definida em que a bola era do A
    events: dict[str, int]


def open_db(path: Path = DB_FILE) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


def latest_match(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute("SELECT match_id FROM matches ORDER BY started_at DESC LIMIT 1").fetchone()
    return None if row is None else str(row[0])


def summarize(conn: duckdb.DuckDBPyConnection, match_id: str) -> MatchSummary:
    row = conn.execute(
        """
        SELECT count(*), coalesce(max(ts) - min(ts), 0), avg(control_a),
               avg(CASE possession WHEN 'A' THEN 1.0 WHEN 'B' THEN 0.0 END)
        FROM frames WHERE match_id = ?
        """,
        [match_id],
    ).fetchone()
    frames, duration_s, control_a, possession_a = row or (0, 0.0, None, None)
    events = conn.execute(
        "SELECT type, count(*) FROM events WHERE match_id = ? GROUP BY type ORDER BY 2 DESC",
        [match_id],
    ).fetchall()
    return MatchSummary(match_id, frames, duration_s, control_a, possession_a, dict(events))


def heatmap(
    conn: duckdb.DuckDBPyConnection, match_id: str, team: Team, bins: tuple[int, int] = (21, 14)
) -> NDArray[np.int64]:
    """Contagem de presença por célula do campo, `grid[linha_y, coluna_x]`."""
    nx, ny = bins
    cells = conn.execute(
        """
        SELECT least(floor(x * $nx), $nx - 1)::INTEGER AS i,
               least(floor(y * $ny), $ny - 1)::INTEGER AS j,
               count(*)
        FROM telemetry WHERE match_id = $match AND team = $team
        GROUP BY i, j
        """,
        {"nx": nx, "ny": ny, "match": match_id, "team": team},
    ).fetchall()
    grid = np.zeros((ny, nx), dtype=np.int64)
    for i, j, count in cells:
        grid[j, i] = count
    return grid


def export(
    conn: duckdb.DuckDBPyConnection, match_id: str, out_dir: Path, fmt: ExportFormat = "parquet"
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in TABLES:
        path = out_dir / f"{match_id}_{table}.{fmt}"
        relation = conn.sql(
            f"SELECT * FROM {table} WHERE match_id = $match ORDER BY tick_id",
            params={"match": match_id},
        )
        match fmt:
            case "parquet":
                relation.write_parquet(str(path))
            case "csv":
                relation.write_csv(str(path))
        paths.append(path)
    return paths


def render_heatmap(grid: NDArray[np.int64]) -> str:
    levels = np.rint(grid / max(int(grid.max()), 1) * (len(SHADES) - 1)).astype(int)
    border = "+" + "-" * grid.shape[1] + "+"
    rows = ("|" + "".join(SHADES[level] for level in row) + "|" for row in levels)
    return "\n".join([border, *rows, border])


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Relatório pós-jogo.")
    parser.add_argument("--db", type=Path, default=DB_FILE)
    parser.add_argument("--match", help="match_id (padrão: a partida mais recente)")
    parser.add_argument("--export", choices=["parquet", "csv"])
    parser.add_argument("--out", type=Path, default=DATA_DIR / "exports")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Banco não encontrado: {args.db}. Grave uma partida primeiro.")
    with open_db(args.db) as conn:
        match_id = args.match or latest_match(conn)
        if match_id is None:
            raise SystemExit("Nenhuma partida gravada ainda.")
        summary = summarize(conn, match_id)
        print(f"Partida {summary.match_id}: {summary.frames} frames, {summary.duration_s:.0f} s")
        print(f"Domínio territorial do time A: {_percent(summary.control_a)}")
        print(f"Posse do time A: {_percent(summary.possession_a)}")
        for kind, count in summary.events.items():
            print(f"  {kind}: {count}")
        print("\nMapa de calor do time A (coordenadas do radar):")
        print(render_heatmap(heatmap(conn, match_id, Team.A)))
        if args.export:
            for path in export(conn, match_id, args.out, args.export):
                print(f"Exportado: {path}")


if __name__ == "__main__":
    main()
