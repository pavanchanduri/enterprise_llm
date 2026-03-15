"""
═══════════════════════════════════════════════════════════════
Enterprise LLM — REST API Server
═══════════════════════════════════════════════════════════════
A FastAPI server that exposes your Enterprise LLM as an HTTP API.

Endpoints:
  POST /ask          — Ask a question (full pipeline: security + RAG + reasoning)
  POST /ask/simple   — Ask without RAG (reasoning only)
  POST /ingest       — Add a document to the knowledge base
  GET  /health       — Health check
  GET  /stats        — System statistics
  GET  /sources      — List all documents in the knowledge base

Start the server:
  python -m src.api.server

Then call it:
  curl -X POST http://localhost:8000/ask \
    -H "Content-Type: application/json" \
    -d '{"question": "What is a B-Tree index?"}'
═══════════════════════════════════════════════════════════════
"""

import json
import time
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
 
from src.inference.model import ReasoningModel, SYSTEM_PROMPT, RAG_SYSTEM_PROMPT, clean_latex, parse_response
from src.rag.pipeline import RAGPipeline
from src.security.middleware import SecurityMiddleware
 
import requests as http_requests
 
app = FastAPI(
    title="Enterprise LLM API",
    description="Production-ready LLM with reasoning, RAG, security, streaming, and conversation history",
    version="2.0.0",
)
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
WEB_DIR = Path(__file__).parent.parent / "web"
 
model = None
rag = None
security = None
 
 
@app.on_event("startup")
async def startup():
    global model, rag, security
 
    print("\n" + "=" * 50)
    print("  Enterprise LLM API v2 — Starting Up")
    print("  Streaming + Conversation History")
    print("=" * 50)
 
    print("\n[1/3] Loading model...")
    model = ReasoningModel(model_name="enterprise-llm")
 
    print("\n[2/3] Loading RAG pipeline...")
    rag = RAGPipeline()
 
    print("\n[3/3] Loading security...")
    security = SecurityMiddleware()
 
    print("\n" + "=" * 50)
    print(f"  API ready at http://localhost:8000")
    print(f"  Docs at http://localhost:8000/docs")
    print("=" * 50 + "\n")
 
 
# ═══════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════
 
class Message(BaseModel):
    role: str
    content: str
 
class AskRequest(BaseModel):
    question: str
    user_id: str = "default"
    top_k: int = 5
    temperature: float = 0.7
    show_thinking: bool = True
    history: List[Message] = []
 
class AskResponse(BaseModel):
    answer: str
    thinking: Optional[str] = None
    sources: list = []
    security: dict = {}
    stats: dict = {}
 
class IngestRequest(BaseModel):
    title: str
    content: str
    source: str = "api_upload"
    category: str = "general"
 
class IngestResponse(BaseModel):
    success: bool
    chunks_created: int
    total_chunks: int
 
 
# ═══════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════
 
@app.get("/", response_class=HTMLResponse)
async def web_ui():
    html_path = WEB_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text()
    return "<h1>Web UI not found.</h1>"
 
 
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "model": model is not None,
        "rag": rag is not None,
        "security": security is not None,
        "chunks_in_db": rag.get_stats()["total_chunks"] if rag else 0,
    }
 
 
@app.get("/stats")
async def get_stats():
    return {
        "rag": rag.get_stats() if rag else {},
        "audit": security.audit.get_stats() if security else {},
    }
 
 
@app.get("/sources")
async def list_sources():
    if not rag:
        return {"sources": []}
    all_data = rag.collection.get()
    titles = set()
    for meta in all_data["metadatas"]:
        titles.add(meta.get("title", "Unknown"))
    return {"sources": sorted(titles), "total_chunks": rag.get_stats()["total_chunks"]}
 
 
@app.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest):
    """Full pipeline: security + RAG + reasoning (non-streaming)."""
    total_start = time.time()
 
    # Security
    input_result = security.process_input(request.question, request.user_id)
    security_info = {
        "pii_detected": len(input_result["pii_findings"]) > 0,
        "pii_redacted": len(input_result["pii_findings"]),
        "injection_blocked": not input_result["allowed"] and "injection" in str(input_result.get("block_reason", "")),
        "topic_blocked": not input_result["allowed"] and "topic" in str(input_result.get("block_reason", "")),
    }
 
    if not input_result["allowed"]:
        security.audit.log("query_blocked", {"reason": input_result["block_reason"]})
        return AskResponse(
            answer=f"Request blocked: {input_result['block_reason']}",
            security=security_info,
            stats={"total_time": time.time() - total_start},
        )
 
    cleaned_query = input_result["cleaned_query"]
 
    # RAG
    retrieved = rag.retrieve(cleaned_query, top_k=request.top_k)
    sources = [{"title": r["title"], "score": round(r["score"], 3),
                "relevance": "HIGH" if r["score"] > 0.5 else "MEDIUM" if r["score"] > 0.3 else "LOW"}
               for r in retrieved]
 
    # Build messages with history
    if retrieved:
        context_parts = [f"[Document {i+1}: {c['title']}]\n{c['text']}" for i, c in enumerate(retrieved)]
        system = RAG_SYSTEM_PROMPT.format(context="\n\n---\n\n".join(context_parts))
    else:
        system = SYSTEM_PROMPT
 
    messages = [{"role": "system", "content": system}]
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": cleaned_query})
 
    # Generate
    gen_start = time.time()
    result = model.generate_from_messages(messages, temperature=request.temperature)
    gen_time = time.time() - gen_start
 
    if not result or "error" in result:
        raise HTTPException(status_code=500, detail=result.get("error", "Generation failed"))
 
    # Output security
    cleaned_output, output_findings = security.process_output(result.get("raw", ""))
 
    # Audit
    total_time = time.time() - total_start
    security.audit.log("query_response", {
        "query": cleaned_query[:200],
        "pii_found": len(input_result["pii_findings"]) > 0,
        "sources": [r["title"] for r in retrieved],
        "latency": total_time,
    })
 
    return AskResponse(
        answer=result.get("answer", ""),
        thinking=result.get("thinking") if request.show_thinking else None,
        sources=sources,
        security=security_info,
        stats={"total_time": round(total_time, 2), "generate_time": round(gen_time, 2),
               "tokens": result.get("tokens", 0)},
    )
 
 
@app.post("/ask/stream")
async def ask_stream(request: AskRequest):
    """Streaming endpoint — tokens sent via Server-Sent Events."""
    # Security
    input_result = security.process_input(request.question, request.user_id)
 
    if not input_result["allowed"]:
        async def blocked_stream():
            data = json.dumps({"type": "security", "data": {
                "pii_detected": False,
                "injection_blocked": True,
            }})
            yield f"data: {data}\n\n"
            data = json.dumps({"type": "done", "data": {
                "answer": f"Request blocked: {input_result['block_reason']}",
                "thinking": None,
            }})
            yield f"data: {data}\n\n"
        return StreamingResponse(blocked_stream(), media_type="text/event-stream")
 
    cleaned_query = input_result["cleaned_query"]
 
    # RAG
    retrieved = rag.retrieve(cleaned_query, top_k=request.top_k)
    sources = [{"title": r["title"], "score": round(r["score"], 3),
                "relevance": "HIGH" if r["score"] > 0.5 else "MEDIUM" if r["score"] > 0.3 else "LOW"}
               for r in retrieved]
 
    # Build messages
    if retrieved:
        context_parts = [f"[Document {i+1}: {c['title']}]\n{c['text']}" for i, c in enumerate(retrieved)]
        system = RAG_SYSTEM_PROMPT.format(context="\n\n---\n\n".join(context_parts))
    else:
        system = SYSTEM_PROMPT
 
    messages = [{"role": "system", "content": system}]
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": cleaned_query})
 
    async def token_stream():
        # Send security info first
        sec_data = json.dumps({"type": "security", "data": {
            "pii_detected": len(input_result["pii_findings"]) > 0,
            "injection_blocked": False,
        }})
        yield f"data: {sec_data}\n\n"
 
        # Send sources
        if sources:
            src_data = json.dumps({"type": "sources", "data": sources})
            yield f"data: {src_data}\n\n"
 
        # Stream from MLX server
        start = time.time()
        full_text = ""
        token_count = 0
 
        try:
            resp = http_requests.post(
                f"{model.base_url}/v1/chat/completions",
                json={
                    "model": model.mlx_model_name,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": 512,
                    "stream": True,
                },
                stream=True,
                timeout=120,
            )
 
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                        token_count += 1
                        token_data = json.dumps({"type": "token", "data": content})
                        yield f"data: {token_data}\n\n"
                except json.JSONDecodeError:
                    continue
 
        except Exception as e:
            err_data = json.dumps({"type": "error", "data": str(e)})
            yield f"data: {err_data}\n\n"
 
        # Send final parsed result
        elapsed = time.time() - start
        parsed = parse_response(clean_latex(full_text))
 
        # Output PII check
        security.process_output(full_text)
 
        # Audit
        security.audit.log("query_response", {
            "query": cleaned_query[:200],
            "sources": [s["title"] for s in sources],
            "latency": elapsed,
        })
 
        done_data = json.dumps({"type": "done", "data": {
            "answer": parsed.get("answer", full_text),
            "thinking": parsed.get("thinking"),
            "tokens": token_count,
            "total_time": round(elapsed, 2),
        }})
        yield f"data: {done_data}\n\n"
 
    return StreamingResponse(token_stream(), media_type="text/event-stream")
 
 
@app.post("/ask/simple", response_model=AskResponse)
async def ask_simple(request: AskRequest):
    """Reasoning only, no RAG."""
    input_result = security.process_input(request.question, request.user_id)
    if not input_result["allowed"]:
        return AskResponse(answer=f"Request blocked: {input_result['block_reason']}", sources=[], security={}, stats={})
 
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": input_result["cleaned_query"]})
 
    result = model.generate_from_messages(messages, temperature=request.temperature)
    if not result or "error" in result:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed"))
 
    return AskResponse(
        answer=result.get("answer", ""),
        thinking=result.get("thinking") if request.show_thinking else None,
        sources=[],
        security={"pii_detected": len(input_result["pii_findings"]) > 0},
        stats={"tokens": result.get("tokens", 0)},
    )
 
 
@app.post("/ingest", response_model=IngestResponse)
async def ingest_document(request: IngestRequest):
    chunks = rag.ingest_document(title=request.title, content=request.content,
                                 source=request.source, category=request.category)
    return IngestResponse(success=True, chunks_created=chunks, total_chunks=rag.get_stats()["total_chunks"])
 
 
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.server:app", host="0.0.0.0", port=8000, reload=True)