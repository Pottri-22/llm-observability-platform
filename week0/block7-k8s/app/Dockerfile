# Week 0 · Block 7 — hello-FastAPI image.
# Mirrors the patterns the real backend/Dockerfile uses: slim base, deps
# layer cached separately from source, non-root user, no shell in CMD.

FROM python:3.12-slim

# Don't buffer stdout/stderr — logs show up immediately in `kubectl logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Deps first: this layer is cached and only rebuilds when requirements change,
# not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Run as a non-root user — K8s securityContext will also enforce this, but
# the image should be correct on its own.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# Exec form (no shell) so uvicorn is PID 1 and receives SIGTERM directly for
# a clean graceful shutdown when K8s rolls or deletes the pod.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
