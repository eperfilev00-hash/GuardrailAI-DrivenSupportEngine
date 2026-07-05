
from datetime import datetime, timezone
from typing import List

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.router import route_request
from app.db.database import get_async_db
from app.db.models import Conversation, Message, Session as ChatSession, User
from app.cache import _cache_lock, _dashboard_cache
from app.guardrails.validator import validate_response

from app.dependencies.auth import get_current_user, CurrentUser

logger = structlog.get_logger()
router = APIRouter()

# ==================== PYDANTIC MODELS ====================

class SupportRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier")
    user_message: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(None, description="Optional user ID")
    metadata: dict | None = Field(None, description="Additional context")


class SupportResponse(BaseModel):
    session_id: str
    response: str
    agent_type: str
    confidence: float
    sources: List[str] = []
    validated: bool


class AnalyticsOverview(BaseModel):
    total_users: int
    total_sessions: int
    total_conversations: int
    total_messages: int
    unique_users_today: int
    messages_per_hour: dict


class AgentStats(BaseModel):
    agent_type: str
    conversation_count: int
    message_count: int
    avg_confidence: float
    validated_count: int
    unvalidated_count: int


class QualityMetrics(BaseModel):
    avg_confidence: float
    validated_percentage: float
    messages_with_sources: int
    messages_without_sources: int


class AnalyticsActivity(BaseModel):
    hour: int
    conversations: int
    messages: int


class AnalyticsDashboard(BaseModel):
    overview: AnalyticsOverview
    agents: List[AgentStats]
    quality: QualityMetrics
    activity: List[AnalyticsActivity]


# ==================== CORE ROUTES ====================

@router.post("/support", response_model=SupportResponse)
async def handle_support_request(
    request: SupportRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Обработка запроса в поддержку.
    
    SECURITY: Доступно только авторизованным пользователям 
    user_id автоматически берётся из сессии, игнорируется из запроса.
    """
    try:
        # ИСПОЛЬЗУЕМ USER_ID ИЗ АВТОРИЗОВАННОЙ СЕССИИ 
        agent_response = await route_request(
            session_id=request.session_id,
            message=request.user_message,
            user_id=str(current_user.id),  # Берём из сессии
            metadata=request.metadata,
        )

        validation_result = await validate_response(
            response=agent_response.response,
            context=agent_response.context,
        )

        if not validation_result.is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"Response failed validation: {validation_result.reason}",
            )

        # Найти или создать Conversation
        conversation = (await db.execute(
            select(Conversation).filter(Conversation.session_id == request.session_id)
        )).scalar_one_or_none()

        if not conversation:
            conversation = Conversation(
                session_id=request.session_id,
                user_id=str(current_user.id),  
                agent_type=agent_response.agent_type,
                metadata_=request.metadata,
            )
            db.add(conversation)
            await db.flush()

        # Сохранить сообщение пользователя
        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=request.user_message,
            validated=True,
        )
        db.add(user_message)

        # Сохранить ответ ассистента
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=agent_response.response,
            validated=validation_result.is_valid,
            confidence=agent_response.confidence,
            sources=agent_response.sources if agent_response.sources else None,
        )
        db.add(assistant_message)

        conversation.updated_at = datetime.utcnow()
        conversation.agent_type = agent_response.agent_type

        await db.commit()

        return SupportResponse(
            session_id=request.session_id,
            response=agent_response.response,
            agent_type=agent_response.agent_type,
            confidence=agent_response.confidence,
            sources=agent_response.sources,
            validated=validation_result.is_valid,
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Support request failed", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


class MessageHistory(BaseModel):
    """Отдельное сообщение в истории."""
    id: str
    role: str
    content: str
    validated: bool
    confidence: float | None = None
    sources: list | None = None
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    """Ответ с историей чата."""
    session_id: str
    conversation_id: str | None = None
    messages: List[MessageHistory]
    limit: int


class ClearHistoryResponse(BaseModel):
    """Ответ после очистки истории."""
    session_id: str
    cleared: bool
    messages_deleted: int


@router.get("/history/{session_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Получить историю чата для сессии.
    
    SECURITY: Пользователь может видеть только свои сессии 
    """
    # Найти Conversation по session_id
    conversation = (await db.execute(
        select(Conversation).filter(
            Conversation.session_id == session_id,
            # ПРОВЕРКА: session_id принадлежит текущему пользователю 
            Conversation.user_id == str(current_user.id)
        )
    )).scalar_one_or_none()

    if not conversation:
        # Возвращаем пустой ответ вместо 404 (не раскрываем существование чужих сессий)
        return ChatHistoryResponse(
            session_id=session_id,
            conversation_id=None,
            messages=[],
            limit=limit,
        )

    messages_result = (await db.execute(
        select(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )).scalars().all()

    messages = [
        MessageHistory(
            id=str(msg.id),
            role=msg.role,
            content=msg.content,
            validated=msg.validated,
            confidence=msg.confidence,
            sources=msg.sources,
            created_at=msg.created_at,
        )
        for msg in messages_result
    ]

    return ChatHistoryResponse(
        session_id=session_id,
        conversation_id=str(conversation.id),
        messages=messages,
        limit=limit,
    )


@router.post("/history/{session_id}/clear", response_model=ClearHistoryResponse)
async def clear_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Очистить историю чата для сессии.
    
    ECURITY: Пользователь может удалять только свои сессии 
    """
    conversation = (await db.execute(
        select(Conversation).filter(
            Conversation.session_id == session_id,
            # ПРОВЕРКА: session_id принадлежит текущему пользователю
            Conversation.user_id == str(current_user.id)
        )
    )).scalar_one_or_none()

    if not conversation:
        return ClearHistoryResponse(
            session_id=session_id,
            cleared=False,
            messages_deleted=0,
        )

    messages_count = (await db.execute(
        select(func.count(Message.id)).filter(Message.conversation_id == conversation.id)
    )).scalar() or 0

    await db.delete(conversation)
    await db.commit()

    return ClearHistoryResponse(
        session_id=session_id,
        cleared=True,
        messages_deleted=messages_count,
    )


# ==================== ASYNC ANALYTICS ROUTES ====================

@router.get("/analytics/overview", response_model=AnalyticsOverview)
async def get_analytics_overview(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get overview statistics for the current day."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day.replace(hour=23, minute=59, second=59)

    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_sessions = (await db.execute(select(func.count(ChatSession.id)))).scalar() or 0
    total_conversations = (await db.execute(select(func.count(Conversation.id)))).scalar() or 0
    total_messages = (await db.execute(select(func.count(Message.id)))).scalar() or 0

    unique_users_today = (await db.execute(
        select(func.count(func.distinct(Conversation.user_id))).filter(
            Conversation.created_at >= start_of_day,
            Conversation.created_at <= end_of_day
        )
    )).scalar() or 0

    hourly_messages = (await db.execute(
        select(
            extract('hour', Message.created_at).label('hour'),
            func.count(Message.id).label('count')
        ).filter(
            Message.created_at >= start_of_day,
            Message.created_at <= end_of_day
        ).group_by(extract('hour', Message.created_at))
    )).all()

    messages_per_hour = {int(h): int(c) for h, c in hourly_messages}

    return AnalyticsOverview(
        total_users=total_users,
        total_sessions=total_sessions,
        total_conversations=total_conversations,
        total_messages=total_messages,
        unique_users_today=unique_users_today,
        messages_per_hour=messages_per_hour,
    )


@router.get("/analytics/agents", response_model=List[AgentStats])
async def get_analytics_agents(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get statistics grouped by agent type for the current day."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day.replace(hour=23, minute=59, second=59)

    agent_stats = (await db.execute(
        select(
            Conversation.agent_type,
            func.count(func.distinct(Conversation.id)).label('conv_count'),
            func.count(Message.id).label('msg_count'),
            func.avg(Message.confidence).label('avg_conf'),
            func.sum(func.case((Message.validated == True, 1), else_=0)).label('validated'),
            func.sum(func.case((Message.validated == False, 1), else_=0)).label('unvalidated'),
        ).join(
            Message, Conversation.id == Message.conversation_id
        ).filter(
            Conversation.created_at >= start_of_day,
            Conversation.created_at <= end_of_day
        ).group_by(Conversation.agent_type)
    )).all()

    result = []
    for row in agent_stats:
        result.append(AgentStats(
            agent_type=row[0],
            conversation_count=row[1],
            message_count=row[2] or 0,
            avg_confidence=round(float(row[3]) if row[3] else 0.0, 4),
            validated_count=row[4] or 0,
            unvalidated_count=row[5] or 0,
        ))

    return result


@router.get("/analytics/quality", response_model=QualityMetrics)
async def get_analytics_quality(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get quality metrics for today's messages."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day.replace(hour=23, minute=59, second=59)

    total_messages = (await db.execute(
        select(func.count(Message.id)).filter(
            Message.created_at >= start_of_day,
            Message.created_at <= end_of_day
        )
    )).scalar() or 0

    if total_messages == 0:
        return QualityMetrics(
            avg_confidence=0.0,
            validated_percentage=0.0,
            messages_with_sources=0,
            messages_without_sources=0,
        )

    avg_confidence = (await db.execute(
        select(func.avg(Message.confidence)).filter(
            Message.created_at >= start_of_day,
            Message.created_at <= end_of_day
        )
    )).scalar() or 0.0

    validated_count = (await db.execute(
        select(func.count(Message.id)).filter(
            Message.created_at >= start_of_day,
            Message.created_at <= end_of_day,
            Message.validated == True
        )
    )).scalar() or 0

    with_sources = (await db.execute(
        select(func.count(Message.id)).filter(
            Message.created_at >= start_of_day,
            Message.created_at <= end_of_day,
            Message.sources.isnot(None),
            func.json_b_array_length(Message.sources) > 0 if hasattr(Message.sources, 'astext') else Message.sources != []
        )
    )).scalar() or 0

    return QualityMetrics(
        avg_confidence=round(float(avg_confidence), 4),
        validated_percentage=round((validated_count / total_messages) * 100, 2),
        messages_with_sources=with_sources,
        messages_without_sources=total_messages - with_sources,
    )


@router.get("/analytics/activity", response_model=List[AnalyticsActivity])
async def get_analytics_activity(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get hourly conversation activity for the current day."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day.replace(hour=23, minute=59, second=59)

    conversations = (await db.execute(
        select(
            extract('hour', Conversation.created_at).label('hour'),
            func.count(Conversation.id).label('count')
        ).filter(
            Conversation.created_at >= start_of_day,
            Conversation.created_at <= end_of_day
        ).group_by(extract('hour', Conversation.created_at))
    )).all()

    messages = (await db.execute(
        select(
            extract('hour', Message.created_at).label('hour'),
            func.count(Message.id).label('count')
        ).filter(
            Message.created_at >= start_of_day,
            Message.created_at <= end_of_day
        ).group_by(extract('hour', Message.created_at))
    )).all()

    conv_dict = {int(h): int(c) for h, c in conversations}
    msg_dict = {int(h): int(c) for h, c in messages}

    result = []
    for hour in range(24):
        result.append(AnalyticsActivity(
            hour=hour,
            conversations=conv_dict.get(hour, 0),
            messages=msg_dict.get(hour, 0),
        ))

    return result


@router.get("/analytics/dashboard", response_model=AnalyticsDashboard)
async def get_analytics_dashboard(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Get complete dashboard data in one request.
    Использует глобальный кэш фонового воркера для моментального ответа.
    """
    async with _cache_lock:
        cached_data = _dashboard_cache.copy() if _dashboard_cache else None

    if cached_data and "data" in cached_data:
        d = cached_data["data"]
        return AnalyticsDashboard(
            overview=AnalyticsOverview(**d["overview"]),
            agents=[AgentStats(**a) for a in d["agents"]],
            quality=QualityMetrics(**d["quality"]),
            activity=await get_analytics_activity(db)
        )

    logger.warning("Dashboard cache miss. Fetching data directly from DB.")
    overview = await get_analytics_overview(db)
    agents = await get_analytics_agents(db)
    quality = await get_analytics_quality(db)
    activity = await get_analytics_activity(db)

    return AnalyticsDashboard(
        overview=overview,
        agents=agents,
        quality=quality,
        activity=activity,
    )


# ==================== PUBLIC ENDPOINTS (без авторизации) ====================

@router.get("/health")
async def health_check():
    """
    Public health check endpoint.
    
    """
    return {"status": "healthy", "environment": "production"}


@router.get("/me", response_model=CurrentUser)
async def get_current_user_info(
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Get current user information.
    
    Требует авторизации 
    """
    return current_user