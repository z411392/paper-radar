import sqlite3
from collections.abc import Callable

from libs.research_workflow.dtos.operational_health import ExplanationHealthEvidence
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
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
                "SELECT currency,"
                "SUM(CASE WHEN state IN ('reserved','unknown') THEN reserved_micros ELSE 0 END) "
                "AS reserved_micros,"
                "SUM(CASE WHEN state='settled' THEN actual_micros ELSE 0 END) "
                "AS settled_actual_micros,"
                "SUM(CASE WHEN state='unknown' THEN 1 ELSE 0 END) "
                "AS unknown_cost_reservations "
                "FROM usage_reservations GROUP BY currency ORDER BY currency"
            ).fetchall()
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise OperationalHealthError("explanation_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        if len(rows) > 1:
            raise OperationalHealthError("multiple_health_currencies")
        if not rows:
            return ExplanationHealthEvidence(
                qa_rejected=qa_rejected,
                currency=None,
                reserved_micros=0,
                settled_actual_micros=0,
                unknown_cost_reservations=0,
            )

        row = rows[0]
        values = (
            qa_rejected,
            row["reserved_micros"],
            row["settled_actual_micros"],
            row["unknown_cost_reservations"],
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise OperationalHealthError("explanation_health_corrupt")
        return ExplanationHealthEvidence(
            qa_rejected=qa_rejected,
            currency=row["currency"],
            reserved_micros=row["reserved_micros"],
            settled_actual_micros=row["settled_actual_micros"],
            unknown_cost_reservations=row["unknown_cost_reservations"],
        )
