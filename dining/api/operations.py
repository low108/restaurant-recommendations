"""Restricted, structural operations summaries; raw profiles/prompts are never exported."""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from dining.core.store import decode
from dining.meals.exposure import get_exposure_metrics
from dining.meals.generation import job_view, result_metadata
from dining.meals.outcomes import compute_outcome_metrics


def public_run(meal):
    """Structural summary of a meal's latest recommendation run (no private inputs)."""
    result = decode(meal["result"], {})
    metadata = result_metadata(result)
    return {
        **metadata,
        "session_ref": meal["id"],
        "context_version": meal["revision"],
        "status": metadata["result_status"],
        "eligible_count": metadata["option_count"],
    }


def operations_router(store, generation_worker=None, inference_settings=None):
    """Token-protected operations routes: run summaries and outcome metrics."""
    router = APIRouter(prefix="/api/operations")

    @router.get("/summary")
    def summary(x_operations_token: str = Header(default="")):
        key = os.getenv("DINING_OPERATIONS_TOKEN", "")
        if len(key) < 32:
            raise HTTPException(503, "Operations access is not configured")
        if not hmac.compare_digest(key, x_operations_token):
            raise HTTPException(403, "Operations access denied")
        with store.transaction() as db:
            states = {
                r["status"]: r["count"]
                for r in db.execute(
                    "SELECT status,COUNT(*) AS count FROM meals GROUP BY status"
                )
            }
            jobs = {
                r["status"]: r["count"]
                for r in db.execute(
                    "SELECT status,COUNT(*) AS count FROM notification_jobs GROUP BY status"
                )
            }
            generation_jobs = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status,COUNT(*) AS count FROM generation_jobs GROUP BY status"
                )
            }
            generation_history = [
                {**job_view(db, row), "session_ref": row["meal_id"]}
                for row in db.execute(
                    "SELECT * FROM generation_jobs ORDER BY created_at DESC,id DESC LIMIT 50"
                ).fetchall()
            ]
            runs = [
                public_run(row)
                for row in db.execute(
                    "SELECT id,revision,result FROM meals WHERE result IS NOT NULL ORDER BY created_at DESC LIMIT 100"
                )
            ]
            data_error_reports = [
                {
                    "id": row["id"],
                    "meal_id": row["meal_id"],
                    "outlet_id": row["outlet_id"],
                    "category": row["category"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in db.execute(
                    "SELECT id,meal_id,outlet_id,category,status,created_at FROM data_error_reports ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
            ]
            recommendation_archives_count = db.execute(
                "SELECT COUNT(*) FROM recommendation_archives"
            ).fetchone()[0]
            has_email_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_email_outbox'"
            ).fetchone()
            email_delivery_health = None
            if has_email_table:
                total_email = db.execute(
                    "SELECT COUNT(*) FROM account_email_outbox"
                ).fetchone()[0]
                sent_email = db.execute(
                    "SELECT COUNT(*) FROM account_email_outbox WHERE status='sent'"
                ).fetchone()[0]
                pending_email = db.execute(
                    "SELECT COUNT(*) FROM account_email_outbox WHERE status='pending'"
                ).fetchone()[0]
                failed_email = db.execute(
                    "SELECT COUNT(*) FROM account_email_outbox WHERE status='failed'"
                ).fetchone()[0]
                email_delivery_health = {
                    "total_jobs": total_email,
                    "sent_count": sent_email,
                    "pending_count": pending_email,
                    "failed_count": failed_email,
                }
        total_in = sum(
            r.get("input_tokens") or 0
            for r in runs
            if r.get("input_tokens") is not None
        )
        total_out = sum(
            r.get("output_tokens") or 0
            for r in runs
            if r.get("output_tokens") is not None
        )
        total_tok = sum(
            r.get("total_tokens") or 0
            for r in runs
            if r.get("total_tokens") is not None
        )
        total_cost = round(
            sum(
                r.get("estimated_cost") or 0.0
                for r in runs
                if r.get("estimated_cost") is not None
            ),
            8,
        )
        currencies = sorted(
            {r.get("currency") for r in runs if r.get("currency") is not None}
        )
        cost_summary = {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_tok,
            "total_estimated_cost": total_cost,
            "currencies": currencies,
        }
        return {
            "inference": inference_settings.public()
            if inference_settings
            else {"configuration_status": "unknown"},
            "meal_states": states,
            "inbox_jobs": jobs,
            "recent_runs": runs,
            "cost_summary": cost_summary,
            "exposure_metrics": get_exposure_metrics(db),
            "outcomes": compute_outcome_metrics(db, min_cohort_size=5),
            "data_error_reports": data_error_reports,
            "recommendation_archives_count": recommendation_archives_count,
            "email_delivery_health": email_delivery_health,
            "generation_jobs": generation_jobs,
            "generation_worker": generation_worker.health()
            if generation_worker
            else {"status": "unavailable"},
            "recent_generation_jobs": generation_history,
            "trace_export": "disabled",
            "projection": "structural_only",
            "scope": "Current results and the latest 50 durable generation jobs with sanitized attempt receipts. Counts are not the PRD outcome metrics.",
        }

    @router.get("/outcomes")
    def outcomes(
        x_operations_token: str = Header(default=""),
        exclude_demo: bool = False,
    ):
        key = os.getenv("DINING_OPERATIONS_TOKEN", "")
        if len(key) < 32:
            raise HTTPException(503, "Operations access is not configured")
        if not hmac.compare_digest(key, x_operations_token):
            raise HTTPException(403, "Operations access denied")
        with store.transaction() as db:
            return compute_outcome_metrics(db, exclude_demo=exclude_demo)

    return router
