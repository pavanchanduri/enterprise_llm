# Enterprise LLM — Local Development Guide

## What This Project Is

A production-ready enterprise LLM with step-by-step reasoning, RAG (retrieval-augmented generation), and security guardrails. Built from scratch, running locally on Mac M4 Pro.

The model thinks inside `<thinking>` tags before answering, retrieves relevant documents from your knowledge base, and applies PII detection, prompt injection defense, and audit logging on every query.

## Project Structure

```
enterprise_llm/
├── setup.sh                    ← Run this FIRST (one-time setup)
├── requirements.txt            ← Python dependencies
├── .env                        ← Your API keys (created by setup.sh)
├── .gitignore
│
├── config/
│   └── config.yaml             ← All configuration in one place
│
├── src/
│   ├── main.py                 ← Entry point — ties everything together
│   ├── inference/
│   │   └── model.py            ← Ollama-based model interaction
│   ├── rag/
│   │   └── pipeline.py         ← Document ingestion + retrieval
│   ├── security/
│   │   └── middleware.py        ← PII, injection defense, audit logging
│   ├── training/               ← Fine-tuning scripts (for Colab/cloud)
│   └── utils/
│       └── config.py           ← Configuration loader
│
├── data/
│   ├── traces/                 ← Your Claude-generated traces (from Drive)
│   ├── training_data/          ← train.jsonl, val.jsonl (from Drive)
│   ├── documents/              ← Your documents for RAG
│   └── chromadb/               ← Vector database (auto-created)
│
├── models/                     ← Trained adapters (from Drive)
├── logs/                       ← Audit logs (auto-created)
└── tests/                      ← Test files
```

## Setup (One-Time, ~10 minutes)

### Step 1: Open Terminal

Press `Cmd + Space`, type "Terminal", press Enter.

### Step 2: Install Homebrew (if you don't have it)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

After installation, follow the instructions it prints to add Homebrew to your PATH.

### Step 3: Install Python 3.11+

```bash
brew install python@3.11
```

Verify: `python3 --version` should show 3.11 or higher.

### Step 4: Install Ollama

```bash
brew install ollama
```

### Step 5: Create the project

```bash
# Navigate to where you want the project (e.g., Desktop or home folder)
cd ~/Desktop

# Create project directory
mkdir enterprise_llm
cd enterprise_llm
```

### Step 6: Copy project files

Copy all the files from this package into the `enterprise_llm` folder, maintaining the directory structure. You can drag-and-drop in Finder or use the terminal.

### Step 7: Create virtual environment

```bash
# Create a Python virtual environment (isolates dependencies)
python3 -m venv venv

# Activate it (you'll see (venv) in your terminal prompt)
source venv/bin/activate
```

**Important:** Every time you open a new terminal to work on this project, you need to activate the venv:
```bash
cd ~/Desktop/enterprise_llm
source venv/bin/activate
```

### Step 8: Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

This takes 3-5 minutes. Ignore any warnings about "dependency conflicts" — they don't affect our project.

### Step 9: Download the model via Ollama

```bash
# Start Ollama (keep this terminal running)
ollama serve
```

**Open a NEW terminal tab** (Cmd + T) and run:

```bash
# Download Qwen 2.5 3B (2GB download)
ollama pull qwen2.5:3b

# Optional: Download the 7B model for better quality (4.7GB)
# Your 24GB Mac can handle this easily
ollama pull qwen2.5:7b
```

### Step 10: Add your API key

Edit the `.env` file:
```bash
nano .env
```

Replace `sk-ant-api03-YOUR-KEY-HERE` with your actual Anthropic API key. Press `Ctrl+O` to save, `Ctrl+X` to exit.

### Step 11: Copy your data from Google Drive

Download these files from your Google Drive `enterprise_llm_backup` folder and place them:

| File from Drive | Copy to |
|----------------|---------|
| `train.jsonl` | `data/training_data/train.jsonl` |
| `val.jsonl` | `data/training_data/val.jsonl` |
| `test.jsonl` | `data/training_data/test.jsonl` |
| `raw_traces.jsonl` | `data/traces/raw_traces.jsonl` |
| `filtered_traces.jsonl` | `data/traces/filtered_traces.jsonl` |
| `seed_questions.jsonl` | `data/traces/seed_questions.jsonl` |

### Step 12: Open in VS Code

```bash
code .
```

If `code` command doesn't work:
1. Open VS Code
2. Press `Cmd + Shift + P`
3. Type "Shell Command: Install 'code' command in PATH"
4. Then retry `code .`

## Running the Project

**Important:** Always make sure you have TWO things running:
1. Ollama serving (in one terminal): `ollama serve`
2. Your Python command (in another terminal with venv activated)

### Ingest sample documents (first time)

```bash
python -m src.main --ingest
```

### Ingest your own documents

Put `.txt` or `.md` files in `data/documents/`, then:

```bash
python -m src.main --ingest-dir data/documents
```

### Ask a single question

```bash
python -m src.main --ask "What is the time complexity of a B-Tree index search?"
```

### Interactive chat mode

```bash
python -m src.main
```

This starts a chat loop where you can ask questions continuously. Type `/quit` to exit, `/stats` for audit statistics.

### Use the 7B model (better quality)

```bash
python -m src.main --model qwen2.5:7b
```

## How It Works

Every query flows through this pipeline:

```
Your Question
  → Rate Limit Check
  → PII Detection (redact names, emails, SSNs)
  → Injection Defense (block manipulation attempts)
  → Topic Boundary (block harmful queries)
  → RAG Retrieval (search vector DB for relevant docs)
  → Reasoning Model via Ollama (generate with <thinking>)
  → Output PII Scan (catch any leaks)
  → Audit Log (immutable record)
  → Clean Response
```

## Key Differences: Colab vs Local

| Aspect | Colab | Local Mac M4 Pro |
|--------|-------|-----------------|
| Model serving | Unsloth + PyTorch | Ollama (Metal GPU optimized) |
| GPU | NVIDIA T4 (CUDA) | Apple M4 Pro (Metal) |
| Runtime | Temporary, disconnects | Permanent, always available |
| Files | Lost on restart | Persist forever |
| Code structure | Notebook cells | Proper Python modules |
| Fine-tuning | Yes (CUDA GPU) | Use Colab for this |

## Training (Still on Colab)

Fine-tuning (LoRA/GRPO) still works best on Colab because it needs CUDA GPUs. Your trained adapter is already backed up in Google Drive. If you need to retrain:

1. Open Colab, get a GPU runtime
2. Run `helper_restore_from_drive.py`
3. Run `03_finetune_lora.py`
4. Download the new adapter and place in `models/adapters/`

To use a custom fine-tuned model with Ollama, you'd create a Modelfile — but for now the base Qwen model works great with the RAG + reasoning pipeline.

## Troubleshooting

**"Ollama not running" error:**
Make sure `ollama serve` is running in a separate terminal.

**"No module named 'src'" error:**
Make sure you're running from the project root (`enterprise_llm/` folder) and the venv is activated.

**Slow responses:**
The 3B model generates at ~15-20 tokens/second on M4 Pro. For faster responses, reduce `max_tokens` in config.yaml. The 7B model is slower but more accurate.

**"spacy model not found" error:**
Run: `python -m spacy download en_core_web_lg`

**PII detection not working:**
Presidio needs the spaCy model. Reinstall: `pip install presidio-analyzer presidio-anonymizer && python -m spacy download en_core_web_lg`
