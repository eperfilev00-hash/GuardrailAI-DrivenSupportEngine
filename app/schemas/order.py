# app/schemas/order.py
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator

from app.schemas.validators import validate_json_depth



class OrderBase(BaseModel):
    order_number: str = Field(..., min_length=1, max_length=64)
    status: str = Field(..., min_length=1, max_length=32)
    total_amount: float = Field(..., ge=0)  # Не может быть отрицательным
    items: dict = Field(..., description="Items must pass depth/size validation")
    shipping_address: Optional[str] = Field(None, max_length=500)
    tracking_number: Optional[str] = Field(None, max_length=128)

    @field_validator("items")
    @classmethod
    def validate_items_depth(cls, v: dict) -> dict:
        """Validate JSON depth and size to prevent DoS attacks."""
        try:
            return validate_json_depth(v)
        except (ValueError, RecursionError) as e:
            raise ValueError(f"Invalid items structure: {e}")


class OrderDTO(OrderBase):
    """DTO для передачи данных заказа между слоями."""
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReturnBase(BaseModel):
    return_number: str = Field(..., min_length=1, max_length=64)
    status: str = Field(..., min_length=1, max_length=32)
    reason: str = Field(..., min_length=1, max_length=2000)
    items: dict = Field(..., description="Items must pass depth/size validation")
    refund_amount: float = Field(..., ge=0)  
    comment: Optional[str] = Field(None, max_length=2000)

    @field_validator("items")
    @classmethod
    def validate_items_depth(cls, v: dict) -> dict:
        """Validate JSON depth and size to prevent DoS attacks."""
        try:
            return validate_json_depth(v)
        except (ValueError, RecursionError) as e:
            raise ValueError(f"Invalid items structure: {e}")


class ReturnDTO(ReturnBase):
    """DTO для передачи данных возврата между слоями."""
    id: int
    order_id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True