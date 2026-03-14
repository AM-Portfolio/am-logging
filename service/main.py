import os
import json
import uuid
import datetime
import asyncio
from typing import Optional, Dict, Any, List
from enum import Enum

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field, validator
import redis.asyncio as redis
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- Local Environment Support ---
# Load .env file if present in the current directory or parent
load_dotenv()

app = FastAPI(title="AM Centralized Logging Service", version="1.0.0")

# --- Configuration ---
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-service.infra.svc.cluster.local:6379/0")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongodb-service.infra.svc.cluster.local:27017")
LOKI_URL = os.getenv("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push")
ENVIRONMENT = os.getenv("ENVIRONMENT", "preprod")

# --- Redis & Mongo Clients ---
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.am_analytics

# --- Models (Sync with logging_api_spec.yaml) ---
class LogType(str, Enum):
    TECHNICAL = "TECHNICAL"
    BUSINESS = "BUSINESS"
    AUDIT = "AUDIT"

class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class LogContext(BaseModel):
    class_name: Optional[str] = Field(None, alias="class")
    method: Optional[str] = None
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    latency_ms: Optional[float] = None

class ExceptionInfo(BaseModel):
    type: str
    message: str
    stack: str

class LogEntry(BaseModel):
    trace_id: str
    span_id: str
    service: str
    timestamp: datetime.datetime
    log_type: LogType
    level: LogLevel
    context: Optional[LogContext] = None
    payload: Dict[str, Any]
    exception: Optional[ExceptionInfo] = None
    metadata: Optional[Dict[str, str]] = None

    class Config:
        populate_by_name = True

# --- Masking Engine (Stub) ---
SENSITIVE_FIELDS = ["password", "token", "secret", "cvv", "credit_card"]

def mask_data(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: mask_data(v) if k.lower() not in SENSITIVE_FIELDS else "[REDACTED]" for k, v in data.items()}
    elif isinstance(data, list):
        return [mask_data(item) for item in data]
    return data

# --- Data Distribution (Background) ---
async def distribute_log(log_data: dict):
    # 1. Mask PII
    masked_log = mask_data(log_data)
    print(f"Distributing log: {masked_log['trace_id']}")
    
    # 2. Persist to MongoDB if BUSINESS or AUDIT
    if log_data["log_type"] in [LogType.BUSINESS, LogType.AUDIT]:
        try:
            await db.business_events.insert_one(masked_log)
            print("Successfully persisted to MongoDB")
        except Exception as e:
            print(f"Failed to persist to MongoDB: {e}")
    
    # 3. Push to Loki (Technical track)
    print(f"Loki push simulated for: {masked_log['trace_id']}")

# --- Endpoints ---
@app.post("/v1/logs", status_code=202)
async def ingest_log(log: LogEntry, background_tasks: BackgroundTasks):
    log_dict = log.model_dump() if hasattr(log, 'model_dump') else log.dict()
    log_dict["timestamp"] = log_dict["timestamp"].isoformat()
    log_dict["trace_id"] = str(log_dict["trace_id"])
    
    # Immediate persist to Redis for Zero Log Loss
    try:
        await redis_client.lpush("logging_queue", json.dumps(log_dict))
        print("Log pushed to Redis queue")
    except Exception as e:
        print(f"Warning: Failed to push to Redis: {e}")
    
    # Background task to process and distribute
    background_tasks.add_task(distribute_log, log_dict)
    
    return {"status": "accepted", "trace_id": log_dict["trace_id"]}

@app.get("/health")
async def health():
    return {"status": "healthy"}

def main():
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
