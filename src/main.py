"""
═══════════════════════════════════════════════════════════════
Enterprise LLM — Main Entry Point
═══════════════════════════════════════════════════════════════
This is the complete production pipeline. It ties together:
  - Reasoning Model (via Ollama)
  - RAG Pipeline (ChromaDB + sentence-transformers)
  - Security Middleware (PII + injection + audit)

Usage:
    python -m src.main                  # Interactive mode
    python -m src.main --ingest         # Ingest sample documents
    python -m src.main --ask "question" # Ask a single question
═══════════════════════════════════════════════════════════════
"""

import argparse
import sys
import time
from pathlib import Path

from src.inference.model import ReasoningModel
from src.rag.pipeline import RAGPipeline
from src.security.middleware import SecurityMiddleware


# ═══════════════════════════════════════════════════════════════
# SAMPLE DOCUMENTS (for initial setup)
# ═══════════════════════════════════════════════════════════════

SAMPLE_DOCS = [
    {"title": "Python Data Structures Guide", "category": "programming", "content": """# Python Data Structures Guide

## Lists
Lists are ordered, mutable collections in Python. They support indexing, slicing, and methods like append(), extend(), insert(), remove(), and pop(). Time complexity: O(1) for append and access by index, O(n) for search and insert at arbitrary position.

## Dictionaries
Dictionaries store key-value pairs and provide O(1) average lookup time. Keys must be hashable. In Python 3.7+, dictionaries maintain insertion order.

## Sets
Sets are unordered collections of unique elements. They support union (|), intersection (&), difference (-). Sets are useful for membership testing (O(1) average).

## Tuples
Tuples are immutable ordered sequences. They use less memory than lists and can be used as dictionary keys."""},

    {"title": "Database Indexing Explained", "category": "databases", "content": """# Database Indexing

## B-Tree Indexes
B-Tree indexes maintain sorted data and allow searches, insertions, and deletions in O(log n) time. They work well for equality queries (WHERE id = 5) and range queries (WHERE date BETWEEN '2024-01-01' AND '2024-12-31'). On a table with 1 million rows, a B-Tree index query takes 1-5ms compared to 500ms for a full table scan.

## Hash Indexes
Hash indexes provide O(1) lookup for equality queries but do NOT support range queries.

## Composite Indexes
A composite index covers multiple columns. The order matters: an index on (last_name, first_name) can serve queries filtering by last_name alone or both, but NOT first_name alone. This is the "leftmost prefix" rule."""},

    {"title": "REST API Design Best Practices", "category": "architecture", "content": """# REST API Design

## HTTP Methods
GET retrieves resources (safe, idempotent). POST creates new resources. PUT replaces entirely (idempotent). PATCH partially updates. DELETE removes (idempotent).

## Status Codes
200 OK, 201 Created, 204 No Content, 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 429 Too Many Requests, 500 Internal Server Error.

## Authentication
Use OAuth 2.0 or API keys. Implement rate limiting: return 429 with Retry-After header. Common limits: 100 req/min free tier, 1000 paid."""},

    {"title": "Network Security Fundamentals", "category": "security", "content": """# Network Security

## Encryption
Symmetric encryption (AES-256) uses the same key for encryption and decryption — fast but requires secure key exchange. Asymmetric encryption (RSA, ECC) uses a public key for encryption and private key for decryption — slower but solves key exchange.

## Zero Trust Architecture
Core principles: verify explicitly (always authenticate), least privilege access (minimum permissions), assume breach (design as if attackers are inside). Implementation includes micro-segmentation and continuous monitoring."""},

    {"title": "Cloud Cost Optimization", "category": "cloud", "content": """# Cloud Cost Optimization

## Right-Sizing
Most workloads are over-provisioned. Average EC2 utilization is 20-30%. Right-sizing can reduce compute costs by 30-50%.

## Spot Instances
Spot instances offer 60-90% discount using spare capacity. They can be interrupted with 2 minutes notice. Ideal for batch processing, CI/CD, and ML training. Not suitable for databases.

## Auto-Scaling
Scale down aggressively during off-hours. Many companies waste 40% of cloud budget on resources running overnight with no traffic."""},

    {"title": "ML Model Evaluation", "category": "machine_learning", "content": """# ML Model Evaluation

## Precision and Recall
Precision: TP / (TP + FP). High precision = few false positives. Recall: TP / (TP + FN). High recall = few false negatives.

## F1 Score
Harmonic mean of precision and recall: 2 * (P * R) / (P + R). Ranges 0-1. Use macro-F1 for multi-class with equal class importance.

## AUC-ROC
AUC of 0.5 = random guessing. AUC of 1.0 = perfect. AUC above 0.8 is generally good. Cross-validation (5-fold or 10-fold) provides robust performance estimates."""},
]


# ═══════════════════════════════════════════════════════════════
# PRODUCTION ASK FUNCTION
# ═══════════════════════════════════════════════════════════════

def production_ask(question, model, rag, security, user_id="default",
                   show_security=True, show_sources=True, show_thinking=True):
    """
    Complete production pipeline:
    Rate Limit → PII → Injection → Topic → RAG → Model → Output PII → Audit
    """
    total_start = time.time()

    # ── Security: Input Guards ──
    input_result = security.process_input(question, user_id)

    if show_security:
        print(f"\n{'─'*50}")
        print(f"🔒 SECURITY:")
        pii_status = f"PII redacted ({len(input_result['pii_findings'])})" if input_result['pii_findings'] else "Clean"
        if input_result["allowed"]:
            print(f"  ✅ Rate limit: OK | PII: {pii_status} | Injection: Safe | Topic: OK")
        else:
            print(f"  🛑 BLOCKED: {input_result['block_reason']}")

    if not input_result["allowed"]:
        print(f"\n  I cannot process this request. {input_result['block_reason']}")
        security.audit.log("query_blocked", {"reason": input_result["block_reason"]})
        return None

    cleaned_query = input_result["cleaned_query"]

    # ── RAG: Retrieve ──
    retrieved = rag.retrieve(cleaned_query)

    if show_sources and retrieved:
        print(f"{'─'*50}")
        print(f"📚 SOURCES ({len(retrieved)} chunks):")
        for i, r in enumerate(retrieved, 1):
            rel = "HIGH" if r['score'] > 0.5 else "MED" if r['score'] > 0.3 else "LOW"
            print(f"  {i}. [{r['title']}] {rel} ({r['score']:.3f})")

    # ── Model: Generate ──
    if retrieved:
        result = model.ask_with_context(cleaned_query, retrieved, show_thinking=show_thinking)
    else:
        result = model.ask(cleaned_query, show_thinking=show_thinking)

    if result and result.get("raw"):
        # ── Security: Output Guard ──
        cleaned_output, output_findings = security.process_output(result["raw"])
        if output_findings and show_security:
            print(f"  ⚠️  Output PII redacted: {', '.join(f['type'] for f in output_findings)}")

        # ── Audit Log ──
        security.audit.log("query_response", {
            "query": cleaned_query[:200],
            "pii_found": len(input_result["pii_findings"]) > 0,
            "sources": [r["title"] for r in retrieved],
            "latency": time.time() - total_start,
        })

    return result


# ═══════════════════════════════════════════════════════════════
# INTERACTIVE MODE
# ═══════════════════════════════════════════════════════════════

def interactive_mode(model, rag, security):
    """Run an interactive chat loop."""
    print(f"\n{'═'*50}")
    print(f"  Enterprise LLM — Interactive Mode")
    print(f"{'═'*50}")
    print(f"  Type your question and press Enter.")
    print(f"  Commands: /stats /sources /quit")
    print(f"{'═'*50}\n")

    while True:
        try:
            question = input("You: ").strip()

            if not question:
                continue
            if question.lower() in ("/quit", "/exit", "quit", "exit"):
                print("Goodbye!")
                break
            if question.lower() == "/stats":
                stats = security.audit.get_stats()
                rag_stats = rag.get_stats()
                print(f"\n  Audit: {stats}")
                print(f"  RAG: {rag_stats}\n")
                continue
            if question.lower() == "/sources":
                stats = rag.get_stats()
                print(f"\n  Vector DB: {stats['total_chunks']} chunks stored\n")
                continue

            production_ask(question, model, rag, security)
            print()

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Enterprise LLM")
    parser.add_argument("--ingest", action="store_true", help="Ingest sample documents")
    parser.add_argument("--ingest-dir", type=str, help="Ingest all .txt/.md files from directory")
    parser.add_argument("--ask", type=str, help="Ask a single question")
    parser.add_argument("--model", type=str, default="qwen2.5:3b", help="Ollama model name")
    parser.add_argument("--no-security", action="store_true", help="Disable security checks")
    args = parser.parse_args()

    print("═" * 50)
    print("  Enterprise LLM — Starting Up")
    print("═" * 50)

    # Initialize components
    print("\n[1/3] Loading model...")
    model = ReasoningModel(model_name=args.model)

    print("\n[2/3] Loading RAG pipeline...")
    rag = RAGPipeline()

    print("\n[3/3] Loading security...")
    security = SecurityMiddleware()

    # Handle commands
    if args.ingest:
        print(f"\n{'─'*50}")
        print("Ingesting sample documents...")
        for doc in SAMPLE_DOCS:
            rag.ingest_document(title=doc["title"], content=doc["content"],
                              source=f"samples/{doc['title'].lower().replace(' ', '_')}.md",
                              category=doc["category"])
        print(f"Done! {rag.get_stats()['total_chunks']} total chunks in database")
        return

    if args.ingest_dir:
        print(f"\nIngesting files from: {args.ingest_dir}")
        count = rag.ingest_directory(args.ingest_dir)
        print(f"Ingested {count} chunks from directory")
        return

    if args.ask:
        production_ask(args.ask, model, rag, security)
        return

    # Default: interactive mode
    interactive_mode(model, rag, security)


if __name__ == "__main__":
    main()
