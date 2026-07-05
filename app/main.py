"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import router as api_router
from app.api.websocket import websocket_router
from app.api.registr.auth import auth_router
from app.db.database import init_db
from app.rag.retriever import retriever 
from app.middleware.rate_limiter import RateLimitMiddleware
from app.guardrails.validator import initialize_guardrails

from app.logging.pii_processor import mask_pii_processor

from app.api.cache.redis_client import redis_client

# Configure structlog with PII masking
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        mask_pii_processor,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting up", app_name=settings.app_name)
    
    # Инициализация БД
    await init_db()
    logger.info("Database initialized")
    
    # ИНИЦИАЛИЗАЦИЯ REDIS 
    await redis_client.connect()
    logger.info("Redis connected")
    
    # Инициализация RAG
    await retriever.initialize()
    logger.info("RAG retriever initialized")
        
    # Инициализация Guardrails
    await initialize_guardrails()
    logger.info("Guardrails initialized")

    yield

    # Shutdown
    logger.info("Shutting down")
    
    # ЗАКРЫТИЕ REDIS 
    await redis_client.close()
    logger.info("Redis connection closed")


app = FastAPI(
    title=settings.app_name,
    description="Multi-Agent AI Customer Support System with Guardrails",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimitMiddleware, redis_url=settings.redis_url)

# Include routers
app.include_router(api_router, prefix="/api/v1", tags=["API"])
app.include_router(websocket_router, prefix="/ws", tags=["WebSocket"])
app.include_router(auth_router, prefix='/api/v1')


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "environment": settings.app_env}