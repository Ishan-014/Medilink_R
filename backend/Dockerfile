FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements-foundation.txt /app/backend/requirements-foundation.txt
RUN python -m pip install --no-cache-dir -r backend/requirements-foundation.txt \
    && groupadd --gid 10001 medilink \
    && useradd --uid 10001 --gid medilink --no-create-home medilink \
    && mkdir -p /app/var/documents \
    && chown -R medilink:medilink /app/var
COPY --chown=medilink:medilink backend /app/backend
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-m", "backend.ops.healthcheck"]
CMD ["python", "-m", "uvicorn", "backend.application:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--log-config", "backend/config/logging.json", "--no-access-log"]
