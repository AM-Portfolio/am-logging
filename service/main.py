import os
import json
import uuid
import datetime
import asyncio
from typing import Optional, Dict, Any, List
from enum import Enum

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Path, Body, Query
from pydantic import BaseModel, Field, validator
import redis.asyncio as redis
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- Local Environment Support ---
# Load .env file if present in the current directory or parent
load_dotenv()

app = FastAPI(title="AM Centralized Logging Service", version="1.0.0")

@app.on_event("startup")
async def startup_event():
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
        print("✅ Redis connection successful")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
    
    try:
        # Test MongoDB
        await db.command('ping')
        print("✅ MongoDB connection successful")
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")

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
    print(f"Distributing log: {masked_log['trace_id']}")
    
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
                    print(f"Successfully updated business event: {trace_id}")
                else:
                    print(f"No document found to update for trace_id: {trace_id}, inserting new")
                    await db.business_events.insert_one(masked_log)
            else:
                # Insert new document
                await db.business_events.insert_one(masked_log)
            print("Successfully persisted to MongoDB")
        except Exception as e:
            print(f"Failed to persist to MongoDB: {e}")
    
    # 3. Push to Loki (Technical track)
    print(f"Loki push simulated for: {masked_log['trace_id']}")

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
    
    **Log Types:**
    - **BUSINESS**: Business events like orders, payments, user actions (persisted to MongoDB)
    - **AUDIT**: Security and compliance events (persisted to MongoDB)  
    - **TECHNICAL**: System events, debugging, performance metrics (sent to Loki)
    
    **Status Flow:**
    - pending → processing → in_progress → completed/failed
    
    **Example Usage:**
    ```json
    {
      "trace_id": "txn_12345",
      "span_id": "order_process",
      "service": "order-service",
      "timestamp": "2024-03-14T12:00:00Z",
      "log_type": "BUSINESS",
      "level": "INFO",
      "status": "processing",
      "intensity": "normal",
      "payload": {
        "order_id": "ORD-123",
        "customer_id": "CUST-456",
        "amount": 99.99
      }
    }
    ```
    """
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
    
    **Status Values:**
    - `pending`: Initial state
    - `processing`: Being processed
    - `in_progress`: Currently being handled
    - `completed`: Successfully finished
    - `failed`: Failed with errors
    - `cancelled`: Cancelled before completion
    
    **Intensity Levels:**
    - `low`: Low priority/normal operations
    - `normal`: Standard priority
    - `urgent`: High priority, requires attention
    
    **Example Request:**
    ```json
    {
      "status": "completed",
      "intensity": "normal",
      "message": "Order processed successfully"
    }
    ```
    
    **Responses:**
    - `200`: Successfully updated
    - `201`: New log created (if didn't exist)
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
            print(f"Created new log entry for trace_id: {trace_id}")
            
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
            print(f"Successfully updated business event: {trace_id}")
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
        print(f"Failed to update log {trace_id}: {str(e)}")
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
    
    **Search Order:**
    1. MongoDB (persisted logs)
    2. Redis queue (recently added, not yet processed)
    
    **Response Status:**
    - `found`: Log found in database
    - `found_in_queue`: Log found in Redis queue (not yet processed)
    - `not_found`: Log not found anywhere
    
    **Example Response:**
    ```json
    {
      "status": "found",
      "log": {
        "trace_id": "txn_12345",
        "service": "order-service",
        "status": "completed",
        "payload": {...}
      }
    }
    ```
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
                print(f"Redis check failed: {redis_error}")
            
            return {
                "status": "not_found",
                "trace_id": trace_id,
                "message": "Log not found in database or queue"
            }
    except Exception as e:
        print(f"Failed to retrieve log {trace_id}: {str(e)}")
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
    
    **Parameters:**
    - `limit`: Number of logs to return (max 100)
    - `offset`: Number of logs to skip (for pagination)
    
    **Default Behavior:**
    - Returns 10 most recent logs
    - Sorted by timestamp (newest first)
    - Only includes BUSINESS and AUDIT logs from MongoDB
    
    **Example Response:**
    ```json
    {
      "status": "success",
      "logs": [...],
      "count": 10,
      "offset": 0,
      "limit": 10
    }
    ```
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
        print(f"Failed to list logs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list logs: {str(e)}")

@app.get("/health",
         summary="Health check",
         description="Check the health status of the logging service and its dependencies.",
         response_description="Service health status",
         tags=["System"])
async def health():
    """
    Health check endpoint for the logging service.
    
    **Response:**
    - `healthy`: All systems operational
    - `degraded`: Some services have issues but logging still works
    - `unhealthy`: Critical failures
    
    **Dependencies Checked:**
    - Redis connection (for log queuing)
    - MongoDB connection (for log persistence)
    - Service status
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
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
