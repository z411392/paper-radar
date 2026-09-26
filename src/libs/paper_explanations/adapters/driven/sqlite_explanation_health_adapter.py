import sqlite3
from collections.abc import Callable

from libs.paper_explanations.dtos.explanation_health import (
    ExplanationHealthEvidence,
    UsagePeriodHealthEvidence,
)
from libs.paper_explanations.exceptions.explanation_health_error import (
    ExplanationHealthError,
)


class SqliteExplanationHealthAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def __call__(self) -> ExplanationHealthEvidence:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            qa_rejected = connection.execute(
                "SELECT COUNT(*) FROM summary_revisions WHERE qa_state='rejected'"
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT period_key,currency,"
                "SUM(CASE WHEN state IN ('reserved','unknown') THEN reserved_micros ELSE 0 END) "
                "AS reserved_micros,"
                "SUM(CASE WHEN state='settled' THEN actual_micros ELSE 0 END) "
                "AS settled_actual_micros,"
                "SUM(CASE WHEN state='unknown' THEN 1 ELSE 0 END) "
                "AS unknown_cost_reservations "
                "FROM usage_reservations "
                "GROUP BY period_key,currency ORDER BY period_key,currency"
            ).fetchall()
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise ExplanationHealthError("explanation_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        periods = []
        for row in rows:
            values = (
                row["reserved_micros"],
                row["settled_actual_micros"],
                row["unknown_cost_reservations"],
            )
            if (
                not isinstance(row["period_key"], str)
                or not row["period_key"]
                or not isinstance(row["currency"], str)
                or not row["currency"]
                or any(type(value) is not int or value < 0 for value in values)
            ):
                raise ExplanationHealthError("explanation_health_corrupt")
            periods.append(
                UsagePeriodHealthEvidence(
                    period_key=row["period_key"],
                    currency=row["currency"],
                    reserved_micros=row["reserved_micros"],
                    settled_actual_micros=row["settled_actual_micros"],
                    unknown_cost_reservations=row["unknown_cost_reservations"],
                )
            )
        if type(qa_rejected) is not int or qa_rejected < 0:
            raise ExplanationHealthError("explanation_health_corrupt")
        return ExplanationHealthEvidence(
            qa_rejected=qa_rejected,
            usage_periods=tuple(periods),
        )
