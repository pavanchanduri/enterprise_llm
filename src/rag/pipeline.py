"""
RAG module — document ingestion, chunking, embedding, and retrieval.

Usage:
    from src.rag.pipeline import RAGPipeline
    rag = RAGPipeline()
    rag.ingest_document(title="My Doc", content="...", source="file.md")
    results = rag.retrieve("How does X work?")
"""

import hashlib
import json
import os
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from src.utils.config import CONFIG


class RAGPipeline:
    """
    Complete RAG pipeline: ingest, chunk, embed, store, and retrieve.
    Uses ChromaDB for vector storage and sentence-transformers for embeddings.
    """

    def __init__(self):
        rag_config = CONFIG.get("rag", {})
        self.chunk_size = rag_config.get("chunk_size", 500)
        self.chunk_overlap = rag_config.get("chunk_overlap", 50)
        self.top_k = rag_config.get("top_k", 5)
        self.similarity_threshold = rag_config.get("similarity_threshold", 0.3)

        db_path = CONFIG["paths"].get("data_dir", "./data") + "/chromadb"
        collection_name = rag_config.get("collection_name", "knowledge_base")

        # Initialize embedding model
        model_name = CONFIG.get("model", {}).get("embedding_model", "all-MiniLM-L6-v2")
        print(f"  Loading embedding model: {model_name}...")
        self.embedding_model = SentenceTransformer(model_name)

        # Initialize ChromaDB
        os.makedirs(db_path, exist_ok=True)
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"  Vector DB ready: {self.collection.count()} chunks stored")

    def chunk_text(self, text):
        """Split text into overlapping chunks preserving paragraph boundaries."""
        text = text.strip()
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        chunks = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) > self.chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                if self.chunk_overlap > 0 and len(current_chunk) > self.chunk_overlap:
                    current_chunk = current_chunk[-self.chunk_overlap:] + "\n\n" + para
                else:
                    current_chunk = para
            else:
                current_chunk = current_chunk + "\n\n" + para if current_chunk else para

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def ingest_document(self, title, content, source="unknown", category="general"):
        """
        Add a document to the vector database.

        Args:
            title: Document title
            content: Full text content
            source: Source file path or URL
            category: Document category for filtering
        """
        chunks = self.chunk_text(content)
        embeddings = self.embedding_model.encode(chunks).tolist()

        ids = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(f"{source}_{i}_{chunk[:50]}".encode()).hexdigest()
            ids.append(chunk_id)
            metadatas.append({
                "title": title,
                "source": source,
                "category": category,
                "chunk_index": i,
                "total_chunks": len(chunks),
            })

        self.collection.add(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        print(f"  ✅ Ingested '{title}' → {len(chunks)} chunks (total: {self.collection.count()})")
        return len(chunks)

    def ingest_file(self, file_path, category="general"):
        """Ingest a text/markdown file from disk."""
        path = Path(file_path)
        if not path.exists():
            print(f"  ❌ File not found: {file_path}")
            return 0

        content = path.read_text(encoding="utf-8")
        title = path.stem.replace("_", " ").replace("-", " ").title()
        return self.ingest_document(title=title, content=content, source=str(path), category=category)

    def ingest_directory(self, dir_path, extensions=(".txt", ".md"), category="general"):
        """Ingest all text files from a directory."""
        dir_path = Path(dir_path)
        total = 0
        for ext in extensions:
            for file in dir_path.glob(f"*{ext}"):
                total += self.ingest_file(file, category=category)
        return total

    def retrieve(self, query, top_k=None):
        """
        Search for documents relevant to the query.

        Args:
            query: The search query
            top_k: Number of results (uses config default if None)

        Returns:
            List of dicts with 'text', 'title', 'source', 'score'
        """
        if top_k is None:
            top_k = self.top_k

        query_embedding = self.embedding_model.encode(query).tolist()

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

        retrieved = []
        for i in range(len(results['documents'][0])):
            score = 1 - results['distances'][0][i]
            if score >= self.similarity_threshold:
                retrieved.append({
                    "text": results['documents'][0][i],
                    "title": results['metadatas'][0][i]['title'],
                    "source": results['metadatas'][0][i]['source'],
                    "category": results['metadatas'][0][i].get('category', 'unknown'),
                    "score": score,
                })

        return retrieved

    def get_stats(self):
        """Return database statistics."""
        return {
            "total_chunks": self.collection.count(),
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "embedding_model": CONFIG.get("model", {}).get("embedding_model", "unknown"),
        }

    def clear(self):
        """Delete all documents from the database."""
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection.name,
            metadata={"hnsw:space": "cosine"},
        )
        print("  Vector database cleared")
