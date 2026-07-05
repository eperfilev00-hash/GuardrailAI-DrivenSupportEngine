"""SQLAlchemy database models."""

from datetime import datetime
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from sqlalchemy import TIMESTAMP, Enum, Integer, String, Text, Float, Boolean, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    """User model."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer,primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String,nullable=False)
    email: Mapped[str] = mapped_column(unique=True,nullable=False)
    hashed_password: Mapped[str] = mapped_column(String,nullable=False)

class Session(Base):
    __tablename__ = "sessions"
    
    id: Mapped[int] = mapped_column(primary_key=True,autoincrement=True,nullable=False)
    session_id: Mapped[str] = mapped_column(String(255),unique=True,index=True,nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    ) 
    expires_at:Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True),nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45),nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255),nullable=True)

class Conversation(Base):
    """Conversation session model."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    metadata_: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_conversations_user", "user_id"),
        Index("idx_conversations_created", "created_at"),
    )


class Message(Base):
    """Individual message model."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user, assistant, system
    content: Mapped[str] = mapped_column(Text)
    validated: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Document(Base):
    """Knowledge base document for RAG."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(nullable=True)  # pgvector
    metadata_: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class OrderStatus(StrEnum):
    """Order status enumeration."""
    pending = "pending"
    confirmed = "confirmed"
    processing = "processing"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class ReturnStatus(StrEnum):
    """Return status enumeration."""
    requested = "requested"
    approved = "approved"
    rejected = "rejected"
    received = "received"
    refunded = "refunded"

class Order(Base):
    """Order model."""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus,native_enum=False), default=OrderStatus.pending)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    items: Mapped[dict] = mapped_column(JSONB, nullable=False)  # Список товаров
    shipping_address: Mapped[str] = mapped_column(Text, nullable=True)
    tracking_number: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Связь с возвратами
    returns = relationship("Return", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_orders_user_status", "user_id", "status"),
    )


class Return(Base):
    """Return/Refund model."""
    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[ReturnStatus] = mapped_column(Enum(ReturnStatus,native_enum=False), default=ReturnStatus.requested)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    items: Mapped[dict] = mapped_column(JSONB, nullable=False)  # Возвращаемые товары
    refund_amount: Mapped[float] = mapped_column(Float, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=True)  # Комментарий менеджера
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = relationship("Order", back_populates="returns")

    __table_args__ = (
        Index("idx_returns_user_status", "user_id", "status"),
    )