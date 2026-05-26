from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Target(db.Model):
    __tablename__ = "target"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(2048), nullable=False, index=True)
    title = db.Column(db.String(500))
    status = db.Column(db.String(50), default="pending", index=True)
    has_form = db.Column(db.Boolean, default=False)
    has_captcha = db.Column(db.Boolean, default=False)
    score = db.Column(db.Integer, default=0)
    page_title = db.Column(db.String(500))
    emails_found = db.Column(db.Integer, default=0)
    source_query = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Submission(db.Model):
    __tablename__ = "submission"

    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(db.Integer, db.ForeignKey("target.id"), nullable=True)
    status = db.Column(db.String(50), index=True)
    fields_filled = db.Column(db.Integer, default=0)
    fields_total = db.Column(db.Integer, default=0)
    screenshot_path = db.Column(db.String(500))
    error_message = db.Column(db.Text)
    field_mapping = db.Column(db.Text)  # JSON: what was written in each field
    session_log = db.Column(db.Text)     # Session activity log
    final_url = db.Column(db.String(2048))  # URL after submission
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    target = db.relationship("Target", backref="submissions")


class Lead(db.Model):
    __tablename__ = "lead"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), index=True)
    name = db.Column(db.String(255))
    company = db.Column(db.String(255))
    source_url = db.Column(db.String(2048))
    status = db.Column(db.String(50), default="new", index=True)
    score = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    # Exact form data sent to the target site
    submitted_form_data = db.Column(db.Text)  # JSON: full field mapping
    submitted_message = db.Column(db.Text)    # The actual message sent
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), nullable=True)
    target_id = db.Column(db.Integer, db.ForeignKey("target.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    submission = db.relationship("Submission", backref="leads")
    target = db.relationship("Target", backref="leads")


class TaskLog(db.Model):
    __tablename__ = "task_log"

    id = db.Column(db.Integer, primary_key=True)
    task_type = db.Column(db.String(50), nullable=False, index=True)
    target_id = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(50), default="running", index=True)
    result = db.Column(db.Text)
    error = db.Column(db.Text)
    started_at = db.Column(db.DateTime, default=utcnow)
    finished_at = db.Column(db.DateTime)


class PipelineStat(db.Model):
    __tablename__ = "pipeline_stat"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime)
    total_targets = db.Column(db.Integer, default=0)
    processed = db.Column(db.Integer, default=0)
    submitted = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    skipped = db.Column(db.Integer, default=0)
    captchas_solved = db.Column(db.Integer, default=0)
    captchas_failed = db.Column(db.Integer, default=0)
    rate_per_hour = db.Column(db.Float, default=0)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
