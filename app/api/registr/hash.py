import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError,InvalidHash
from jose import JWTError, jwt

ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16
)
executor = ThreadPoolExecutor()

async def hash_password(password:str) -> str:
    return await asyncio.get_event_loop().run_in_executor(
        executor,lambda:ph.hash(password)
        )

async def verify_password(password:str,hashed:str) -> bool:
    try:
        await asyncio.get_event_loop().run_in_executor(
            executor,lambda:ph.verify(hashed,password)
            )
        return True
    except VerifyMismatchError:
        return False

async def verify_password_with_rehash(password:str,hashed:str) -> Tuple[bool,Optional[str]]:
    """Проверяет пароль и возвращает новый хеш если требуется рехеш."""
    try:
        # Проверяем пароль
        await asyncio.get_event_loop().run_in_executor(
            executor, lambda: ph.verify(hashed, password)
        )
        
        # Проверяем, нужен ли рехеш (например, после изменения параметров)
        needs_update = ph.check_needs_rehash(hashed)
        new_hash = None
        if needs_update:
            new_hash = await asyncio.get_event_loop().run_in_executor(
                executor, lambda: ph.hash(password)
            )
        return True, new_hash
    except (VerifyMismatchError, InvalidHash):
        return False, None


async def shutdown_executor():
    executor.shutdown(wait=True)

async def create_reset_token(user_id: int, email: str, secret_key: str, expire_minutes: int = 60) -> str:
    """Создаёт токен для сброса пароля"""
    to_encode = {
        "sub": str(user_id),
        "email": email,
        "type": "reset",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    }
    return jwt.encode(to_encode, secret_key, algorithm="HS256")

async def verify_reset_token(token: str, secret_key: str) -> Optional[dict]:
    """Проверяет валидность токена сброса пароля"""
    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        if payload.get("type") != "reset":
            return None
        return payload
    except JWTError:
        return None