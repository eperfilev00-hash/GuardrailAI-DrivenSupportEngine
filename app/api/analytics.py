import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Conversation, Message, Session, User

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

websocket_router = APIRouter()

# 1. НАСТРОЙКА БАЗЫ ДАННЫХ (Обязательно асинхронный драйвер, например, asyncpg)
DATABASE_URL = "postgresql+asyncpg://user:password@localhost/dbname"
async_engine = create_async_engine(DATABASE_URL, echo=False)

# Фабрика асинхронных сессий

# Ваш асинхронный движок
async_engine = create_async_engine(DATABASE_URL, echo=False)

# Идеальный и современный вариант без ошибок типизации:
async_session_factory = async_sessionmaker(
    bind=async_engine, 
    expire_on_commit=False
)

# 2. ГЛОБАЛЬНЫЙ КЭШ ДЛЯ ЗАЩИТЫ БД ОТ НАГРУЗКИ
_dashboard_cache = {}
_cache_lock = asyncio.Lock()


async def collect_dashboard_data() -> dict | None:
    """Собирает всю аналитику за сегодня. 
    Использует современный синтаксис SQLAlchemy 2.0 (select вместо query).
    """
    async with async_session_factory() as session:
        try:
            now = datetime.now(timezone.utc)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day.replace(hour=23, minute=59, second=59)

            # Общая статистика (выполняем асинхронно через session.execute)
            total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
            total_sessions = (await session.execute(select(func.count(Session.id)))).scalar() or 0
            total_conversations = (await session.execute(select(func.count(Conversation.id)))).scalar() or 0
            total_messages = (await session.execute(select(func.count(Message.id)))).scalar() or 0

            # Уникальные пользователи за сегодня
            unique_users_today_stmt = (
                select(func.count(func.distinct(Conversation.user_id)))
                .filter(Conversation.created_at >= start_of_day, Conversation.created_at <= end_of_day)
            )
            unique_users_today = (await session.execute(unique_users_today_stmt)).scalar() or 0

            # Почасовая статистика сообщений
            hourly_messages_stmt = (
                select(
                    func.extract('hour', Message.created_at).label('hour'), 
                    func.count(Message.id).label('count')
                )
                .filter(Message.created_at >= start_of_day, Message.created_at <= end_of_day)
                .group_by(func.extract('hour', Message.created_at))
            )
            hourly_messages_raw = (await session.execute(hourly_messages_stmt)).all()
            messages_per_hour = {int(h): int(c) for h, c in hourly_messages_raw}

            # Средняя уверенность (confidence)
            avg_confidence_stmt = (
                select(func.avg(Message.confidence))
                .filter(Message.created_at >= start_of_day, Message.created_at <= end_of_day)
            )
            avg_confidence = (await session.execute(avg_confidence_stmt)).scalar() or 0.0

            # Валидированные сообщения
            validated_stmt = (
                select(func.count(Message.id))
                .filter(
                    Message.created_at >= start_of_day, 
                    Message.created_at <= end_of_day, 
                    Message.validated == True
                )
            )
            validated_count = (await session.execute(validated_stmt)).scalar() or 0

            # Статистика по агентам
            agent_stats_stmt = (
                select(
                    Conversation.agent_type, 
                    func.count(func.distinct(Conversation.id)).label('conv_count'), 
                    func.avg(Message.confidence).label('avg_conf')
                )
                .join(Message, Conversation.id == Message.conversation_id)
                .filter(Conversation.created_at >= start_of_day, Conversation.created_at <= end_of_day)
                .group_by(Conversation.agent_type)
            )
            agent_stats_raw = (await session.execute(agent_stats_stmt)).all()

            agents = [
                {
                    "agent_type": row[0],
                    "conversation_count": row[1],
                    "avg_confidence": round(float(row[2]) if row[2] else 0.0, 4),
                }
                for row in agent_stats_raw
            ]

            # Формируем итоговый JSON-ответ
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
            logger.error(f"Ошибка при сборе данных для дашборда: {e}")
            return None


async def metrics_broadcaster_task(interval: int = 5):
    """Фоновый периодический воркер. 
    Раз в `interval` секунд делает один запрос в БД и обновляет кэш в памяти.
    """
    global _dashboard_cache
    logger.info("Запущен фоновый сборщик метрик дашборда.")
    while True:
        data = await collect_dashboard_data()
        if data:
            async with _cache_lock:
                _dashboard_cache = data
        await asyncio.sleep(interval)


@websocket_router.websocket("/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """Эндпоинт Веб-сокета. Читает данные ТОЛЬКО из памяти (`_dashboard_cache`).
    Обрабатывает команду 'stop' от клиента в реальном времени.
    """
    await websocket.accept()
    logger.info("Клиент подключился к Dashboard WebSocket")
    
    stop_event = asyncio.Event()

    async def listen_to_client():
        """Корутина для параллельного чтения сообщений от клиента."""
        try:
            while not stop_event.is_set():
                data = await websocket.receive_text()
                if data.strip().lower() == "stop":
                    logger.info("Получена команда 'stop' от клиента. Закрываем соединение.")
                    stop_event.set()
                    break
        except WebSocketDisconnect:
            logger.info("Клиент разорвал соединение (разрядка веб-сокета)")
            stop_event.set()
        except Exception as e:
            logger.error(f"Ошибка при чтении из веб-сокета: {e}")
            stop_event.set()

    # Запускаем чтение сообщений от клиента в фоновом таске
    listen_task = asyncio.create_task(listen_to_client())

    try:
        while not stop_event.is_set():
            # Безопасно берем копию данных из кэша
            async with _cache_lock:
                current_data = _dashboard_cache.copy() if _dashboard_cache else None

            if current_data:
                await websocket.send_json(current_data)
            
            # Интервал отправки обновлений на фронтенд (1 секунда)
            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Ошибка в цикле отправки веб-сокета: {e}")
    finally:
        # Корректно подчищаем ресурсы при выходе
        stop_event.set()
        listen_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("Сессия Dashboard WebSocket полностью завершена")


# 3. ПРИМЕР ИНТЕГРАЦИИ В СТАРТ ПРИЛОЖЕНИЯ FASTAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # При старте приложения запускаем фоновый сборщик метрик (раз в 5 секунд)
    broadcaster_task = asyncio.create_task(metrics_broadcaster_task(interval=5))
    yield
    # При остановке приложения отменяем таск
    broadcaster_task.cancel()

app = FastAPI(lifespan=lifespan)
app.include_router(websocket_router)