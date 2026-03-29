import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from logging_config import setup_logging

# Setup Logging
logger = setup_logging("am-logging-db-setup", os.getenv("LOG_LEVEL", "INFO"))

async def setup_mongodb():
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    db = client.am_analytics
    
    try:
        # Create TTL index for 2-year retention (63072000 seconds)
        logger.info("Creating TTL index for business_events...", extra={"collection": "business_events", "ttl_seconds": 63072000})
        await db.business_events.create_index("timestamp", expireAfterSeconds=63072000)
        
        # Create TTL index for technical_stats metadata (e.g., 30 days retention)
        logger.info("Creating TTL index for technical_stats...", extra={"collection": "technical_stats", "ttl_seconds": 2592000})
        await db.technical_stats.create_index("timestamp", expireAfterSeconds=2592000)
        
        logger.info("MongoDB setup complete.")
    except Exception as e:
        logger.error(f"MongoDB setup failed: {e}", extra={"error": str(e)})
        raise

if __name__ == "__main__":
    asyncio.run(setup_mongodb())
