import os
import json
import uuid
import datetime
import asyncio
from typing import Optional, Dict, Any, List
from enum import Enum
import logging

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Path, Body, Query
from pydantic import BaseModel, Field, validator
import redis.asyncio as redis
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

from logging_config import setup_logging

# --- Local Environment Support ---
# Load .env file if present in the current directory or parent
load_dotenv()

# Setup Logging
logger = setup_logging("am-logging-service", os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="AM Centralized Logging Service", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    logger.info("Service starting up")
    await test_db_connection()

# --- Configuration ---
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-service.infra.svc.cluster.local:6379/0")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongodb-service.infra.svc.cluster.local:27017")
LOKI_URL = os.getenv("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push")
ENVIRONMENT = os.getenv("ENVIRONMENT", "preprod")

# --- Redis & Mongo Clients ---
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = mongo_client.am_analytics

# --- Database Connection Test ---
async def test_db_connection():
    """Test database connections on startup"""
    try:
        # Test Redis
        await redis_client.ping()
        logger.info("Redis connection successful", extra={"component": "redis", "status": "connected"})
    except Exception as e:
        logger.error(f"Redis connection failed: {e}", extra={"component": "redis", "status": "failed", "error": str(e)})
    
    try:
        # Test MongoDB
        await db.command('ping')
        logger.info("MongoDB connection successful", extra={"component": "mongodb", "status": "connected"})
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}", extra={"component": "mongodb", "status": "failed", "error": str(e)})

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

class StatusType(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class IntensityType(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    URGENT = "urgent"

class LogContext(BaseModel):
    class_name: Optional[str] = Field(None, alias="class", description="Class name where the log originated")
    method: Optional[str] = Field(None, description="Method name that generated the log")
    filename: Optional[str] = Field(None, description="Filename where the log originated")
    line_number: Optional[int] = Field(None, description="Line number where the log originated")
    inputs: Optional[Dict[str, Any]] = Field(None, description="Input parameters for the method")
    outputs: Optional[Dict[str, Any]] = Field(None, description="Output values from the method")
    latency_ms: Optional[float] = Field(None, description="Execution time in milliseconds")

class ExceptionInfo(BaseModel):
    type: str = Field(..., description="Exception type/class name")
    message: str = Field(..., description="Exception message")
    stack: str = Field(..., description="Full stack trace")

class LogEntry(BaseModel):
    trace_id: str = Field(..., description="Unique identifier for the entire transaction/request")
    span_id: str = Field(..., description="Identifier for this specific operation within the trace")
    service: str = Field(..., description="Service name that generated the log")
    timestamp: datetime.datetime = Field(..., description="Timestamp when the log was created (ISO 8601 format)")
    log_type: LogType = Field(..., description="Type of log: BUSINESS, AUDIT, or TECHNICAL")
    level: LogLevel = Field(..., description="Log level: DEBUG, INFO, WARN, ERROR, CRITICAL")
    status: Optional[StatusType] = Field(StatusType.PENDING, description="Current status: pending, processing, in_progress, completed, failed, cancelled")
    intensity: Optional[IntensityType] = Field(IntensityType.NORMAL, description="Intensity level: low, normal, urgent")
    context: Optional[LogContext] = Field(None, description="Context information about the operation")
    payload: Dict[str, Any] = Field(..., description="Main log data/content")
    exception: Optional[ExceptionInfo] = Field(None, description="Exception details if an error occurred")
    metadata: Optional[Dict[str, str]] = Field(None, description="Additional metadata as key-value pairs")

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "trace_id": "txn_12345_abcde",
                "span_id": "order_processing",
                "service": "order-service",
                "timestamp": "2024-03-14T12:00:00Z",
                "log_type": "BUSINESS",
                "level": "INFO",
                "status": "processing",
                "intensity": "normal",
                "context": {
                    "class_name": "OrderService",
                    "method": "processOrder",
                    "latency_ms": 150.5
                },
                "payload": {
                    "order_id": "ORD-12345",
                    "customer_id": "CUST-67890",
                    "amount": 99.99,
                    "action": "order_placed"
                },
                "metadata": {
                    "region": "us-west-2",
                    "version": "1.2.3"
                }
            }
        }

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
    logger.debug(f"Distributing log", extra={"trace_id": masked_log['trace_id']})
    
    # 2. Persist to MongoDB if BUSINESS or AUDIT (and not explicitly disabled)
    persist_to_db = log_data.get("metadata", {}).get("persist_to_db", "true").lower() == "true"
    
    if persist_to_db and log_data["log_type"] in [LogType.BUSINESS, LogType.AUDIT]:
        try:
            # Check if this is an update (has update_status field)
            if "update_status" in log_data.get("payload", {}):
                # Update existing document
                trace_id = log_data["trace_id"]
                update_payload = log_data["payload"]
                
                # Update the document with new status and metadata
                update_data = {
                    "$set": {
                        "payload.status": update_payload.get("status"),
                        "payload.intensity": update_payload.get("intensity"),
                        "payload.updated_at": datetime.datetime.utcnow().isoformat(),
                        "level": log_data.get("level", "INFO"),
                        "timestamp": log_data["timestamp"]
                    }
                }
                
                result = await db.business_events.update_one(
                    {"trace_id": trace_id}, 
                    update_data
                )
                
                if result.modified_count > 0:
                    logger.info(f"Successfully updated business event", extra={"trace_id": trace_id})
                else:
                    logger.info(f"No document found to update for trace_id, inserting new", extra={"trace_id": trace_id})
                    await db.business_events.insert_one(masked_log)
            else:
                # Insert new document
                await db.business_events.insert_one(masked_log)
            logger.info("Successfully persisted to MongoDB", extra={"trace_id": masked_log['trace_id']})
        except Exception as e:
            logger.error(f"Failed to persist to MongoDB: {e}", extra={"trace_id": masked_log['trace_id'], "error": str(e)})
    
    # 3. Push to Loki (Technical track)
    logger.debug(f"Loki push simulated", extra={"trace_id": masked_log['trace_id']})

# --- Endpoints ---
@app.post("/v1/logs", 
          status_code=202,
          summary="Create a new log entry",
          description="Create a new log entry. Business and Audit logs are persisted to MongoDB, all logs are queued in Redis for processing.",
          response_description="Returns acceptance confirmation with trace ID",
          tags=["Logs"])
async def ingest_log(log: LogEntry, background_tasks: BackgroundTasks):
    """
    Create a new log entry in the system.
    """
    log_dict = log.model_dump() if hasattr(log, 'model_dump') else log.dict()
    log_dict["timestamp"] = log_dict["timestamp"].isoformat()
    log_dict["trace_id"] = str(log_dict["trace_id"])
    
    # Immediate persist to Redis for Zero Log Loss
    try:
        await redis_client.lpush("logging_queue", json.dumps(log_dict))
        logger.debug("Log pushed to Redis queue", extra={"trace_id": log_dict["trace_id"]})
    except Exception as e:
        logger.warning(f"Failed to push to Redis: {e}", extra={"trace_id": log_dict["trace_id"], "error": str(e)})
    
    # Background task to process and distribute
    background_tasks.add_task(distribute_log, log_dict)
    
    return {"status": "accepted", "trace_id": log_dict["trace_id"]}

@app.put("/v1/logs/{trace_id}", 
          status_code=200,
          summary="Update log status",
          description="Update the status and intensity of an existing log entry. If the log doesn't exist, it will be created.",
          response_description="Returns update confirmation with new status",
          tags=["Logs"])
async def update_log_status(
    trace_id: str = Path(..., description="Unique trace identifier for the log entry"),
    status_update: dict = Body(..., description="Status update information")
):
    """
    Update the status of an existing business event or create a new one if it doesn't exist.
    """
    try:
        # First check if the document exists
        existing_log = await db.business_events.find_one({"trace_id": trace_id})
        
        if not existing_log:
            # Create a new log entry if it doesn't exist
            new_log = {
                "trace_id": trace_id,
                "span_id": "update",
                "service": "logging-service",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "log_type": "BUSINESS",
                "level": "INFO",
                "status": status_update.get("status", "created"),
                "intensity": status_update.get("intensity", "normal"),
                "payload": {
                    "message": status_update.get("message", "Log created via update"),
                    "created_via": "update_endpoint"
                },
                "metadata": {"created_by": "api"}
            }
            
            await db.business_events.insert_one(new_log)
            logger.info(f"Created new log entry via update", extra={"trace_id": trace_id})
            
            return {
                "status": "created", 
                "trace_id": trace_id,
                "new_status": status_update.get("status", "created"),
                "message": "New log entry created"
            }
        
        # Update existing document
        update_data = {
            "$set": {
                "status": status_update.get("status", "updated"),
                "intensity": status_update.get("intensity", "normal"),
                "level": status_update.get("level", "INFO"),
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
        }
        
        result = await db.business_events.update_one(
            {"trace_id": trace_id}, 
            update_data
        )
        
        if result.modified_count > 0:
            logger.info(f"Successfully updated business event", extra={"trace_id": trace_id})
            return {
                "status": "updated", 
                "trace_id": trace_id,
                "new_status": status_update.get("status", "updated"),
                "message": "Log entry updated successfully"
            }
        else:
            return {
                "status": "no_change", 
                "trace_id": trace_id,
                "message": "No changes made to the log entry"
            }
            
    except Exception as e:
        logger.error(f"Failed to update log {trace_id}: {str(e)}", extra={"trace_id": trace_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to update log: {str(e)}")

@app.get("/v1/logs/{trace_id}", 
         summary="Get log by trace ID",
         description="Retrieve a specific log entry by its trace ID. Checks both MongoDB and Redis queue.",
         response_description="Returns the log entry if found",
         tags=["Logs"])
async def get_log(
    trace_id: str = Path(..., description="Unique trace identifier for the log entry")
):
    """
    Retrieve a specific business event by trace_id.
    """
    try:
        log = await db.business_events.find_one({"trace_id": trace_id})
        if log:
            # Convert ObjectId to string
            log["_id"] = str(log["_id"])
            return {
                "status": "found",
                "log": log
            }
        else:
            # Check if it's in Redis queue (recently added but not yet processed)
            try:
                redis_logs = await redis_client.lrange("logging_queue", 0, -1)
                for redis_log in redis_logs:
                    log_data = json.loads(redis_log)
                    if log_data.get("trace_id") == trace_id:
                        return {
                            "status": "found_in_queue",
                            "log": log_data,
                            "message": "Log found in Redis queue, not yet persisted to MongoDB"
                        }
            except Exception as redis_error:
                logger.warning(f"Redis check failed: {redis_error}", extra={"trace_id": trace_id, "error": str(redis_error)})
            
            return {
                "status": "not_found",
                "trace_id": trace_id,
                "message": "Log not found in database or queue"
            }
    except Exception as e:
        logger.error(f"Failed to retrieve log {trace_id}: {str(e)}", extra={"trace_id": trace_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to retrieve log: {str(e)}")

@app.get("/v1/logs",
         summary="List all logs",
         description="Retrieve paginated list of business events from MongoDB, sorted by timestamp (newest first).",
         response_description="Returns paginated list of logs",
         tags=["Logs"])
async def list_logs(
    limit: int = Query(10, ge=1, le=100, description="Number of logs to return (1-100)"),
    offset: int = Query(0, ge=0, description="Number of logs to skip for pagination")
):
    """
    List recent business events with pagination.
    """
    try:
        logs = []
        cursor = db.business_events.find().sort("timestamp", -1).skip(offset).limit(limit)
        
        async for log in cursor:
            log["_id"] = str(log["_id"])
            logs.append(log)
        
        return {
            "status": "success",
            "logs": logs,
            "count": len(logs),
            "offset": offset,
            "limit": limit
        }
    except Exception as e:
        logger.error(f"Failed to list logs: {str(e)}", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list logs: {str(e)}")

@app.get("/health",
         summary="Health check",
         description="Check the health status of the logging service and its dependencies.",
         response_description="Service health status",
         tags=["System"])
async def health():
    """
    Health check endpoint for the logging service.
    """
    return {
        "status": "healthy",
        "service": "AM Centralized Logging Service",
        "version": "1.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "dependencies": {
            "redis": "connected",
            "mongodb": "connected"
        }
    }

def main():
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    # Configure Uvicorn to use our loggers
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)

if __name__ == "__main__":
    main()
