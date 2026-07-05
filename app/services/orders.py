from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.db.models import Order, Return, OrderStatus, ReturnStatus
from app.schemas.order import OrderDTO, ReturnDTO
from app.schemas.validators import safe_json_loads, JSONDepthError, JSONSizeError


class OrderService:
    """Service for order operations."""

    @classmethod
    async def get_by_order_number(cls, order_number: str) -> Optional[OrderDTO]:
        """Get order by order number. Возвращает DTO, а не ORM-объект."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Order).where(Order.order_number == order_number)
            )
            order = result.scalar_one_or_none()
            return OrderDTO.model_validate(order) if order else None

    @classmethod
    async def get_by_order_number_with_auth(
        cls, order_number: str, user_id: int
    ) -> tuple[Optional[OrderDTO], bool]:
        """
        Получить заказ с проверкой прав доступа.
        
        Returns:
            Tuple of (order, has_access)
            - order: найденный заказ или None
            - has_access: True если пользователь является владельцем заказа
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Order).where(Order.order_number == order_number)
            )
            order = result.scalar_one_or_none()
            
            if order is None:
                return None, False
            
            # Проверяем, что пользователь является владельцем заказа
            has_access = order.user_id == user_id
            order_dto = OrderDTO.model_validate(order) if order else None
            
            return order_dto, has_access

    @classmethod
    async def get_by_user_id(cls, user_id: int) -> list[OrderDTO]:
        """Get all orders for a user. Возвращает список DTO."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Order)
                .where(Order.user_id == user_id)
                .order_by(Order.created_at.desc())
            )
            orders = list(result.scalars().all())
            return [OrderDTO.model_validate(o) for o in orders]

    @classmethod
    async def get_status(cls, order_number: str) -> Optional[str]:
        """Get order status by order number."""
        order = await cls.get_by_order_number(order_number)
        return order.status if order else None


class ReturnService:
    """Service for return operations."""

    @classmethod
    async def get_by_return_number(cls, return_number: str) -> Optional[ReturnDTO]:
        """Get return by return number. Возвращает DTO."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Return).where(Return.return_number == return_number)
            )
            return_obj = result.scalar_one_or_none()
            return ReturnDTO.model_validate(return_obj) if return_obj else None

    @classmethod
    async def get_by_return_number_with_auth(
        cls, return_number: str, user_id: int
    ) -> tuple[Optional[ReturnDTO], bool]:
        """
        Получить возврат с проверкой прав доступа.
        
        Returns:
            Tuple of (return_obj, has_access)
            - return_obj: найденный возврат или None
            - has_access: True если пользователь является владельцем возврата
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Return).where(Return.return_number == return_number)
            )
            return_obj = result.scalar_one_or_none()
            
            if return_obj is None:
                return None, False
            
            # Проверяем, что пользователь является владельцем возврата
            has_access = return_obj.user_id == user_id
            return_dto = ReturnDTO.model_validate(return_obj) if return_obj else None
            
            return return_dto, has_access

    @classmethod
    async def get_by_order_id(cls, order_id: int) -> list[ReturnDTO]:
        """Get all returns for an order. Возвращает список DTO."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Return)
                .where(Return.order_id == order_id)
                .order_by(Return.created_at.desc())
            )
            returns = list(result.scalars().all())
            return [ReturnDTO.model_validate(r) for r in returns]

    @classmethod
    async def get_by_user_id(cls, user_id: int) -> list[ReturnDTO]:
        """Get all returns for a user. Возвращает список DTO."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Return)
                .where(Return.user_id == user_id)
                .order_by(Return.created_at.desc())
            )
            returns = list(result.scalars().all())
            return [ReturnDTO.model_validate(r) for r in returns]

    @classmethod
    async def create_return(
        cls,
        order_id: int,
        user_id: int,
        reason: str,
        items: dict,
        refund_amount: float,
    ) -> ReturnDTO:
        """
        Create a new return request.
        
        БЕЗОПАСНОСТЬ: Валидация items перед сохранлением 
        """
        from uuid import uuid4
        return_number = f"RET-{uuid4().hex[:8].upper()}"

        # ВАЛИДАЦИЯ ITEMS 
        try:
            validated_items = safe_json_loads(items)
        except (JSONDepthError, JSONSizeError, ValueError) as e:
            # Логгируем попытку подозрительного ввода
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "Malicious items structure rejected",
                extra={
                    "user_id": user_id,
                    "order_id": order_id,
                    "error": str(e),
                }
            )
            raise ValueError(f"Invalid items structure: {e}")

        async with AsyncSessionLocal() as session:
            return_obj = Return(
                return_number=return_number,
                order_id=order_id,
                user_id=user_id,
                reason=reason,
                items=validated_items,  # Используем валидированные данные
                refund_amount=refund_amount,
                status=ReturnStatus.requested,
            )
            session.add(return_obj)
            await session.commit()
            await session.refresh(return_obj)
            return ReturnDTO.model_validate(return_obj)