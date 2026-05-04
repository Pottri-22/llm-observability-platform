from fastapi import FastAPI
from app.api.routes.llm_routes import router
from app.tracer.middleware import LLMTracingMiddleware

app = FastAPI()

app.add_middleware(LLMTracingMiddleware)
app.include_router(router)