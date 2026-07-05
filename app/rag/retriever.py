"""Vector-based document retriever for RAG."""

from typing import Optional, Any
from sentence_transformers import SentenceTransformer

# ИСПРАВЛЕНО: Добавлен импорт функции select
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Document
from app.db.database import AsyncSessionLocal
from app.config import settings


class DocumentRetriever:
    """
    Retrieves relevant documents from vector store.
    
    Uses pgvector for similarity search.
    """

    def __init__(self, collection_name: str = "knowledge_base"):
        self.collection_name = collection_name
        self._client = None
        self._embedding_model: Optional[SentenceTransformer] = None

    async def initialize(self):
        """Initialize vector store connection and embedding model."""
        # Initialize embedding model
        self._embedding_model = SentenceTransformer(settings.embedding_model)

    def _get_embedding(self, text_data: str) -> list[float]:
        """Generate embedding for text."""
        if self._embedding_model is None:
            raise RuntimeError("Retriever not initialized. Call initialize() first.")
        embedding = self._embedding_model.encode(text_data, convert_to_numpy=True)
        return embedding.tolist()

    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Search for relevant documents using pgvector similarity search.
        """
        if self._embedding_model is None:
            await self.initialize()

        # Generate query embedding
        query_embedding = self._get_embedding(query)

        async with AsyncSessionLocal() as session:
            # ИСПРАВЛЕНИЕ: Правильный формат для pgvector 
            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
            
            # Используем literal_column вместо text()
            # literal_column интегрируется с ORM, text() — нет
            from sqlalchemy import literal_column
            
            similarity_score = literal_column(
                "1 - (embedding <=> :embedding)"
            ).bindparams(embedding=embedding_str)
            
            stmt = (
                select(
                    Document,
                    similarity_score.label("similarity")
                )
                .where(
                    Document.embedding.isnot(None),
                    similarity_score >= score_threshold 
                )
                .order_by(similarity_score.desc())
                .limit(top_k)
            )
            
            result = await session.execute(stmt)
            
            documents = []
            for row in result.all():
                doc, similarity = row[0], row[1]
                documents.append({
                    "id": str(doc.id),
                    "title": doc.title,
                    "content": doc.content,
                    "metadata": doc.metadata_,
                    "similarity": float(similarity),
                    "created_at": doc.created_at.isoformat() if doc.created_at else None,
                })
            
            return documents

    async def add_document(
        self,
        content: str,
        metadata: dict | None = None,
        title: str = "Untitled",
    ) -> str:
        """Add document to vector store."""
        if self._embedding_model is None:
            await self.initialize()

        # Generate embedding for document content
        embedding = self._get_embedding(content)

        async with AsyncSessionLocal() as session:
            from uuid import uuid4
            
            doc = Document(
                id=uuid4(),
                title=title,
                content=content,
                # Если в модели Document тип колонки определен как Vector из pgvector,
                # передаем туда обычный list[float]
                embedding=embedding, 
                metadata_=metadata,
            )
            
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
            
            return str(doc.id)


# Global retriever instance
retriever = DocumentRetriever()