"""
Inference module — interact with the reasoning model via Ollama.

Ollama runs the model locally on your Mac M4 Pro using Metal GPU acceleration.
It handles model loading, quantization, and generation automatically.

Usage:
    from src.inference.model import ReasoningModel
    model = ReasoningModel()
    response = model.ask("What is 25% off $40?")
"""

import subprocess
import json
import re
import time
import requests


SYSTEM_PROMPT = """You are a helpful reasoning assistant. For every question, you MUST:
1. First think through the problem step-by-step inside <thinking>...</thinking> tags.
2. Then provide a clear, concise answer after the thinking block.

Your thinking should show every step of your reasoning. Be thorough but focused.
Write all math in plain text. Do NOT use LaTeX or backslashes."""

RAG_SYSTEM_PROMPT = """You are a helpful reasoning assistant with access to a knowledge base.

For every question, you MUST:
1. Review the CONTEXT documents provided below.
2. Think step-by-step inside <thinking>...</thinking> tags.
3. In your thinking, reference which documents support each step.
4. If the context contains the answer, provide it grounded in the context.
5. If the context does NOT contain enough information, clearly say so.
6. After the thinking block, give a clear, concise answer.

CONTEXT (retrieved from knowledge base):
{context}

IMPORTANT: Base your answer on the provided context. Write math in plain text."""


def clean_latex(text):
    """Remove LaTeX formatting from model output."""
    text = re.sub(r'\\boxed\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\\(', '', text)
    text = re.sub(r'\\\)', '', text)
    text = re.sub(r'\\\[', '', text)
    text = re.sub(r'\\\]', '', text)
    text = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', text)
    text = re.sub(r'\\cdot', '*', text)
    text = re.sub(r'\\times', '×', text)
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\(?!n)([a-zA-Z]+)', r'\1', text)
    return text.strip()


def parse_response(text):
    """Separate <thinking> blocks from the final answer, removing duplicates."""
    text = clean_latex(text)
    # Find ALL thinking blocks and combine them
    thinking_parts = re.findall(r'<thinking>(.*?)</thinking>', text, re.DOTALL)
    if thinking_parts:
        thinking = "\n".join(part.strip() for part in thinking_parts)
        # Remove all thinking blocks from text to get clean answer
        answer = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL).strip()
        
        # Remove duplicate content: if the answer repeats large chunks
        # of the thinking, trim it down
        if thinking and answer:
            # Split answer into paragraphs
            answer_paragraphs = [p.strip() for p in answer.split('\n\n') if p.strip()]
            thinking_lower = thinking.lower()
            
            # Keep only paragraphs that aren't substantially repeated from thinking
            unique_paragraphs = []
            for para in answer_paragraphs:
                # Check if this paragraph (or most of it) appears in thinking
                para_clean = para.strip().lower()
                # If paragraph is short or not found in thinking, keep it
                if len(para_clean) < 30 or para_clean[:50] not in thinking_lower:
                    unique_paragraphs.append(para)
            
            if unique_paragraphs:
                answer = "\n\n".join(unique_paragraphs)
            else:
                # If everything was duplicate, keep the last paragraph as summary
                answer = answer_paragraphs[-1] if answer_paragraphs else answer
        
        return {"thinking": thinking, "answer": answer, "raw": text}
    return {"thinking": None, "answer": text, "raw": text}


class ReasoningModel:
    """
    Interface to the reasoning model running via Ollama.

    Ollama serves the model locally using Metal GPU on Mac M4 Pro.
    It handles all the model loading and inference optimization.
    """

    def __init__(self, model_name="qwen2.5:3b", base_url=None):
        self.model_name = model_name
        
        if model_name == "mlx" or model_name == "enterprise-llm":
            self.backend = "mlx"
            self.base_url = base_url or "http://localhost:8080"
            self.mlx_model_name = "models/quantized_model"
            self._verify_mlx()
        else:
            self.backend = "ollama"
            self.base_url = base_url or "http://localhost:11434"
            self._verify_ollama()

    def _verify_ollama(self):
        """Check that Ollama is running and the model is available."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            if self.model_name not in models and f"{self.model_name}:latest" not in models:
                # Check partial match
                found = any(self.model_name.split(":")[0] in m for m in models)
                if not found:
                    print(f"  ⚠️  Model '{self.model_name}' not found. Run: ollama pull {self.model_name}")
                    return
            print(f"  ✅ Ollama connected, model '{self.model_name}' ready")
        except requests.ConnectionError:
            print("  ❌ Ollama not running! Start it with: ollama serve")
            print("     Then in another terminal: ollama pull qwen2.5:3b")

    def _verify_mlx(self):
        """ Check that MLX server is running."""
        try:
            resp = requests.get(f"{self.base_url}/v1/models", timeout=5)
            if resp.status_code == 200:
                print(f"  ✅ MLX server connected at {self.base_url}")
            else:
                print(f"  ⚠️  MLX server responded with status {resp.status_code}")
        except requests.ConnectionError:
            print("  ❌ MLX server not running! Start it with:")
            print("     python -m mlx_lm server --model Qwen/Qwen2.5-7B-Instruct --adapter-path models/adapters/mlx_lora --port 8080")

    def generate(self, prompt, system=None, temperature=0.7, max_tokens=1024):
        """
        Generate a response from the model.

        Args:
            prompt: User's question
            system: System prompt (uses default if None)
            temperature: Creativity (0=deterministic, 1=creative)
            max_tokens: Maximum response length

        Returns:
            Dict with 'text', 'thinking', 'answer', 'tokens', 'latency'
        """
        if system is None:
            system = SYSTEM_PROMPT

        start = time.time()

        try:
            if self.backend == "mlx":
                # MLX server uses OpenAI-compatible API
                resp = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.mlx_model_name,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                latency = time.time() - start
                
                parsed = parse_response(text)
                parsed["latency"] = latency
                parsed["tokens"] = data.get("usage", {}).get("completion_tokens", 0)
                parsed["model"] = self.model_name
                return parsed

            else:
                # Ollama API
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                        },
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["message"]["content"]
                latency = time.time() - start

                parsed = parse_response(text)
                parsed["latency"] = latency
                parsed["tokens"] = data.get("eval_count", 0)
                parsed["model"] = self.model_name
                return parsed

        except requests.ConnectionError:
            backend = "MLX server" if self.backend == "mlx" else "Ollama"
            return {"error": f"{backend} not running.",
                    "thinking": None, "answer": None, "raw": None, "latency": 0}
        except Exception as e:
            return {"error": str(e), "thinking": None, "answer": None,
                    "raw": None, "latency": 0}

    def ask(self, question, temperature=0.7, show_thinking=True):
        """
        Ask a question and print the formatted response.

        Args:
            question: Your question
            temperature: Creativity level
            show_thinking: Whether to display the thinking block
        """
        result = self.generate(question, temperature=temperature)

        if "error" in result:
            print(f"\n  ❌ Error: {result['error']}")
            return result

        if result["thinking"] and show_thinking:
            print(f"\n{'─'*50}")
            print(f"🧠 THINKING:")
            print(f"{'─'*50}")
            print(result["thinking"])
            print(f"{'─'*50}")
            print(f"💡 ANSWER:")
            print(f"{'─'*50}")
            print(result["answer"])
            print(f"{'─'*50}")
        else:
            print(f"\n💡 {result['answer']}")

        print(f"\n📊 {result['tokens']} tokens in {result['latency']:.1f}s")
        return result
    
    def generate_from_messages(self, messages, temperature=0.7, max_tokens=512):
        """
        Generate from a pre-built messages array (supports conversation history).
        Used by the API server which builds its own message arrays.
        """
        start = time.time()

        try:
            if self.backend == "mlx":
                resp = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.mlx_model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                latency = time.time() - start

                parsed = parse_response(clean_latex(text))
                parsed["latency"] = latency
                parsed["tokens"] = data.get("usage", {}).get("completion_tokens", 0)
                parsed["model"] = self.mlx_model_name
                return parsed

            else:
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model_name,
                        "messages": messages,
                        "stream": False,
                        "options": {"temperature": temperature, "num_predict": max_tokens},
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["message"]["content"]
                latency = time.time() - start

                parsed = parse_response(clean_latex(text))
                parsed["latency"] = latency
                parsed["tokens"] = data.get("eval_count", 0)
                parsed["model"] = self.model_name
                return parsed

        except requests.ConnectionError:
            backend = "MLX server" if self.backend == "mlx" else "Ollama"
            return {"error": f"{backend} not running.", "thinking": None, "answer": None, "raw": None, "latency": 0}
        except Exception as e:
            return {"error": str(e), "thinking": None, "answer": None, "raw": None, "latency": 0}

    def ask_with_context(self, question, context_chunks, temperature=0.7, show_thinking=True):
        """
        Ask a question with RAG context.

        Args:
            question: User's question
            context_chunks: List of retrieved document chunks
            temperature: Creativity level
            show_thinking: Display thinking block
        """
        # Build context string
        context_parts = []
        for i, chunk in enumerate(context_chunks, 1):
            title = chunk.get("title", f"Document {i}")
            context_parts.append(f"[Document {i}: {title}]\n{chunk['text']}")
        context_str = "\n\n---\n\n".join(context_parts)

        system = RAG_SYSTEM_PROMPT.format(context=context_str)
        result = self.generate(question, system=system, temperature=temperature)

        if "error" in result:
            print(f"\n  ❌ Error: {result['error']}")
            return result

        if result["thinking"] and show_thinking:
            print(f"\n{'─'*50}")
            print(f"🧠 THINKING:")
            print(f"{'─'*50}")
            print(result["thinking"])
            print(f"{'─'*50}")
            print(f"💡 ANSWER:")
            print(f"{'─'*50}")
            print(result["answer"])
            print(f"{'─'*50}")
        else:
            print(f"\n💡 {result['answer']}")

        print(f"\n📊 {result['tokens']} tokens in {result['latency']:.1f}s")
        return result
