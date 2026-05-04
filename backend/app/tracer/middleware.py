import time
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.llm_service import generate_response
from app.db.repositories.trace_repo import TraceRepository

class LLMTracingMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/ask":
            return await call_next(request)

        body = await request.json()
        prompt = body.get("question")

        trace_id = str(uuid.uuid4())
        start_time = time.time()

        # Call LLM
        llm_result = generate_response(prompt)

        latency = int((time.time() - start_time) * 1000)

        cost = (llm_result["input_tokens"] + llm_result["output_tokens"]) * 0.000002  # rough estimate

        # Save trace
        TraceRepository.save({
            "trace_id": trace_id,
            "prompt": prompt,
            "response": llm_result["content"],
            "latency_ms": latency,
            "input_tokens": llm_result["input_tokens"],
            "output_tokens": llm_result["output_tokens"],
            "cost_usd": cost
        })

        return JSONResponse({
            "trace_id": trace_id,
            "response": llm_result["content"]
        })