import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os

async def setup_mongodb():
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    db = client.am_analytics
    
    # Create TTL index for 2-year retention (63072000 seconds)
    print("Creating TTL index for business_events...")
    await db.business_events.create_index("timestamp", expireAfterSeconds=63072000)
    
    # Create TTL index for technical_stats metadata (e.g., 30 days retention)
    print("Creating TTL index for technical_stats...")
    await db.technical_stats.create_index("timestamp", expireAfterSeconds=2592000)
    
    print("MongoDB setup complete.")

if __name__ == "__main__":
    asyncio.run(setup_mongodb())
