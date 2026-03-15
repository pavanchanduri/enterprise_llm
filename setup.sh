#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Enterprise LLM — Local Setup Script for Mac M4 Pro
# ═══════════════════════════════════════════════════════════════
# Run this script ONCE to set up everything:
#   chmod +x setup.sh
#   ./setup.sh
# ═══════════════════════════════════════════════════════════════

set -e  # Exit on any error

echo "═══════════════════════════════════════════════════════════"
echo "  Enterprise LLM — Local Setup for Mac M4 Pro (24GB)"
echo "═══════════════════════════════════════════════════════════"

# ─── Step 1: Check prerequisites ───
echo ""
echo "[1/7] Checking prerequisites..."

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo "  ✅ Homebrew installed"
fi

# Check Python version
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version | cut -d' ' -f2)
    echo "  ✅ Python $PY_VERSION"
else
    echo "  Installing Python 3.11..."
    brew install python@3.11
fi

# ─── Step 2: Install Ollama ───
echo ""
echo "[2/7] Installing Ollama (local model inference engine)..."

if ! command -v ollama &> /dev/null; then
    brew install ollama
    echo "  ✅ Ollama installed"
else
    echo "  ✅ Ollama already installed"
fi

# ─── Step 3: Create project directory structure ───
echo ""
echo "[3/7] Creating project structure..."

mkdir -p enterprise_llm/{src/{training,rag,security,inference,utils},data/{traces,training_data,documents,chromadb},models/{adapters,ollama},config,tests,scripts,logs}

echo "  ✅ Directory structure created"

# ─── Step 4: Create Python virtual environment ───
echo ""
echo "[4/7] Creating Python virtual environment..."

cd enterprise_llm
python3 -m venv venv
source venv/bin/activate

echo "  ✅ Virtual environment created and activated"

# ─── Step 5: Install Python dependencies ───
echo ""
echo "[5/7] Installing Python dependencies (this takes 2-3 minutes)..."

pip install --upgrade pip

# Core ML libraries (Apple Silicon optimized)
pip install torch torchvision torchaudio
pip install mlx mlx-lm

# Training
pip install transformers datasets peft trl accelerate
pip install bitsandbytes

# RAG
pip install chromadb sentence-transformers

# Security
pip install presidio-analyzer presidio-anonymizer spacy
python -m spacy download en_core_web_lg

# API + Utils
pip install anthropic
pip install rich           # Pretty terminal output
pip install click          # CLI framework
pip install python-dotenv  # Environment variables
pip install fastapi uvicorn  # API server (for deployment)
pip install pyyaml

echo "  ✅ All dependencies installed"

# ─── Step 6: Download base model via Ollama ───
echo ""
echo "[6/7] Downloading Qwen 2.5 3B model via Ollama..."
echo "  (This downloads ~2GB, may take a few minutes)"

# Start Ollama service
ollama serve &> /dev/null &
sleep 3

# Pull the model
ollama pull qwen2.5:3b

echo "  ✅ Qwen 2.5 3B model downloaded"
echo "  (You can also run: ollama pull qwen2.5:7b for the 7B model later)"

# ─── Step 7: Create configuration files ───
echo ""
echo "[7/7] Creating configuration files..."

# Create .env file
cat > .env << 'EOF'
# Enterprise LLM Configuration
# Copy your Anthropic API key here (for trace generation only)
ANTHROPIC_API_KEY=sk-ant-api03-YOUR-KEY-HERE

# Model settings
BASE_MODEL=qwen2.5:3b
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Paths
DATA_DIR=./data
MODELS_DIR=./models
LOGS_DIR=./logs
EOF

echo "  ✅ Configuration files created"

# ─── Done! ───
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SETUP COMPLETE!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Project directory: $(pwd)"
echo ""
echo "  Next steps:"
echo "    1. Open this folder in VS Code:"
echo "       code ."
echo ""
echo "    2. Edit .env and add your Anthropic API key"
echo ""
echo "    3. Copy your Google Drive backup files to:"
echo "       data/traces/       ← raw_traces.jsonl, filtered_traces.jsonl"
echo "       data/training_data/ ← train.jsonl, val.jsonl, test.jsonl"
echo ""
echo "    4. Activate the virtual environment in VS Code terminal:"
echo "       source venv/bin/activate"
echo ""
echo "    5. Run the pipeline:"
echo "       python -m src.main"
echo ""
echo "═══════════════════════════════════════════════════════════"
