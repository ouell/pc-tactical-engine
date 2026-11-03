"""Gravação da partida no DuckDB, em lotes e fora da thread do motor."""

import logging
import queue
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import duckdb

from config.settings import DATA_DIR
from src.models import FrameTelemetry, TacticalEvent, TacticalSnapshot, Team

log = logging.getLogger(__name__)

DB_FILE = DATA_DIR / "telemetry.duckdb"
BALL_ID = -1

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id VARCHAR PRIMARY KEY, started_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS frames (
    match_id VARCHAR, tick_id BIGINT, ts DOUBLE, state VARCHAR, minute SMALLINT,
    score_a SMALLINT, score_b SMALLINT, possession VARCHAR,
    control_a DOUBLE, depth_a_m DOUBLE, cb_gap_a_m DOUBLE
);
CREATE TABLE IF NOT EXISTS telemetry (
    match_id VARCHAR, tick_id BIGINT, ts DOUBLE, entity_id SMALLINT, team VARCHAR,
    x DOUBLE, y DOUBLE, vx DOUBLE, vy DOUBLE
);
CREATE TABLE IF NOT EXISTS events (
    match_id VARCHAR, tick_id BIGINT, ts DOUBLE, type VARCHAR, value DOUBLE, target_id SMALLINT
);
"""


class _Columns:
    """Buffer colunar de uma tabela, gravado num único INSERT vetorizado (unnest)."""

    def __init__(self, table: str, *names: str) -> None:
        self.data: dict[str, list[Any]] = {name: [] for name in names}
        selects = ", ".join(f"unnest(${name})" for name in names)
        self.sql = f"INSERT INTO {table} ({', '.join(names)}) SELECT {selects}"

    def append(self, *values: Any) -> None:
        for column, value in zip(self.data.values(), values, strict=True):
            column.append(value)

    def __len__(self) -> int:
        return len(next(iter(self.data.values())))

    def insert(self, conn: duckdb.DuckDBPyConnection) -> int:
        rows = len(self)
        if rows:
            conn.execute(self.sql, self.data)
        return rows

    def clear(self) -> None:
        for column in self.data.values():
            column.clear()


@dataclass(frozen=True, slots=True)
class _Record:
    frame: FrameTelemetry
    snapshot: TacticalSnapshot | None
    events: tuple[TacticalEvent, ...]


class TelemetryWriter:
    """`submit` só enfileira; uma thread grava tudo a cada `flush_every_s`.

    A memória fica limitada ao que chega entre dois flushes, então uma partida inteira
    não acumula nada em RAM.
    """

    def __init__(
        self, path: Path = DB_FILE, match_id: str | None = None, flush_every_s: float = 1.0
    ) -> None:
        self.path = path
        self.match_id = match_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.flush_every_s = flush_every_s
        self.rows_written = 0
        self._queue: queue.SimpleQueue[_Record | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="telemetry-db", daemon=True)
        self._frames = _Columns(
            "frames", "match_id", "tick_id", "ts", "state", "minute", "score_a", "score_b",
            "possession", "control_a", "depth_a_m", "cb_gap_a_m",
        )  # fmt: skip
        self._telemetry = _Columns(
            "telemetry", "match_id", "tick_id", "ts", "entity_id", "team", "x", "y", "vx", "vy"
        )
        self._events = _Columns("events", "match_id", "tick_id", "ts", "type", "value", "target_id")

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread.start()

    def submit(
        self,
        frame: FrameTelemetry,
        snapshot: TacticalSnapshot | None = None,
        events: Iterable[TacticalEvent] = (),
    ) -> None:
        self._queue.put(_Record(frame, snapshot, tuple(events)))

    def close(self) -> None:
        """Grava o que falta e fecha o banco."""
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        with duckdb.connect(str(self.path)) as conn:
            conn.execute(SCHEMA)
            conn.execute(
                "INSERT INTO matches (match_id) VALUES (?) ON CONFLICT DO NOTHING",
                [self.match_id],
            )
            next_flush = time.monotonic() + self.flush_every_s
            while True:
                try:
                    record = self._queue.get(timeout=max(next_flush - time.monotonic(), 0))
                except queue.Empty:
                    pass
                else:
                    if record is None:
                        break
                    self._buffer(record)
                if time.monotonic() >= next_flush:
                    self._flush(conn)
                    next_flush = time.monotonic() + self.flush_every_s
            self._flush(conn)

    def _flush(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Um lote = uma transação. Se falhar, o buffer fica para a próxima tentativa."""
        tables = (self._frames, self._telemetry, self._events)
        try:
            conn.begin()
            written = sum(table.insert(conn) for table in tables)
            conn.commit()
        except duckdb.Error:
            conn.rollback()
            log.exception("Falha ao gravar lote de telemetria; tento de novo no próximo flush")
            return
        for table in tables:
            table.clear()
        self.rows_written += written

    def _buffer(self, record: _Record) -> None:
        frame, snapshot, match = record.frame, record.snapshot, self.match_id
        tick, ts = frame.frame_id, frame.timestamp
        score_a, score_b = frame.score or (None, None)
        possession: Team | None = None
        control_a = depth_m = cb_gap_m = None
        if snapshot:
            shape = snapshot.shapes[Team.A]
            possession, control_a = snapshot.possession, snapshot.control_a
            depth_m, cb_gap_m = shape.depth_m, shape.cb_gap_m
        self._frames.append(
            match, tick, ts, frame.state, frame.minute, score_a, score_b,
            possession, control_a, depth_m, cb_gap_m,
        )  # fmt: skip
        for p in frame.players:
            self._telemetry.append(match, tick, ts, p.id, p.team, p.x, p.y, p.vx, p.vy)
        if frame.ball:
            self._telemetry.append(match, tick, ts, BALL_ID, None, *frame.ball, None, None)
        for e in record.events:
            self._events.append(match, tick, ts, e.type, e.value, e.target_id)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
