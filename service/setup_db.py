import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os

async def setup_mongodb():
    mongo_url = os.getenv("MONGO_URL")
    if not mongo_url:
        mongo_host = os.getenv("MONGO_HOST", "mongodb.infra.svc.cluster.local")
        mongo_port = os.getenv("MONGO_PORT", "27017")
        mongo_user = os.getenv("MONGO_USERNAME", "")
        mongo_pass = os.getenv("MONGO_PASSWORD", "")
        if mongo_user and mongo_pass:
            mongo_url = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/am_analytics?authSource=admin"
        else:
            mongo_url = f"mongodb://{mongo_host}:{mongo_port}"
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
