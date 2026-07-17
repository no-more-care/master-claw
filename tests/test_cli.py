import sqlite3

import pytest

import masterclaw.cli as cli
from masterclaw.cli import build_parser, main
from masterclaw.config import ModelRole
from masterclaw.storage.sqlite import SCHEMA_VERSION, SQLiteStore


def test_cli_parser_accepts_explicit_benchmark_transports() -> None:
    args = build_parser().parse_args(
        [
            "model-benchmark",
            "--output",
            "report.json",
            "--transports",
            "prompt_json",
            "native_tool",
            "--repeats",
            "3",
            "--config-mode",
            "fixed",
            "--suite",
            "worldgen",
        ]
    )
    assert args.command == "model-benchmark"
    assert args.transports == ["prompt_json", "native_tool"]
    assert args.repeats == 3
    assert args.config_mode == "fixed"
    assert args.suite == "worldgen"


def test_cli_initializes_and_backs_up_database(tmp_path) -> None:
    database = tmp_path / "live.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    assert main(["init-db", "--database", str(database)]) == 0
    assert main(["backup", "--database", str(database), "--output", str(backup)]) == 0
    assert database.exists()
    assert backup.exists()
    SQLiteStore(backup).initialize()


def test_backup_copies_existing_database_without_initializing_it(tmp_path, monkeypatch) -> None:
    database = tmp_path / "legacy-live.sqlite3"
    output_dir = tmp_path / "generations"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('before-migration')")

    def forbidden_initialize(_store) -> None:
        raise AssertionError("backup must not initialize or migrate the live database")

    monkeypatch.setattr(SQLiteStore, "initialize", forbidden_initialize)
    assert (
        main(
            [
                "backup",
                "--database",
                str(database),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    generations = list(output_dir.glob("masterclaw-*.sqlite3"))
    assert len(generations) == 1
    with sqlite3.connect(generations[0]) as connection:
        assert connection.execute("SELECT value FROM legacy_marker").fetchone() == (
            "before-migration",
        )


def test_healthcheck_opens_database_read_only(tmp_path) -> None:
    database = tmp_path / "live.sqlite3"
    SQLiteStore(database).initialize()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    before = database.read_bytes()

    assert main(["healthcheck", "--database", str(database)]) == 0

    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
        }
    assert {"schema_version", "inbox_messages", "outbox_messages", "marker"} <= tables


def test_healthcheck_does_not_create_a_missing_database(tmp_path) -> None:
    database = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit, match="does not exist"):
        main(["healthcheck", "--database", str(database)])
    assert not database.exists()


def test_healthcheck_rejects_incomplete_or_unsupported_application_schema(tmp_path) -> None:
    incomplete = tmp_path / "incomplete.sqlite3"
    with sqlite3.connect(incomplete) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
    with pytest.raises(SystemExit, match="required application tables are missing"):
        main(["healthcheck", "--database", str(incomplete)])

    unsupported = tmp_path / "unsupported.sqlite3"
    SQLiteStore(unsupported).initialize()
    with sqlite3.connect(unsupported) as connection:
        connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION + 1,))
    before = unsupported.read_bytes()
    with pytest.raises(SystemExit, match=f"expected {SCHEMA_VERSION}"):
        main(["healthcheck", "--database", str(unsupported)])
    assert unsupported.read_bytes() == before


def test_service_completion_uses_production_retry_schedule(monkeypatch) -> None:
    captured = {}
    sentinel = object()

    def fake_port(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(cli, "OpenHandsCompletionPort", fake_port)
    result = cli._service_completion(object(), ModelRole.REASONING, object())

    assert result is sentinel
    assert captured["kwargs"]["retry_delays"] == (5.0, 30.0)
