import asyncio
import redis.asyncio as aioredis
import os
from dotenv import load_dotenv

load_dotenv()

async def clear_cache():
    try:
        redis_client = await aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses=True
        )
        await redis_client.flushall()
        print("✅ Cache cleared successfully!")
        await redis_client.close()
    except Exception as e:
        print(f"❌ Error clearing cache: {e}")

if __name__ == "__main__":
    asyncio.run(clear_cache())
