from sqlalchemy import Column, String, Integer, Float, Text, DateTime
from datetime import datetime
from app.db.session import engine
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Trace(Base):
    __tablename__ = "traces"

    trace_id = Column(String, primary_key=True)
    prompt = Column(Text)
    response = Column(Text)
    latency_ms = Column(Integer)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cost_usd = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)