#!/usr/bin/env bash
# Start llama-cpp-python OpenAI-compatible server with local Gemma-3-4B Q4_K_M
# Usage: bash run_gemma_server.sh
# NOTE: The pipeline now loads the GGUF in-process automatically (no server needed).
#       Use this script only if you want to share one loaded model across multiple
#       parallel pipeline processes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${SCRIPT_DIR}/models/google_gemma-3-4b-it-Q4_K_M.gguf"
PORT=8000
N_CTX=4096
N_THREADS=32  # one NUMA node; using all 128 threads hurts cross-socket performance

if [[ ! -f "$MODEL" ]]; then
    echo "Model not found at $MODEL"
    exit 1
fi

echo "Starting Gemma-3-4B Q4_K_M server on port $PORT (CPU, threads=$N_THREADS) ..."
echo "Pipeline will auto-detect the server — no env vars needed."
echo ""

conda run -n ensembl python -m llama_cpp.server \
    --model "$MODEL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --n_ctx "$N_CTX" \
    --n_threads "$N_THREADS" \
    --chat_format gemma
