FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY apps apps
COPY packages packages
COPY services services
COPY migrations migrations
COPY alembic.ini alembic.ini
COPY runbooks runbooks
COPY scripts scripts
ENV PYTHONPATH=/app/apps/api:/app/apps/worker:/app/services/crm_simulator:/app/services/support_service:/app/packages/recovery_core
EXPOSE 8101
CMD ["python", "-m", "uvicorn", "recovery_api.main:app", "--host", "0.0.0.0", "--port", "8101"]
