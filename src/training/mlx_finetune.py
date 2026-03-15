"""
═══════════════════════════════════════════════════════════════
MLX Fine-Tuning Pipeline for Mac M4 Pro
═══════════════════════════════════════════════════════════════
Fine-tunes Qwen 2.5 7B on YOUR training data using Apple's MLX
framework. Everything runs locally — no Colab, no cloud needed.

What happens:
  Step 1: Install MLX dependencies
  Step 2: Convert training data to MLX format
  Step 3: Configure LoRA fine-tuning
  Step 4: Train (~25-40 minutes on M4 Pro)
  Step 5: Test the fine-tuned model
  Step 6: Create Ollama model for deployment

Usage:
  cd /Users/pavan.chanduri/AI_Learning/enterprise_llm
  source venv/bin/activate
  python src/training/mlx_finetune.py
═══════════════════════════════════════════════════════════════
"""

import json
import subprocess
import sys
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "training_data"
MODELS_DIR = PROJECT_ROOT / "models"
MLX_DATA_DIR = PROJECT_ROOT / "data" / "mlx_training"
ADAPTERS_DIR = MODELS_DIR / "adapters" / "mlx_lora"
FUSED_DIR = MODELS_DIR / "fused_model"

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"

print("=" * 60)
print("  MLX Fine-Tuning — Qwen 2.5 7B on Mac M4 Pro")
print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# STEP 1: INSTALL DEPENDENCIES
# ═══════════════════════════════════════════════════════════════

print("\n[1/6] Checking dependencies...")

try:
    import mlx_lm
    print(f"  ✅ mlx-lm installed (v{mlx_lm.__version__})")
except ImportError:
    print("  Installing mlx-lm...")
    subprocess.run([sys.executable, "-m", "pip", "install", "mlx-lm"], check=True)
    import mlx_lm
    print(f"  ✅ mlx-lm installed")

try:
    import yaml
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml"], check=True)
    import yaml


# ═══════════════════════════════════════════════════════════════
# STEP 2: PREPARE TRAINING DATA
# ═══════════════════════════════════════════════════════════════

print("\n[2/6] Preparing training data...")

MLX_DATA_DIR.mkdir(parents=True, exist_ok=True)

def convert_data(input_path, output_path):
    """Validate and convert training data to MLX format."""
    count = 0
    with open(input_path, "r") as fin, open(output_path, "w") as fout:
        for line in fin:
            if not line.strip():
                continue
            data = json.loads(line)
            if "messages" in data:
                msgs = data["messages"]
                if all("role" in m and "content" in m for m in msgs):
                    fout.write(json.dumps({"messages": msgs}) + "\n")
                    count += 1
    return count

for split in ["train", "val"]:
    src = DATA_DIR / f"{split}.jsonl"
    dst = MLX_DATA_DIR / f"{split}.jsonl"
    if src.exists():
        n = convert_data(src, dst)
        print(f"  ✅ {split}: {n} examples")
    else:
        print(f"  ❌ {split}: NOT FOUND at {src}")
        print(f"     Copy from Google Drive → data/training_data/{split}.jsonl")
        sys.exit(1)

# MLX requires a test file
test_src = DATA_DIR / "test.jsonl"
test_dst = MLX_DATA_DIR / "test.jsonl"
if test_src.exists():
    n = convert_data(test_src, test_dst)
    print(f"  ✅ test: {n} examples")
else:
    shutil.copy(MLX_DATA_DIR / "val.jsonl", test_dst)
    print(f"  ✅ test: copied from val")


# ═══════════════════════════════════════════════════════════════
# STEP 3: CONFIGURE TRAINING
# ═══════════════════════════════════════════════════════════════

print("\n[3/6] Configuring training...")

# These settings are optimized for 7B model on 24GB M4 Pro
NUM_LORA_LAYERS = 16    # Apply LoRA to 16 of 32 layers
LORA_RANK = 16          # Rank 16 (memory efficient for 24GB)
NUM_ITERS = 1000         # 500 iterations (~25 min on M4 Pro)
BATCH_SIZE = 1          # 1 to fit in 24GB
GRAD_ACCUMULATE = 4     # Effective batch = 4
LEARNING_RATE = 1e-5    # Conservative for MLX
MAX_SEQ_LEN = 2048

ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)

est_time = NUM_ITERS * 3 / 60  # ~3 sec per iter on M4 Pro
print(f"  Model:          {BASE_MODEL}")
print(f"  LoRA rank:      {LORA_RANK}")
print(f"  LoRA layers:    {NUM_LORA_LAYERS}/32")
print(f"  Iterations:     {NUM_ITERS}")
print(f"  Batch size:     {BATCH_SIZE} (effective: {BATCH_SIZE * GRAD_ACCUMULATE})")
print(f"  Learning rate:  {LEARNING_RATE}")
print(f"  Max seq length: {MAX_SEQ_LEN}")
print(f"  Est. time:      ~{est_time:.0f} minutes")


# ═══════════════════════════════════════════════════════════════
# STEP 4: TRAIN!
# ═══════════════════════════════════════════════════════════════

print(f"\n[4/6] Starting fine-tuning...")
print(f"  First run downloads the model (~5GB). Subsequent runs skip this.")
print(f"  Watch the loss values — they should decrease over time.\n")

train_cmd = [
    sys.executable, "-m", "mlx_lm", "lora",    # New syntax: mlx_lm lora (not mlx_lm.lora)
    "--model", BASE_MODEL,
    "--train",
    "--data", str(MLX_DATA_DIR),
    "--adapter-path", str(ADAPTERS_DIR),
    "--num-layers", str(NUM_LORA_LAYERS),       # Was --lora-layers
    "--batch-size", str(BATCH_SIZE),
    "--iters", str(NUM_ITERS),                  # Was --num-iters
    "--learning-rate", str(LEARNING_RATE),
    "--steps-per-eval", "50",                   # Was --iters-per-eval
    "--val-batches", "10",
    "--save-every", "100",
    "--max-seq-length", str(MAX_SEQ_LEN),
    "--grad-checkpoint",
]

print(f"  Command: {' '.join(train_cmd[:8])}...\n")
print("─" * 60)

result = subprocess.run(train_cmd, cwd=str(PROJECT_ROOT))

if result.returncode != 0:
    print("\n  ❌ Training failed! Common fixes:")
    print("    - Out of memory: reduce --lora-layers to 8 or --max-seq-length to 1024")
    print("    - Model download fail: check internet connection")
    print("    - Try running the command directly in terminal to see full error:")
    print(f"    {' '.join(train_cmd)}")
    sys.exit(1)

print("─" * 60)
print("\n  ✅ Fine-tuning complete!")
print(f"  Adapter saved to: {ADAPTERS_DIR}")


# ═══════════════════════════════════════════════════════════════
# STEP 5: TEST THE FINE-TUNED MODEL
# ═══════════════════════════════════════════════════════════════

print(f"\n[5/6] Testing fine-tuned model...")
print("  Generating a test response...\n")
print("─" * 60)

test_cmd = [
    sys.executable, "-m", "mlx_lm", "generate",    # New syntax
    "--model", BASE_MODEL,
    "--adapter-path", str(ADAPTERS_DIR),
    "--prompt", "What is 25% off a $40 shirt?",
    "--max-tokens", "512",
]

subprocess.run(test_cmd)
print("─" * 60)


# ═══════════════════════════════════════════════════════════════
# STEP 6: CREATE OLLAMA MODEL
# ═══════════════════════════════════════════════════════════════

print(f"\n[6/6] Creating Ollama model...")

# Fuse adapter with base model
FUSED_DIR.mkdir(parents=True, exist_ok=True)

print("  Step 6a: Fusing adapter with base model...")
fuse_cmd = [
    sys.executable, "-m", "mlx_lm", "fuse",        # New syntax
    "--model", BASE_MODEL,
    "--adapter-path", str(ADAPTERS_DIR),
    "--save-path", str(FUSED_DIR),
]

fuse_result = subprocess.run(fuse_cmd, capture_output=True, text=True)
if fuse_result.returncode == 0:
    print(f"  ✅ Fused model saved to: {FUSED_DIR}")
else:
    print(f"  ⚠️  Fuse issue: {fuse_result.stderr[:300]}")

# Create Ollama Modelfile
modelfile_path = MODELS_DIR / "Modelfile"

modelfile_content = f"""FROM {FUSED_DIR}

SYSTEM \"\"\"You are a helpful reasoning assistant. For every question, you MUST:
1. First think through the problem step-by-step inside <thinking>...</thinking> tags.
2. Then provide a clear, concise answer after the thinking block.

Your thinking should show every step of your reasoning. Be thorough but focused.
Write all math in plain text. Do NOT use LaTeX or backslashes.\"\"\"

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER num_predict 1024
PARAMETER repeat_penalty 1.1
"""

with open(modelfile_path, "w") as f:
    f.write(modelfile_content)

print(f"  ✅ Modelfile created")

# Print final instructions
print(f"""
{'='*60}
  FINE-TUNING COMPLETE!
{'='*60}

  What was created:
    {ADAPTERS_DIR}/    ← LoRA adapter weights
    {FUSED_DIR}/       ← Fused model (base + adapter)
    {modelfile_path}   ← Ollama model definition

  NEXT STEPS — Run these commands in terminal:

  Step A: Create the Ollama model:
    ollama create enterprise-llm -f {modelfile_path}

  Step B: Test it:
    ollama run enterprise-llm "What is 25% off a $40 shirt?"

  Step C: Use in your project:
    python -m src.main --model enterprise-llm
    python -m src.api.server  (update config.yaml first)

  Step D: Update config/config.yaml:
    model:
      base_model: "enterprise-llm"

  Now your Web UI and API use YOUR fine-tuned model!
""")
