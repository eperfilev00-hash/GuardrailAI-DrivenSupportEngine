"""Rate limiting middleware using Redis."""

import time
import logging
from typing import Callable
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Redis-based rate limiter using sliding window algorithm.
    
    Prevents financial DoS attacks on LLM API endpoints.
    """
    
    def __init__(self, redis: Redis):
        self.redis = redis
        # Лимиты по умолчанию (можно кастомизировать по эндпоинтам)
        self.requests_per_minute = 60
        self.requests_per_hour = 500
        self.requests_per_day = 5000
    
    async def is_rate_limited(
        self,
        identifier: str,
        window_seconds: int = 60,
        max_requests: int | None = None,
    ) -> tuple[bool, dict]:
        """
        Check if identifier has exceeded rate limit.
        
        Uses Redis sorted set for sliding window.
        
        Args:
            identifier: Unique ID (user_id, IP, API key)
            window_seconds: Time window in seconds
            max_requests: Max requests per window (default: requests_per_minute)
            
        Returns:
            (is_limited: bool, info: dict with remaining/reset info)
        """
        if max_requests is None:
            max_requests = self.requests_per_minute
        
        now = time.time()
        window_start = now - window_seconds
        
        key = f"ratelimit:{identifier}:{window_seconds}"
        
        async with self.redis.pipeline(transaction=True) as pipe:
            # Удаляем старые записи за пределами окна
            await pipe.zremrangebyscore(key, 0, window_start)
            # Считаем текущие запросы в окне
            await pipe.zcard(key)
            # Добавляем текущий запрос
            await pipe.zadd(key, {f"{now}": now})
            # Устанавливаем TTL для ключа
            await pipe.expire(key, window_seconds * 2)
            
            results = await pipe.execute()
        
        current_count = results[1]  # Количество запросов до добавления текущего
        
        if current_count >= max_requests:
            # Лимит превышен
            return True, {
                "limit": max_requests,
                "remaining": 0,
                "reset_at": window_start + window_seconds,
                "window_seconds": window_seconds,
            }
        
        return False, {
            "limit": max_requests,
            "remaining": max_requests - current_count - 1,
            "reset_at": now + window_seconds,
            "window_seconds": window_seconds,
        }
    
    async def get_usage(self, identifier: str) -> dict:
        """Get current usage stats for identifier."""
        now = time.time()
        
        usage = {}
        for window, limit in [
            (60, self.requests_per_minute),
            (3600, self.requests_per_hour),
            (86400, self.requests_per_day),
        ]:
            key = f"ratelimit:{identifier}:{window}"
            count = await self.redis.zcard(key)
            usage[f"{window}s"] = {
                "used": count,
                "limit": limit,
                "remaining": max(0, limit - count),
            }
        
        return usage


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for rate limiting.
    
    Applies rate limits based on:
    1. User ID (if authenticated)
    2. IP address (fallback for anonymous)
    """
    
    def __init__(self, app, redis_url: str | None = None):
        super().__init__(app)
        redis_url = redis_url or settings.redis_url
        self.redis = Redis.from_url(redis_url, decode_responses=True)
        self.limiter = RateLimiter(self.redis)
        
        # Эндпоинты, которые нужно лимитировать
        self.protected_paths = {
            "/api/support": {"requests_per_minute": 30, "requests_per_hour": 200},
            "/api/chat": {"requests_per_minute": 30, "requests_per_hour": 200},
            "/api/agents": {"requests_per_minute": 20, "requests_per_hour": 100},
        }
        
        # Эндпоинты, исключаемые из лимитов (аутентификация, health check)
        self.excluded_paths = {
            "/api/auth/login",
            "/api/auth/registration",
            "/health",
            "/docs",
            "/openapi.json",
        }
    
    async def dispatch(self, request: Request, call_next: Callable):
        # Пропускаем исключённые пути
        if any(request.url.path.startswith(path) for path in self.excluded_paths):
            return await call_next(request)
        
        # Проверяем, защищённый ли путь
        protected = False
        custom_limits = {}
        for path, limits in self.protected_paths.items():
            if request.url.path.startswith(path):
                protected = True
                custom_limits = limits
                break
        
        if not protected:
            return await call_next(request)
        
        # Определяем идентификатор (user_id > IP)
        identifier = self._get_identifier(request)
        
        # Проверяем лимиты
        is_limited, info = await self.limiter.is_rate_limited(
            identifier=identifier,
            window_seconds=60,
            max_requests=custom_limits.get("requests_per_minute", self.limiter.requests_per_minute),
        )
        
        if is_limited:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "identifier": identifier,
                    "path": request.url.path,
                    "remaining": info["remaining"],
                }
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limit_exceeded",
                    "detail": "Too many requests. Please try again later.",
                    "limit": info["limit"],
                    "remaining": info["remaining"],
                    "reset_at": info["reset_at"],
                },
                headers={
                    "X-RateLimit-Limit": str(info["limit"]),
                    "X-RateLimit-Remaining": str(info["remaining"]),
                    "X-RateLimit-Reset": str(int(info["reset_at"])),
                },
            )
        
        # Продолжаем запрос
        response = await call_next(request)
        
        # Добавляем заголовки с информацией о лимитах
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(int(info["reset_at"]))
        
        return response
    
    def _get_identifier(self, request: Request) -> str:
        """Get unique identifier for rate limiting."""
        # Приоритет 1: User ID из сессии/токена
        user_id = request.session.get("user_id") if hasattr(request, "session") else None
        if user_id:
            return f"user:{user_id}"
        
        # Приоритет 2: API key из заголовка
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"apikey:{api_key[:16]}"  # Первые 16 символов
        
        # Приоритет 3: IP адрес (fallback)
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}"
    
    async def close(self):
        """Close Redis connection on shutdown."""
        await self.redis.close()