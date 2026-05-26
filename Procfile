web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-5000} --workers ${UVICORN_WORKERS:-1}
worker: celery -A app.celery_app worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-3}
scheduler: celery -A app.celery_app beat --loglevel=info
