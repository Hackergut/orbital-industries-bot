"""
FastAPI endpoints for Temporal workflow management
"""
import asyncio
import os
import uuid
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

router = APIRouter(prefix="/temporal", tags=["temporal"])

# Temporal connection - respects TEMPORAL_HOST env var, works inside/outside Docker
def _get_temporal_host() -> str:
    return os.getenv("TEMPORAL_HOST", "host.docker.internal:7233")


class StartBatchWorkflowRequest(BaseModel):
    limit_targets: int = 500
    batch_size: int = 100


class StartSingleTargetRequest(BaseModel):
    target_id: int
    url: str


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str
    message: str


@router.post("/workflows/batch", response_model=Dict[str, Any])
async def start_batch_workflow(request: StartBatchWorkflowRequest):
    """
    Start a batch processing workflow.
    Returns workflow ID for monitoring.
    
    Example:
    ```
    POST /temporal/workflows/batch
    {
        "limit_targets": 500,
        "batch_size": 100
    }
    ```
    """
    try:
        from temporalio.client import Client
        from pipeline_workflows import BatchProcessTargetsWorkflow
        
        client = await Client.connect(_get_temporal_host())
        
        workflow_id = f"batch-pipeline-{uuid.uuid4().hex[:8]}"
        
        handle = await client.start_workflow(
            BatchProcessTargetsWorkflow,
            args=[request.batch_size, request.limit_targets],
            id=workflow_id,
            task_queue="orbital-pipeline",
        )
        
        return {
            "workflow_id": workflow_id,
            "status": "started",
            "message": f"Batch workflow started with ID {workflow_id}",
            "ui_url": f"http://localhost:8233/namespaces/default/workflows/{workflow_id}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start workflow: {str(e)}")


@router.post("/workflows/single", response_model=Dict[str, Any])
async def start_single_target_workflow(request: StartSingleTargetRequest):
    """
    Start a single target processing workflow.
    Returns workflow ID for monitoring.
    
    Example:
    ```
    POST /temporal/workflows/single
    {
        "target_id": 1,
        "url": "https://example.com"
    }
    ```
    """
    try:
        from temporalio.client import Client
        from pipeline_workflows import ProcessSingleTargetWorkflow
        from app.config import Config
        
        client = await Client.connect(_get_temporal_host())
        
        workflow_id = f"target-{request.target_id}-{uuid.uuid4().hex[:8]}"
        
        handle = await client.start_workflow(
            ProcessSingleTargetWorkflow,
            args=[request.target_id, request.url, Config.COMPANY_DATA],
            id=workflow_id,
            task_queue="orbital-pipeline",
        )
        
        return {
            "workflow_id": workflow_id,
            "target_id": request.target_id,
            "url": request.url,
            "status": "started",
            "message": f"Single target workflow started with ID {workflow_id}",
            "ui_url": f"http://localhost:8233/namespaces/default/workflows/{workflow_id}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start workflow: {str(e)}")


@router.get("/workflows/{workflow_id}", response_model=Dict[str, Any])
async def get_workflow_status(workflow_id: str):
    """
    Get status of a workflow execution.
    Returns result if complete, status if running.
    
    Example:
    ```
    GET /temporal/workflows/batch-pipeline-12345678
    ```
    """
    try:
        from temporalio.client import Client
        
        client = await Client.connect(_get_temporal_host())
        
        handle = client.get_workflow_handle(workflow_id)
        
        try:
            result = await asyncio.wait_for(handle.result(), timeout=0.1)
            return {
                "workflow_id": workflow_id,
                "status": "completed",
                "result": result,
                "ui_url": f"http://localhost:8233/namespaces/default/workflows/{workflow_id}",
            }
        except asyncio.TimeoutError:
            # Workflow still running
            return {
                "workflow_id": workflow_id,
                "status": "running",
                "message": "Workflow is still executing",
                "ui_url": f"http://localhost:8233/namespaces/default/workflows/{workflow_id}",
            }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {str(e)}")


@router.get("/ui")
async def temporal_ui_redirect():
    """
    Redirect to Temporal Web UI.
    """
    return {
        "message": "Temporal Web UI available at:",
        "url": "http://localhost:8233",
        "workflows": [
            "ProcessSingleTargetWorkflow - Process individual targets",
            "BatchProcessTargetsWorkflow - Batch process targets in parallel",
            "ScheduledPipelineWorkflow - Scheduled batch processing",
        ],
    }


@router.get("/health")
async def temporal_health():
    """
    Check Temporal Server health.
    """
    try:
        from temporalio.client import Client
        client = await Client.connect(_get_temporal_host())
        return {"status": "healthy", "message": "Temporal Server is running"}
    except Exception as e:
        return {"status": "unhealthy", "message": str(e)}, 503
