"""Redis-based session store for fast token validation.

Reduces PostgreSQL load by caching active sessions in Redis.
TTL matches session expiration (7 days by default).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel

from app.api.cache.redis_client import redis_client
from app.config import settings

logger = logging.getLogger(__name__)

SESSION_PREFIX = "session:"
SESSION_TTL_DAYS = 7
SESSION_TTL_SECONDS = SESSION_TTL_DAYS * 24 * 60 * 60  # 604800 секунд


class SessionData(BaseModel):
    """Данные сессии для хранения в Redis."""
    user_id: int
    username: str
    email: str
    created_at: str
    expires_at: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


def _get_session_key(hashed_token: str) -> str:
    """Генерирует ключ Redis для сессии."""
    return f"{SESSION_PREFIX}{hashed_token}"


async def store_session(
    hashed_token: str,
    session_data: SessionData,
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> bool:
    """
    Сохраняет сессию в Redis с TTL.
    
    Args:
        hashed_token: Хэшированный токен сессии (ключ)
        session_data: Данные сессии (значение)
        ttl_seconds: Время жизни в секундах
        
    Returns:
        True если успешно, False если ошибка
    """
    try:
        await redis_client.set(
            key=_get_session_key(hashed_token),
            value=session_data.model_dump_json(),
            expire=ttl_seconds,
        )
        logger.debug("Session stored in Redis", extra={"hashed_token": hashed_token[:8]})
        return True
    except Exception as e:
        logger.error("Failed to store session in Redis", extra={"error": str(e)})
        return False


async def get_session(hashed_token: str) -> Optional[SessionData]:
    """
    Получает сессию из Redis.
    
    Args:
        hashed_token: Хэшированный токен сессии
        
    Returns:
        SessionData если найдена, None если нет или истекла
    """
    try:
        data = await redis_client.get(_get_session_key(hashed_token))
        if not data:
            return None
        
        session_data = SessionData.model_validate_json(data)
        
        # Дополнительная проверка срока действия
        expires_at = datetime.fromisoformat(session_data.expires_at)
        if datetime.now(timezone.utc) > expires_at:
            # Сессия истекла, удаляем из Redis
            await delete_session(hashed_token)
            return None
        
        return session_data
    except Exception as e:
        logger.error("Failed to get session from Redis", extra={"error": str(e)})
        return None


async def delete_session(hashed_token: str) -> bool:
    """
    Удаляет сессию из Redis (logout).
    
    Args:
        hashed_token: Хэшированный токен сессии
        
    Returns:
        True если удалена, False если не найдена
    """
    try:
        result = await redis_client.delete(_get_session_key(hashed_token))
        logger.debug("Session deleted from Redis", extra={"hashed_token": hashed_token[:8]})
        return result
    except Exception as e:
        logger.error("Failed to delete session from Redis", extra={"error": str(e)})
        return False


async def extend_session(hashed_token: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> bool:
    """
    Продлевает TTL сессии (обновляет время жизни).
    
    Args:
        hashed_token: Хэшированный токен сессии
        ttl_seconds: Новый TTL в секундах
        
    Returns:
        True если продлена, False если сессия не найдена
    """
    try:
        if redis_client.client:
            result = await redis_client.client.expire(
                _get_session_key(hashed_token),
                ttl_seconds,
            )
            return result
        return False
    except Exception as e:
        logger.error("Failed to extend session TTL", extra={"error": str(e)})
        return False


async def is_session_valid(hashed_token: str) -> bool:
    """
    Быстрая проверка валидности сессии (без загрузки данных).
    
    Используется в middleware для производительности.
    
    Args:
        hashed_token: Хэшированный токен сессии
        
    Returns:
        True если сессия существует и не истекла
    """
    try:
        if redis_client.client:
            ttl = await redis_client.client.ttl(_get_session_key(hashed_token))
            return ttl > 0
        return False
    except Exception as e:
        logger.error("Failed to check session validity", extra={"error": str(e)})
        return False