from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status, Request 
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr

from app.api.registr.hash import hash_password, verify_password
from app.db.database import get_db
from app.db.models import Session, User

from app.services.session_store import store_session, delete_session, SessionData

auth_router = APIRouter(tags=['Аутентификация'])

class RegistrationRequest(BaseModel):
    username: str
    email: EmailStr
    password: str 

@auth_router.post('/registration', status_code=status.HTTP_201_CREATED)
async def registration(data: RegistrationRequest, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == data.email))
    if user:
        return {"message": "Пользователь уже существует"}
    hashed_password = await hash_password(data.password)

    new_user = User(
        username=data.username,
        email=data.email,
        hashed_password=hashed_password
    )
    db.add(new_user)
    await db.commit()
    return {"message": "Пользователь успешно создан"}

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr

@auth_router.post('/login', status_code=status.HTTP_200_OK, response_model=UserResponse)
async def login(
    data: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Неверный email или пароль'  
        )
    
    if not await verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Неверный email или пароль'  
        )
        
    user_id = user.id
    user_username = user.username
    user_email = user.email

    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    hashed_session_id = await hash_password(session_id)

    session = Session(
        session_id=hashed_session_id,
        user_id=user.id,
        expires_at=expires_at,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    db.add(session)
    await db.commit()

    session_data = SessionData(
        user_id=user.id,
        username=user.username,
        email=user.email,
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at.isoformat(),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await store_session(hashed_session_id, session_data)

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=604800,
        path="/"
    )
    
    return UserResponse(
        id=user_id,
        username=user_username,
        email=user_email,
    )


@auth_router.post('/logout', status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    response: Response,
):
    session_id = request.cookies.get("session_id")
    
    if session_id:
        hashed_session_id = await hash_password(session_id)
        await delete_session(hashed_session_id)
    
    response.delete_cookie(key="session_id", path="/")
    
    return {"message": "Успешный выход"}