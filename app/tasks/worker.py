"""ARQ background worker configuration."""

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings


async def enqueue_task(func_name: str, *args, **kwargs):
    """Enqueue background task."""
    pool = await create_pool(
        RedisSettings(
            host=settings.redis_host,
            port=settings.redis_port,
        )
    )
    return await pool.enqueue_job(func_name, *args, **kwargs)


class WorkerSettings:
    """ARQ worker settings."""

    functions = []  # Add task functions here
    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
    )
    burst = False