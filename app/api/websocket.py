"""WebSocket streaming and analytics endpoint."""

import asyncio
from collections import deque
import json
from datetime import datetime, timezone
import time
from typing import Dict

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ИМПОРТЫ: Добавили функцию стриминга для агентов
from app.agents.router import route_request_stream
from app.db.database import engine
from app.cache import _cache_lock, _dashboard_cache
# Во избежание конфликтов типов c сессией алхимии, импортируем модель Session как ChatSession
from app.db.models import Conversation, Message, Session as ChatSession, User

logger = structlog.get_logger()

websocket_router = APIRouter()

# Фабрика асинхронных сессий
async_session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False
)

# Active connections tracker
active_connections: Dict[str, WebSocket] = {}


active_connections: Dict[str, WebSocket] = {}

WS_MAX_CONCURRENT_REQUESTS = 1  # Только 1 запрос одновременно на соединение
WS_RATE_LIMIT_MESSAGES = 30     # Максимум сообщений в минуту
WS_RATE_LIMIT_WINDOW = 60       # Окно rate limiting (секунды)
WS_REQUEST_TIMEOUT = 120        # Таймаут на один запрос (секунды)

async def validate_session_token(db_session, token: str) -> ChatSession | None:
    """
    Валидация сессионного токена.
    Проверяет существование сессии и срок действия.
    """
    from app.api.registr.hash import hash_password
    
    # Хэшируем токен для сравнения с БД
    hashed_token = hash_password(token)
    
    # Ищем сессию
    result = await db_session.execute(
        select(ChatSession).where(ChatSession.session_id == hashed_token)
    )
    session = result.scalar_one_or_none()
    
    if not session:
        return None
    
    # Проверяем срок действия
    if datetime.now(timezone.utc) > session.expires_at:
        return None
    
    return session


class WebSocketRateLimiter:
    """
    Rate limiter для WebSocket соединений.
    
    Использует sliding window для подсчёта сообщений.
    """
    
    def __init__(self, max_messages: int = WS_RATE_LIMIT_MESSAGES, window_seconds: int = WS_RATE_LIMIT_WINDOW):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.timestamps: deque = deque()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """
        Проверить, можно ли отправить сообщение.
        
        Returns:
            True если разрешено, False если лимит превышен
        """
        async with self._lock:
            now = time.time()
            window_start = now - self.window_seconds
            
            # Удаляем старые timestamps за пределами окна
            while self.timestamps and self.timestamps[0] < window_start:
                self.timestamps.popleft()
            
            # Проверяем лимит
            if len(self.timestamps) >= self.max_messages:
                return False
            
            # Добавляем текущий timestamp
            self.timestamps.append(now)
            return True
    
    def get_remaining(self) -> int:
        """Количество оставшихся сообщений в текущем окне."""
        now = time.time()
        window_start = now - self.window_seconds
        
        # Считаем актуальные timestamps (без мутации deque)
        count = sum(1 for ts in self.timestamps if ts >= window_start)
        return max(0, self.max_messages - count)
    
    def get_reset_time(self) -> float:
        """Время до сброса лимита (секунды)."""
        if not self.timestamps:
            return 0
        
        now = time.time()
        oldest = self.timestamps[0]
        return max(0, (oldest + self.window_seconds) - now)


@websocket_router.websocket("/support")
async def websocket_support_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for streaming AI responses.
    Supports real-time token streaming for lower latency.
    
    SECURITY: Requires valid session token in query parameter.
    Example: ws://.../support?session_token=XXX
    
    RATE LIMITING: 
    - Max 1 concurrent request per connection
    - Max 30 messages per minute per connection
    - Request timeout: 120 seconds
    """
    # === БЕЗОПАСНОСТЬ: Валидация сессии ПЕРЕД accept() ===
    session_token = websocket.query_params.get("session_token")
    
    if not session_token:
        await websocket.close(code=4001, reason="Missing session_token")
        return
    
    # Валидируем токен через БД
    async with async_session_factory() as db_session:
        try:
            session = await validate_session_token(db_session, session_token)
            if not session:
                await websocket.close(code=4002, reason="Invalid or expired session")
                return
            
            authenticated_user_id = session.user_id
            session_id_from_token = session.session_id
        except Exception as e:
            logger.error("Session validation error", extra={"error": str(e)})
            await websocket.close(code=4003, reason="Session validation failed")
            return
    
    # Только после успешной авторизации принимаем соединение
    await websocket.accept()
    
    semaphore = asyncio.Semaphore(WS_MAX_CONCURRENT_REQUESTS)
    rate_limiter = WebSocketRateLimiter(
        max_messages=WS_RATE_LIMIT_MESSAGES,
        window_seconds=WS_RATE_LIMIT_WINDOW
    )
    
    session_id = None

    try:
        while True:
            # Receive message
            data = await websocket.receive_text()
            message = json.loads(data)

            session_id = message.get("session_id")
            user_message = message.get("message")

            if not session_id or not user_message:
                await websocket.send_json({"error": "session_id and message required"})
                continue

            # === ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: session_id должен принадлежать авторизованному пользователю ===
            async with async_session_factory() as db_session:
                result = await db_session.execute(
                    select(ChatSession).where(
                        ChatSession.session_id == session_id,
                        ChatSession.user_id == authenticated_user_id
                    )
                )
                if not result.scalar_one_or_none():
                    await websocket.send_json({"error": "Session access denied"})
                    continue

            # RATE LIMITING: Проверка лимита сообщений 
            if not await rate_limiter.acquire():
                await websocket.send_json({
                    "error": "rate_limit_exceeded",
                    "detail": f"Maximum {WS_RATE_LIMIT_MESSAGES} messages per {WS_RATE_LIMIT_WINDOW} seconds",
                    "remaining": 0,
                    "reset_at": rate_limiter.get_reset_time(),
                })
                continue

            # Track connection
            active_connections[session_id] = websocket

            # SEMAPHORE: Блокировка параллельных запросов 
            async with semaphore:
                try:
                    # TIMEOUT: Защита от зависаний 
                    async with asyncio.timeout(WS_REQUEST_TIMEOUT):
                        async for chunk in route_request_stream(
                            session_id=session_id,
                            message=user_message,
                            user_id=str(authenticated_user_id),
                        ):
                            await websocket.send_json(chunk)
                
                except asyncio.TimeoutError:
                    logger.warning(
                        "Request timeout exceeded",
                        extra={
                            "session_id": session_id,
                            "timeout": WS_REQUEST_TIMEOUT,
                        }
                    )
                    await websocket.send_json({
                        "error": "request_timeout",
                        "detail": f"Request exceeded {WS_REQUEST_TIMEOUT} seconds timeout",
                    })
                
                except Exception as e:
                    logger.error(
                        "Request processing error",
                        extra={
                            "session_id": session_id,
                            "error": str(e),
                        }
                    )
                    await websocket.send_json({"error": str(e)})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected", extra={"session_id": session_id})
        if session_id and session_id in active_connections:
            del active_connections[session_id]
    except Exception as e:
        logger.error("WebSocket error", extra={"error": str(e)})
        await websocket.send_json({"error": str(e)})
    finally:
        # Очистка соединения при любом исходе
        if session_id and session_id in active_connections:
            del active_connections[session_id]


# ==================== DASHBOARD REAL-TIME ====================

async def collect_dashboard_data() -> dict | None:
    """Collect all analytics data for today (теперь полностью асинхронно)."""
    
    async with async_session_factory() as session:
        try:
            now = datetime.now(timezone.utc)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day.replace(hour=23, minute=59, second=59)

            # Используем ChatSession вместо конфликтующего Session
            total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
            total_sessions = (await session.execute(select(func.count(ChatSession.id)))).scalar() or 0
            total_conversations = (await session.execute(select(func.count(Conversation.id)))).scalar() or 0
            total_messages = (await session.execute(select(func.count(Message.id)))).scalar() or 0

            unique_users_today = (await session.execute(
                select(func.count(func.distinct(Conversation.user_id)))
                .filter(Conversation.created_at >= start_of_day, Conversation.created_at <= end_of_day)
            )).scalar() or 0

            hourly_messages_raw = (await session.execute(
                select(
                    func.extract('hour', Message.created_at).label('hour'),
                    func.count(Message.id).label('count')
                )
                .filter(Message.created_at >= start_of_day, Message.created_at <= end_of_day)
                .group_by(func.extract('hour', Message.created_at))
            )).all()

            messages_per_hour = {int(h): int(c) for h, c in hourly_messages_raw}

            avg_confidence = (await session.execute(
                select(func.avg(Message.confidence))
                .filter(Message.created_at >= start_of_day, Message.created_at <= end_of_day)
            )).scalar() or 0.0

            validated_count = (await session.execute(
                select(func.count(Message.id))
                .filter(
                    Message.created_at >= start_of_day,
                    Message.created_at <= end_of_day,
                    Message.validated == True
                )
            )).scalar() or 0

            agent_stats_raw = (await session.execute(
                select(
                    Conversation.agent_type,
                    func.count(func.distinct(Conversation.id)).label('conv_count'),
                    func.avg(Message.confidence).label('avg_conf'),
                )
                .join(Message, Conversation.id == Message.conversation_id)
                .filter(Conversation.created_at >= start_of_day, Conversation.created_at <= end_of_day)
                .group_by(Conversation.agent_type)
            )).all()

            agents = [
                {
                    "agent_type": row[0],
                    "conversation_count": row[1],
                    "avg_confidence": round(float(row[2]) if row[2] else 0.0, 4),
                }
                for row in agent_stats_raw
            ]

            return {
                "type": "dashboard_update",
                "data": {
                    "overview": {
                        "total_users": total_users,
                        "total_sessions": total_sessions,
                        "total_conversations": total_conversations,
                        "total_messages": total_messages,
                        "unique_users_today": unique_users_today,
                        "messages_per_hour": messages_per_hour,
                    },
                    "quality": {
                        "avg_confidence": round(float(avg_confidence), 4),
                        "validated_percentage": round(
                            (validated_count / total_messages * 100) if total_messages > 0 else 0.0, 2
                        ),
                    },
                    "agents": agents,
                    "timestamp": now.isoformat(),
                }
            }
        except Exception as e:
            logger.error("Error collecting dashboard data", error=str(e))
            return None


async def metrics_broadcaster_task(interval: int = 5):
    """Фоновый периодический воркер для обновления кэша метрик."""
    global _dashboard_cache
    while True:
        data = await collect_dashboard_data()
        if data:
            async with _cache_lock:
                _dashboard_cache = data
        await asyncio.sleep(interval)


@websocket_router.websocket("/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """
    WebSocket endpoint for real-time analytics dashboard updates.
    Reads metrics instantly from cache to protect DB from overload.
    """
    await websocket.accept()
    logger.info("Dashboard WebSocket connected")

    stop_event = asyncio.Event()

    async def listen_to_client():
        """Параллельное чтение входящих сообщений (команд) от клиента."""
        try:
            while not stop_event.is_set():
                data = await websocket.receive_text()
                if data.strip().lower() == "stop":
                    stop_event.set()
                    break
        except WebSocketDisconnect:
            stop_event.set()
        except Exception as e:
            logger.error("Dashboard WebSocket read error", error=str(e))
            stop_event.set()

    # Запуск корутины прослушивания клиента в фоне
    listen_task = asyncio.create_task(listen_to_client())

    try:
        while not stop_event.is_set():
            # Безопасное чтение данных из кэша
            async with _cache_lock:
                current_data = _dashboard_cache.copy() if _dashboard_cache else None

            if current_data:
                await websocket.send_json(current_data)

            # Частота отправки данных на фронтенд
            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket disconnected")
    except Exception as e:
        logger.error("Dashboard WebSocket loop error", error=str(e))
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        stop_event.set()
        listen_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass