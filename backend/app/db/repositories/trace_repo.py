from app.db.session import SessionLocal
from app.db.models.trace import Trace

class TraceRepository:

    @staticmethod
    def save(trace_data: dict):
        db = SessionLocal()
        trace = Trace(**trace_data)
        db.add(trace)
        db.commit()
        db.close()