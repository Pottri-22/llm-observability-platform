"""Week 0 · Block 7 — hello-FastAPI, the workload Kind + Helm deploy.

Intentionally tiny. Its only jobs:
  - `/`        — return JSON including the pod's hostname, so you can SEE
                 which replica served the request (proves Service load-
                 balancing) and which "release" deployed it (raw manifest
                 vs Helm).
  - `/healthz` — a liveness/readiness probe target. K8s hits this to decide
                 if the pod is alive and ready for traffic.

The whole point of the block is the *deployment path*, not the app.
"""

from __future__ import annotations

import os
import socket

from fastapi import FastAPI

app = FastAPI(title="hello-aegis", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "hello-aegis",
        "message": "deployed to Kubernetes — Week 0 Block 7",
        "pod": socket.gethostname(),
        # Set via the Deployment's env / Helm values so we can tell the raw-
        # manifest deploy apart from the Helm deploy at runtime.
        "release": os.environ.get("RELEASE_NAME", "raw-manifests"),
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
