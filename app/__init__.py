import logging
import os
import time

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from app.config import Config


db = SQLAlchemy()


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)

    db.init_app(app)

    # Quiet noisy logs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)

    def _wait_for_db(max_tries=10, delay=2):
        for attempt in range(max_tries):
            try:
                with app.app_context():
                    db.engine.connect().close()
                return True
            except Exception:
                time.sleep(delay)
        return False

    with app.app_context():
        from app.routes import register_routes
        register_routes(app)
        _wait_for_db()
        # Ensure instance directory exists for SQLite
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if db_uri.startswith("sqlite:///"):
            db_path = db_uri.replace("sqlite:///", "")
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            # Enable WAL mode for better concurrency
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    conn.execute(text("PRAGMA journal_mode=WAL"))
                    conn.execute(text("PRAGMA synchronous=NORMAL"))
            except Exception:
                pass
        db.create_all()

    return app
