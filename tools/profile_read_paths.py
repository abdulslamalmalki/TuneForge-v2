"""Measure representative read-path latency and SQL counts without changing data."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from sqlalchemy import event, select

from app.db.models import CanonicalSetting, Setting
from app.db.session import SessionLocal, engine
from app.domain import admin_dashboard, interface_lookup, library


@dataclass(frozen=True)
class ProfileResult:
    operation: str
    iterations: int
    average_ms: float
    minimum_ms: float
    maximum_ms: float
    average_sql_statements: float
    maximum_sql_statements: int


def profile_operation(
    operation: str,
    callback: Callable[[], object],
    *,
    iterations: int,
) -> ProfileResult:
    durations: list[float] = []
    statements: list[int] = []
    current_count = 0

    def count_statement(*_args) -> None:
        nonlocal current_count
        current_count += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        callback()  # warm caches and imports; not included in measurements
        for _ in range(iterations):
            current_count = 0
            started = time.perf_counter()
            callback()
            durations.append((time.perf_counter() - started) * 1000)
            statements.append(current_count)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
    return ProfileResult(
        operation=operation,
        iterations=iterations,
        average_ms=round(sum(durations) / len(durations), 3),
        minimum_ms=round(min(durations), 3),
        maximum_ms=round(max(durations), 3),
        average_sql_statements=round(sum(statements) / len(statements), 1),
        maximum_sql_statements=max(statements),
    )


def _with_session(callback: Callable[[object], object]) -> Callable[[], object]:
    def run() -> object:
        with SessionLocal() as session:
            return callback(session)

    return run


def build_profile(iterations: int) -> list[ProfileResult]:
    with SessionLocal() as session:
        canonical_id = session.scalar(
            select(CanonicalSetting.canonical_id)
            .where(CanonicalSetting.status == "active")
            .order_by(CanonicalSetting.canonical_id)
            .limit(1)
        )
        source_id = session.scalar(
            select(Setting.id)
            .where(Setting.status == "approved")
            .order_by(Setting.id)
            .limit(1)
        )
    if canonical_id is None or source_id is None:
        raise RuntimeError("Profiling requires at least one public setting and canonical identity.")

    operations = (
        (
            "library_source_browse",
            _with_session(lambda session: library.browse(session, view="source", page=1)),
        ),
        (
            "library_source_search",
            _with_session(
                lambda session: library.browse(session, view="source", query="PBO", page=1)
            ),
        ),
        (
            "library_canonical_browse",
            _with_session(lambda session: library.browse(session, view="canonical", page=1)),
        ),
        (
            "library_canonical_search",
            _with_session(
                lambda session: library.browse(
                    session, view="canonical", query="PBO", page=1
                )
            ),
        ),
        (
            "library_canonical_detail",
            _with_session(lambda session: library.canonical_detail(session, canonical_id)),
        ),
        (
            "library_source_detail",
            _with_session(lambda session: library.source_detail(session, source_id)),
        ),
        (
            "ask_verified_setting",
            _with_session(
                lambda session: interface_lookup.build_presentation(
                    session, "Precision Boost Overdrive"
                )
            ),
        ),
        ("admin_dashboard", _with_session(admin_dashboard.build_dashboard)),
    )
    return [
        profile_operation(name, callback, iterations=iterations)
        for name, callback in operations
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100:
        parser.error("--iterations must be between 1 and 100")
    print(json.dumps([asdict(row) for row in build_profile(args.iterations)], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
