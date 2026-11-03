import itertools
import time
from pathlib import Path

import duckdb
import pytest

from src.models import Team
from src.simulation import RadarSimulator
from src.storage import TelemetryWriter
from src.storage.post_match_reports import export, heatmap, latest_match, open_db, summarize
from src.tactics import analyze, detect_events


@pytest.fixture(scope="module")
def recorded_match(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    """~5 min de partida simulada a 60 FPS: 18 mil frames, mais de 400 mil linhas."""
    path = tmp_path_factory.mktemp("db") / "telemetry.duckdb"
    frames = 18_000
    with TelemetryWriter(path, match_id="sim") as writer:
        for frame in itertools.islice(RadarSimulator(), frames):
            if frame.frame_id % 6 == 0:  # análise completa só em parte dos frames: teste rápido
                snapshot = analyze(frame)
                writer.submit(frame, snapshot, detect_events(frame, snapshot))
            else:
                writer.submit(frame)
    return path, frames


def test_every_frame_and_entity_is_stored(recorded_match: tuple[Path, int]) -> None:
    path, frames = recorded_match
    with open_db(path) as conn:
        assert conn.execute("SELECT count(*) FROM frames").fetchone() == (frames,)
        assert conn.execute("SELECT count(*) FROM telemetry").fetchone() == (frames * 23,)
        assert latest_match(conn) == "sim"


def test_summary_and_heatmap(recorded_match: tuple[Path, int]) -> None:
    path, frames = recorded_match
    with open_db(path) as conn:
        summary = summarize(conn, "sim")
        grid = heatmap(conn, "sim", Team.A)
    assert summary.frames == frames
    assert summary.duration_s == pytest.approx((frames - 1) / 60)
    assert 0.3 < (summary.control_a or 0) < 0.7
    assert summary.events  # o simulador gera alertas
    assert grid.sum() == frames * 11


def test_queries_fit_the_budget(recorded_match: tuple[Path, int]) -> None:
    path, _ = recorded_match
    with open_db(path) as conn:
        for query in (lambda: summarize(conn, "sim"), lambda: heatmap(conn, "sim", Team.B)):
            query()  # aquece o cache
            start = time.perf_counter()
            query()
            assert (time.perf_counter() - start) * 1000 < 50


def test_export_parquet_and_csv(recorded_match: tuple[Path, int], tmp_path: Path) -> None:
    path, frames = recorded_match
    with open_db(path) as conn:
        parquet = export(conn, "sim", tmp_path, "parquet")
        csv = export(conn, "sim", tmp_path, "csv")
    assert [p.name for p in parquet] == [
        "sim_frames.parquet",
        "sim_telemetry.parquet",
        "sim_events.parquet",
    ]
    assert duckdb.execute("SELECT count(*) FROM read_parquet(?)", [str(parquet[0])]).fetchone() == (
        frames,
    )
    assert all(p.stat().st_size > 0 for p in csv)


def test_writer_flushes_while_running(tmp_path: Path) -> None:
    writer = TelemetryWriter(tmp_path / "t.duckdb", flush_every_s=0.05)
    writer.start()
    for frame in itertools.islice(RadarSimulator(), 10):
        writer.submit(frame)
    time.sleep(0.3)
    flushed_before_close = writer.rows_written
    writer.close()
    assert flushed_before_close == 10 * 24  # 10 frames + 230 posições
