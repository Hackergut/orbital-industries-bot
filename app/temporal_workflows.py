"""Temporal Workflows for Orbital Pipeline — sandbox-safe imports."""
import asyncio
import logging
from datetime import timedelta
from typing import Dict, Any, List

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from config_safe import Config
    from app.temporal_activities import (
        discover_targets_activity,
        analyze_target_activity,
        generate_target_summary_activity,
        detect_and_fill_form_activity,
        save_leads_activity,
        update_target_status_activity,
    )

logger = logging.getLogger(__name__)


@workflow.defn
class ProcessSingleTargetWorkflow:
    """Process a single target: analyze, summarize, fill form, save leads."""

    @workflow.run
    async def run(self, target_id: int, url: str, company_data: Dict[str, Any]) -> Dict[str, Any]:
        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
            maximum_attempts=3,
        )

        result = {"target_id": target_id, "url": url, "status": "started", "steps_completed": []}

        try:
            # Step 1: Analyze
            analysis = await workflow.execute_activity(
                analyze_target_activity,
                target_id,
                url,
                start_to_close_timeout=timedelta(seconds=45),
                retry_policy=retry_policy,
            )
            result["steps_completed"].append("analyze")
            result["analysis"] = analysis

            if not analysis["has_form"]:
                await workflow.execute_activity(
                    update_target_status_activity,
                    target_id,
                    "no_form",
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_policy,
                )
                result["status"] = "no_form"
                return result

            # Step 2: Summary
            summary = await workflow.execute_activity(
                generate_target_summary_activity,
                url,
                analysis["html"],
                company_data,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
            result["steps_completed"].append("summary")
            result["summary"] = summary

            # Step 3: Fill form
            form_result = await workflow.execute_activity(
                detect_and_fill_form_activity,
                target_id,
                url,
                company_data,
                summary,
                analysis["has_captcha"],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=retry_policy,
            )
            result["steps_completed"].append("form_fill")
            result["form_result"] = form_result

            # Step 4: Save leads
            if analysis.get("emails"):
                await workflow.execute_activity(
                    save_leads_activity,
                    target_id,
                    analysis["emails"],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_policy,
                )
                result["steps_completed"].append("leads")

            # Update status
            await workflow.execute_activity(
                update_target_status_activity,
                target_id,
                form_result["status"],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )
            result["status"] = form_result["status"]
            return result

        except Exception as e:
            logger.error("[Workflow] Error processing %s: %s", url, e)
            try:
                await workflow.execute_activity(
                    update_target_status_activity,
                    target_id,
                    "error",
                    str(e),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_policy,
                )
            except Exception:
                pass
            result["status"] = "error"
            result["error"] = str(e)
            raise


@workflow.defn
class BatchProcessTargetsWorkflow:
    """Batch process multiple targets in parallel."""

    @workflow.run
    async def run(self, batch_size: int = 100, limit_targets: int = 500) -> Dict[str, Any]:
        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
            maximum_attempts=3,
        )

        targets = await workflow.execute_activity(
            discover_targets_activity,
            limit_targets,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=retry_policy,
        )

        if not targets:
            return {"batch_size": batch_size, "total_targets": 0, "completed": 0, "failed": 0, "status": "no_targets"}

        company_data = Config.COMPANY_DATA

        futures = []
        for target in targets[:batch_size]:
            future = workflow.execute_child_workflow(
                ProcessSingleTargetWorkflow,
                target["id"],
                target["url"],
                company_data,
                id=f"process_target_{target['id']}",
                retry_policy=retry_policy,
            )
            futures.append((target["id"], future))

        completed = 0
        failed = 0
        results = []

        for target_id, future in futures:
            try:
                result = await future
                results.append(result)
                if result.get("status") == "submitted":
                    completed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.error("[BatchWorkflow] Target %d failed: %s", target_id, e)
                results.append({"target_id": target_id, "status": "error", "error": str(e)})

        return {
            "batch_size": batch_size,
            "total_targets": len(futures),
            "completed": completed,
            "failed": failed,
            "status": "completed",
            "results": results,
        }


@workflow.defn
class ScheduledPipelineWorkflow:
    """Scheduled workflow that runs batch processing at regular intervals."""

    @workflow.run
    async def run(self) -> str:
        try:
            result = await workflow.execute_child_workflow(
                BatchProcessTargetsWorkflow,
                batch_size=getattr(Config, "PIPELINE_BATCH_SIZE", 100),
                limit_targets=getattr(Config, "PIPELINE_TARGET_DAILY", 500),
                id="batch_process_scheduled",
            )
            return f"Batch completed: {result['completed']} submitted, {result['failed']} failed"
        except Exception as e:
            logger.error("[ScheduledWorkflow] Batch failed: %s", e)
            raise
