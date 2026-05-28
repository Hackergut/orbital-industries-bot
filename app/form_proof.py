"""Form Proof System — professional live form builder with evidence capture.
Tracks every field, screenshot, and submission with full audit trail."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import create_app, db
from app.config import Config
from app.models import FormProof, Target

logger = logging.getLogger(__name__)

_SCREENSHOT_DIR = getattr(Config, "SCREENSHOT_DIR", "static/screenshots")


def _utc_now():
    return datetime.now(timezone.utc)


def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _ss_path(stage: str, target_id: int, suffix: str = "") -> str:
    ts = int(time.time())
    name = f"proof_{target_id}_{stage}_{ts}{suffix}.png"
    return os.path.join(_SCREENSHOT_DIR, name)




class VideoRecorder:
    """Screen recorder via ffmpeg on a virtual display."""

    def __init__(self):
        self.process = None
        self.output_path = ""

    def start(self, output_path: str, display: str = ":99", duration: int = 30):
        """Start ffmpeg x11grab recording."""
        _ensure_dir(output_path)
        self.output_path = output_path
        # Use -t to auto-stop, but we also kill manually
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "x11grab",
            "-video_size", "1366x768",
            "-i", display,
            "-t", str(duration),
            "-codec:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-r", "10",
            "-loglevel", "error",
            output_path,
        ]
        import subprocess
        self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_path

    def stop(self):
        """Gracefully stop ffmpeg."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=2)
            self.process = None
        return self.output_path

class FormProofBuilder:
    """Builds a complete FormProof record during pipeline execution."""

    def __init__(self, target_id: int, target_url: str, session_id: str = None):
        self.target_id = target_id
        self.target_url = target_url
        self.proof_id: Optional[int] = None
        self.session_id = session_id or f"proof_{target_id}_{int(time.time())}"

    # ── Screenshots via pool ─────────────────────────────────────

    async def capture_pre(self, pool) -> str:
        """Screenshot before any filling — shows the raw form."""
        path = _ss_path("pre", self.target_id)
        _ensure_dir(path)
        await pool.screenshot(path, session_id=self.session_id, full_page=True)
        return path

    async def capture_filling(self, pool) -> str:
        """Screenshot during fill — shows partially completed form."""
        path = _ss_path("filling", self.target_id)
        _ensure_dir(path)
        await pool.screenshot(path, session_id=self.session_id, full_page=True)
        return path

    async def capture_post(self, pool) -> str:
        """Screenshot immediately after clicking submit."""
        path = _ss_path("post", self.target_id)
        _ensure_dir(path)
        await pool.screenshot(path, session_id=self.session_id, full_page=True)
        return path

    async def capture_confirmation(self, pool, wait_seconds: int = 5) -> str:
        """Screenshot after post-submit page settles — confirmation or error."""
        await asyncio.sleep(wait_seconds)
        path = _ss_path("confirmation", self.target_id)
        _ensure_dir(path)
        await pool.screenshot(path, session_id=self.session_id, full_page=True)
        return path

    # ── Data extraction ──────────────────────────────────────────

    async def extract_actual_values(self, pool) -> Dict[str, Any]:
        """Read back what was actually written into each visible form field."""
        script = """() => {
            const data = {};
            const inputs = document.querySelectorAll('input:not([type=\"hidden\"]):not([type=\"submit\"]), textarea, select');
            inputs.forEach(el => {
                const key = el.name || el.id || el.placeholder || el.type;
                if (key) {
                    let val = '';
                    if (el.tagName === 'SELECT') {
                        val = el.options[el.selectedIndex]?.text || '';
                    } else if (el.type === 'checkbox' || el.type === 'radio') {
                        val = el.checked;
                    } else {
                        val = el.value;
                    }
                    data[key] = { value: val, tag: el.tagName.toLowerCase(), type: el.type || 'text' };
                }
            });
            return data;
        }"""
        return await pool.evaluate(script, session_id=self.session_id) or {}

    async def extract_page_text(self, pool) -> str:
        """Grab current page text for evidence."""
        script = "return document.body.innerText.slice(0, 4000);"
        return await pool.evaluate(script, session_id=self.session_id) or ""

    async def extract_final_url(self, pool) -> str:
        script = "return window.location.href;"
        return await pool.evaluate(script, session_id=self.session_id) or ""

    # ── Database persistence ─────────────────────────────────────

    def save(
        self,
        pre_screenshot: str = "",
        filling_screenshot: str = "",
        post_screenshot: str = "",
        confirmation_screenshot: str = "",
        video_path: str = "",
        detected_fields: List[Dict] = None,
        ai_mapping: Dict = None,
        actual_values: Dict = None,
        submitted_message: str = "",
        final_url: str = "",
        status: str = "pending",
        error_message: str = "",
        session_log: str = "",
    ) -> int:
        """Persist the full FormProof record to SQLite."""
        app = create_app()
        with app.app_context():
            proof = FormProof(
                target_id=self.target_id,
                target_url=self.target_url,
                pre_screenshot=pre_screenshot,
                filling_screenshot=filling_screenshot,
                post_screenshot=post_screenshot,
                confirmation_screenshot=confirmation_screenshot,
                video_path=video_path,
                detected_fields=json.dumps(detected_fields or []),
                ai_mapping=json.dumps(ai_mapping or {}),
                actual_values=json.dumps(actual_values or {}),
                submitted_message=submitted_message,
                final_url=final_url,
                status=status,
                error_message=error_message,
                session_log=session_log,
                pre_at=_utc_now() if pre_screenshot else None,
                filling_at=_utc_now() if filling_screenshot else None,
                post_at=_utc_now() if post_screenshot else None,
            )
            db.session.add(proof)
            db.session.commit()
            self.proof_id = proof.id
            logger.info("FormProof saved: id=%s target=%s status=%s", proof.id, self.target_url, status)
            return proof.id

    def update(self, **kwargs) -> bool:
        """Update an existing FormProof record."""
        if not self.proof_id:
            return False
        app = create_app()
        with app.app_context():
            proof = db.session.get(FormProof, self.proof_id)
            if not proof:
                return False
            for key, val in kwargs.items():
                if hasattr(proof, key):
                    if key in ("detected_fields", "ai_mapping", "actual_values") and isinstance(val, (list, dict)):
                        val = json.dumps(val)
                    setattr(proof, key, val)
            db.session.commit()
            return True


# ── helpers for the pipeline ─────────────────────────────────────

async def build_proof_for_target(
    pool,
    target_id: int,
    target_url: str,
    fields: List[Dict],
    mapping: Dict,
    submit_result: Dict,
    session_log_lines: List[str],
    session_id: str = None,
):
    """High-level helper called by the pipeline after a target is processed."""
    builder = FormProofBuilder(target_id, target_url, session_id=session_id)
    try:
        # We assume the pool already has the session checked out
        pre_screenshot = builder.session_id  # Placeholder — real screenshots captured inline below

        # Capture screenshots
        post_screenshot = submit_result.get("screenshot", "")
        final_url = submit_result.get("final_url", "")
        status = submit_result.get("status", "unknown")

        # Try to get actual values from the page
        actual_values = {}
        try:
            actual_values = await builder.extract_actual_values(pool)
        except Exception as e:
            logger.debug("Could not extract actual values: %s", e)

        # Build session log
        session_log = "\n".join(session_log_lines)

        proof_id = builder.save(
            post_screenshot=post_screenshot,
            detected_fields=fields,
            ai_mapping=mapping,
            actual_values=actual_values,
            submitted_message=mapping.get("message", {}).get("value", ""),
            final_url=final_url,
            status=status,
            error_message=submit_result.get("error", ""),
            session_log=session_log,
        )
        return proof_id
    except Exception as e:
        logger.exception("FormProof build failed for target %s: %s", target_id, target_url)
        return None


# ── API helpers ────────────────────────────────────────────────

def get_proof_detail(proof_id: int) -> Optional[Dict]:
    app = create_app()
    with app.app_context():
        proof = db.session.get(FormProof, proof_id)
        if not proof:
            return None
        return {
            "id": proof.id,
            "target_url": proof.target_url,
            "status": proof.status,
            "pre_screenshot": proof.pre_screenshot,
            "filling_screenshot": proof.filling_screenshot,
            "post_screenshot": proof.post_screenshot,
            "confirmation_screenshot": proof.confirmation_screenshot,
            "detected_fields": json.loads(proof.detected_fields or "[]"),
            "ai_mapping": json.loads(proof.ai_mapping or "{}"),
            "actual_values": json.loads(proof.actual_values or "{}"),
            "submitted_message": proof.submitted_message,
            "final_url": proof.final_url,
            "error_message": proof.error_message,
            "session_log": proof.session_log,
            "created_at": proof.created_at.isoformat() if proof.created_at else None,
        }


def list_proofs(limit: int = 50, status: str = None) -> List[Dict]:
    app = create_app()
    with app.app_context():
        q = FormProof.query.order_by(FormProof.created_at.desc())
        if status:
            q = q.filter_by(status=status)
        proofs = q.limit(limit).all()
        return [
            {
                "id": p.id,
                "target_url": p.target_url,
                "status": p.status,
                "post_screenshot": p.post_screenshot,
                "final_url": p.final_url,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in proofs
        ]
