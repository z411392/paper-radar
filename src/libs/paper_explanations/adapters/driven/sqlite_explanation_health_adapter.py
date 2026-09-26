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

    @staticmethod
    def _usage_rows(connection: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
        return tuple(
            connection.execute(
                "SELECT u.id,u.period_key,u.currency,u.reserved_micros,"
                "u.actual_micros,u.state AS reservation_state,"
                "m.id AS model_run_id,m.actual_cost_micros AS run_actual_micros "
                "FROM usage_reservations u "
                "LEFT JOIN model_runs m ON m.id=u.run_id "
                "ORDER BY u.period_key,u.currency,u.id"
            ).fetchall()
        )

    @staticmethod
    def _reconcile_usage(
        rows: tuple[sqlite3.Row, ...],
    ) -> tuple[UsagePeriodHealthEvidence, ...]:
        groups: dict[tuple[str, str], list[int]] = {}
        for row in rows:
            period_key = row["period_key"]
            currency = row["currency"]
            reserved = row["reserved_micros"]
            state = row["reservation_state"]
            actual = row["actual_micros"]
            run_actual = row["run_actual_micros"]
            if (
                not isinstance(period_key, str)
                or not period_key
                or not isinstance(currency, str)
                or not currency
                or type(reserved) is not int
                or reserved < 0
                or row["model_run_id"] is None
            ):
                raise ExplanationHealthError("explanation_health_corrupt")

            if state == "settled":
                if (
                    type(actual) is not int
                    or actual < 0
                    or type(run_actual) is not int
                    or run_actual != actual
                ):
                    raise ExplanationHealthError(
                        "explanation_health_ledger_mismatch"
                    )
            elif state in {"reserved", "unknown"}:
                if actual is not None or run_actual is not None:
                    raise ExplanationHealthError(
                        "explanation_health_ledger_mismatch"
                    )
            elif state == "released":
                if actual is not None or run_actual != 0:
                    raise ExplanationHealthError(
                        "explanation_health_ledger_mismatch"
                    )
            else:
                raise ExplanationHealthError("explanation_health_corrupt")

            counters = groups.setdefault((period_key, currency), [0, 0, 0])
            if state in {"reserved", "unknown"}:
                counters[0] += reserved
            elif state == "settled":
                counters[1] += actual
            if state == "unknown":
                counters[2] += 1
            if any(value >= 2**63 for value in counters):
                raise ExplanationHealthError("explanation_health_corrupt")

        return tuple(
            UsagePeriodHealthEvidence(
                period_key=period_key,
                currency=currency,
                reserved_micros=values[0],
                settled_actual_micros=values[1],
                unknown_cost_reservations=values[2],
            )
            for (period_key, currency), values in sorted(groups.items())
        )

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
            rows = self._usage_rows(connection)
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise ExplanationHealthError("explanation_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        if type(qa_rejected) is not int or qa_rejected < 0:
            raise ExplanationHealthError("explanation_health_corrupt")
        return ExplanationHealthEvidence(
            qa_rejected=qa_rejected,
            usage_periods=self._reconcile_usage(rows),
        )
