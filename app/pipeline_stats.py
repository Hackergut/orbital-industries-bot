"""Pipeline stats tracker — extracted to avoid circular imports."""
from datetime import datetime, timezone


class PipelineStats:
    def __init__(self):
        self.started_at = None
        self.total_targets = 0
        self.processed = 0
        self.submitted = 0
        self.failed = 0
        self.skipped = 0
        self.captchas_solved = 0
        self.captchas_failed = 0

    def start(self, total: int):
        self.started_at = datetime.now(timezone.utc)
        self.total_targets = total
        self.processed = 0
        self.submitted = 0
        self.failed = 0
        self.skipped = 0

    def record_submit(self):
        self.processed += 1
        self.submitted += 1

    def record_fail(self):
        self.processed += 1
        self.failed += 1

    def record_skip(self):
        self.processed += 1
        self.skipped += 1

    def record_captcha(self, solved: bool = False):
        if solved:
            self.captchas_solved += 1
        else:
            self.captchas_failed += 1

    def to_dict(self):
        elapsed = 0
        if self.started_at:
            elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        rate = 0
        if elapsed > 0:
            rate = (self.submitted / elapsed) * 3600
        return {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "total_targets": self.total_targets,
            "processed": self.processed,
            "submitted": self.submitted,
            "failed": self.failed,
            "skipped": self.skipped,
            "captchas_solved": self.captchas_solved,
            "captchas_failed": self.captchas_failed,
            "rate_per_hour": round(rate, 1),
            "elapsed_seconds": round(elapsed, 1),
        }


pipeline_stats = PipelineStats()
