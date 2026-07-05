"""Authentication dependencies with Redis-backed session validation.

Fast session validation (1-2ms) without hitting PostgreSQL.
"""

import logging
from datetime import datetime, timedelta, timezone 
from typing import Optional

from fastapi import Depends, Request, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.registr.hash import hash_password
from app.db.database import get_db
from app.db.models import User, Session  
from app.services.session_store import get_session, SessionData, store_session

logger = logging.getLogger(__name__)


class CurrentUser(BaseModel):
    """Данные текущего авторизованного пользователя."""
    id: int
    username: str
    email: str


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> CurrentUser:
    """
    Получает текущего пользователя из сессии.
    """
    session_id = request.cookies.get("session_id")
    
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session cookie not found",
            headers={"WWW-Authenticate": "Cookie"},
        )
    
    hashed_session_id = await hash_password(session_id)
    
    # 1. ПРОВЕРКА В REDIS (БЫСТРО)
    session_data = await get_session(hashed_session_id)
    
    if session_data:
        return CurrentUser(
            id=session_data.user_id,
            username=session_data.username,
            email=session_data.email,
        )
    
    # 2. CACHE-MISS: ПРОВЕРКА В POSTGRESQL
    logger.debug("Session cache-miss, checking PostgreSQL")
    
    result = await db.execute(
        select(User)
        .join(Session)
        .where(
            Session.session_id == hashed_session_id,
            Session.expires_at > datetime.now(timezone.utc)  
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Cookie"},
        )
    
    # 3. ВОССТАНАВЛИВАЕМ КЭШ В REDIS
    session_data = SessionData(
        user_id=user.id,
        username=user.username,
        email=user.email,
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    await store_session(hashed_session_id, session_data)
    
    return CurrentUser(
        id=user.id,
        username=user.username,
        email=user.email,
    )


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Optional[CurrentUser]:
    """
    Получает текущего пользователя, возвращает None если не авторизован.
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None