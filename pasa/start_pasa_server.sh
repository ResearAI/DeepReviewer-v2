#!/bin/bash
# ==============================================================================
# PASA vLLM Stack Startup Script
# ==============================================================================
# Starts 3 processes:
#   1) vLLM OpenAI server for CRAWLER model (GPU1)
#   2) vLLM OpenAI server for SELECTOR model (GPU1)
#   3) PASA Flask orchestrator server (calls vLLM via HTTP)
#
# Usage:
#   bash start_pasa_server.sh                 # Start (vLLM in bg, Flask in fg)
#   bash start_pasa_server.sh --background    # Start all in background
#   bash start_pasa_server.sh --stop          # Stop all
#   bash start_pasa_server.sh --restart       # Stop then start
#
# Config:
#   Edit `pasa/.pasa_env` (or provide PASA_ENV_FILE)
#
# Logs (default):
#   /tmp/pasa_vllm_crawler.log
#   /tmp/pasa_vllm_selector.log
#   /tmp/pasa_server.log
# ==============================================================================

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==============================================================================
# Configuration
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASA_SERVER_SCRIPT="$SCRIPT_DIR/pasa_server.py"
PASA_ENV_FILE="${PASA_ENV_FILE:-$SCRIPT_DIR/.pasa_env}"

LOG_DIR="${PASA_LOG_DIR:-/tmp}"
CRAWLER_LOG="$LOG_DIR/pasa_vllm_crawler.log"
SELECTOR_LOG="$LOG_DIR/pasa_vllm_selector.log"
SERVER_LOG="$LOG_DIR/pasa_server.log"

CRAWLER_PID_FILE="$LOG_DIR/pasa_vllm_crawler.pid"
SELECTOR_PID_FILE="$LOG_DIR/pasa_vllm_selector.pid"
SERVER_PID_FILE="$LOG_DIR/pasa_server.pid"

# ==============================================================================
# Helper Functions
# ==============================================================================

print_header() {
    echo -e "${BLUE}======================================================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}======================================================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# ==============================================================================
# Pre-flight Checks
# ==============================================================================

print_header "PASA vLLM Stack Startup"

# Check if pasa_server.py exists
if [ ! -f "$PASA_SERVER_SCRIPT" ]; then
    print_error "pasa_server.py not found at: $PASA_SERVER_SCRIPT"
    exit 1
fi
print_success "Found pasa_server.py"

# Check if .pasa_env exists
if [ ! -f "$PASA_ENV_FILE" ]; then
    print_warning ".pasa_env not found at: $PASA_ENV_FILE"
    print_warning "Using system environment variables instead"
else
    print_success "Found .pasa_env configuration"
fi

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    print_error "python3 not found. Please install Python 3."
    exit 1
fi
print_success "Python 3 is available: $(python3 --version)"

# Check if vLLM is importable
if ! python3 -c "import vllm" >/dev/null 2>&1; then
    print_error "Python cannot import vllm. Please install/repair vLLM in this environment."
    print_info "Expected: `pip install vllm` and `openai>=1.52,<1.76`"
    exit 1
fi

# ==============================================================================
# Load Environment (.pasa_env)
# ==============================================================================

print_header "Environment Setup"

cd "$SCRIPT_DIR"
print_success "Working directory: $SCRIPT_DIR"

if [ -f "$PASA_ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$PASA_ENV_FILE"
    set +a
    print_success "Loaded environment from: $PASA_ENV_FILE"
else
    print_warning "No .pasa_env found; relying on current environment variables"
fi

# Defaults (in case .pasa_env is missing fields)
PASA_GPU_ID="${PASA_GPU_ID:-1}"
PASA_VLLM_HOST="${PASA_VLLM_HOST:-127.0.0.1}"
PASA_VLLM_CRAWLER_PORT="${PASA_VLLM_CRAWLER_PORT:-8101}"
PASA_VLLM_SELECTOR_PORT="${PASA_VLLM_SELECTOR_PORT:-8102}"
PASA_VLLM_CRAWLER_URL="${PASA_VLLM_CRAWLER_URL:-http://$PASA_VLLM_HOST:$PASA_VLLM_CRAWLER_PORT/v1}"
PASA_VLLM_SELECTOR_URL="${PASA_VLLM_SELECTOR_URL:-http://$PASA_VLLM_HOST:$PASA_VLLM_SELECTOR_PORT/v1}"
PASA_VLLM_CRAWLER_MODEL_NAME="${PASA_VLLM_CRAWLER_MODEL_NAME:-pasa-crawler}"
PASA_VLLM_SELECTOR_MODEL_NAME="${PASA_VLLM_SELECTOR_MODEL_NAME:-pasa-selector}"
PASA_VLLM_DTYPE="${PASA_VLLM_DTYPE:-bfloat16}"
PASA_VLLM_MAX_MODEL_LEN="${PASA_VLLM_MAX_MODEL_LEN:-4096}"
PASA_VLLM_CRAWLER_GPU_MEMORY_UTILIZATION="${PASA_VLLM_CRAWLER_GPU_MEMORY_UTILIZATION:-0.45}"
PASA_VLLM_SELECTOR_GPU_MEMORY_UTILIZATION="${PASA_VLLM_SELECTOR_GPU_MEMORY_UTILIZATION:-0.45}"

print_info "GPU: PASA_GPU_ID=$PASA_GPU_ID"
print_info "CRAWLER vLLM: $PASA_VLLM_CRAWLER_URL (model=$PASA_VLLM_CRAWLER_MODEL_NAME)"
print_info "SELECTOR vLLM: $PASA_VLLM_SELECTOR_URL (model=$PASA_VLLM_SELECTOR_MODEL_NAME)"
print_info "PASA server bind: ${PASA_SERVER_HOST:-0.0.0.0}:${PASA_SERVER_PORT:-8001}"

# ==============================================================================
# Argument Parsing
# ==============================================================================

BACKGROUND_MODE=false
DO_STOP=false
DO_RESTART=false

for arg in "${@:-}"; do
    case "$arg" in
        --background|-b) BACKGROUND_MODE=true ;;
        --stop) DO_STOP=true ;;
        --restart) DO_RESTART=true ;;
        *) ;;
    esac
done

stop_pidfile() {
    local pidfile="$1"
    local name="$2"
    if [ -f "$pidfile" ]; then
        local pid
        pid="$(cat "$pidfile" || true)"
        if [ -n "${pid:-}" ] && ps -p "$pid" >/dev/null 2>&1; then
            print_info "Stopping $name (PID: $pid)"
            kill "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$pidfile"
        print_success "Stopped $name"
    fi
}

if [ "$DO_STOP" = true ]; then
    print_header "Stopping PASA vLLM Stack"
    stop_pidfile "$SERVER_PID_FILE" "PASA server"
    stop_pidfile "$CRAWLER_PID_FILE" "vLLM crawler"
    stop_pidfile "$SELECTOR_PID_FILE" "vLLM selector"
    exit 0
fi

if [ "$DO_RESTART" = true ]; then
    print_header "Restart requested"
    stop_pidfile "$SERVER_PID_FILE" "PASA server"
    stop_pidfile "$CRAWLER_PID_FILE" "vLLM crawler"
    stop_pidfile "$SELECTOR_PID_FILE" "vLLM selector"
fi

check_already_running() {
    local pidfile="$1"
    local name="$2"
    if [ -f "$pidfile" ]; then
        local pid
        pid="$(cat "$pidfile" || true)"
        if [ -n "${pid:-}" ] && ps -p "$pid" >/dev/null 2>&1; then
            print_warning "$name is already running (PID: $pid). Use --restart or --stop."
            return 0
        fi
        rm -f "$pidfile"
    fi
    return 1
}

if check_already_running "$CRAWLER_PID_FILE" "vLLM crawler"; then exit 0; fi
if check_already_running "$SELECTOR_PID_FILE" "vLLM selector"; then exit 0; fi
if check_already_running "$SERVER_PID_FILE" "PASA server"; then exit 0; fi

# ==============================================================================
# Start PASA Server
# ==============================================================================

print_header "Starting vLLM Servers"

start_vllm_server() {
    local name="$1"
    local model_path="$2"
    local served_name="$3"
    local port="$4"
    local gpu_mem_util="$5"
    local log_file="$6"
    local pid_file="$7"

    if [ ! -d "$model_path" ]; then
        print_error "$name model path not found: $model_path"
        exit 1
    fi

    print_info "Starting $name vLLM server on $PASA_VLLM_HOST:$port (CUDA_VISIBLE_DEVICES=$PASA_GPU_ID)"
    CUDA_VISIBLE_DEVICES="$PASA_GPU_ID" nohup python3 -m vllm.entrypoints.openai.api_server \
        --host "$PASA_VLLM_HOST" \
        --port "$port" \
        --model "$model_path" \
        --served-model-name "$served_name" \
        --dtype "$PASA_VLLM_DTYPE" \
        --gpu-memory-utilization "$gpu_mem_util" \
        --max-model-len "$PASA_VLLM_MAX_MODEL_LEN" \
        --disable-log-requests \
        > "$log_file" 2>&1 &

    echo "$!" > "$pid_file"
    print_success "$name vLLM started (PID: $(cat "$pid_file"))"
    print_info "$name log: $log_file"
}

wait_for_vllm() {
    local base_url="$1"
    local name="$2"
    local timeout_s="${3:-300}"

    print_info "Waiting for $name to be ready: $base_url/models"
    if python3 - <<PY
import os, sys, time
import requests

url = "${base_url}/models"
timeout_s = int("${timeout_s}")
deadline = time.time() + timeout_s
while time.time() < deadline:
    try:
        r = requests.get(url, timeout=2)
        if r.status_code == 200:
            sys.exit(0)
    except Exception:
        pass
    time.sleep(1)
sys.exit(1)
PY
    then
        print_success "$name is ready"
    else
        print_error "$name not ready after ${timeout_s}s. Check logs:"
        print_error "  $CRAWLER_LOG"
        print_error "  $SELECTOR_LOG"
        exit 1
    fi
}

start_vllm_server "CRAWLER" "$PASA_CRAWLER_PATH" "$PASA_VLLM_CRAWLER_MODEL_NAME" "$PASA_VLLM_CRAWLER_PORT" "$PASA_VLLM_CRAWLER_GPU_MEMORY_UTILIZATION" "$CRAWLER_LOG" "$CRAWLER_PID_FILE"
wait_for_vllm "$PASA_VLLM_CRAWLER_URL" "CRAWLER vLLM"

# Start selector after crawler is fully ready to avoid concurrent init spikes
start_vllm_server "SELECTOR" "$PASA_SELECTOR_PATH" "$PASA_VLLM_SELECTOR_MODEL_NAME" "$PASA_VLLM_SELECTOR_PORT" "$PASA_VLLM_SELECTOR_GPU_MEMORY_UTILIZATION" "$SELECTOR_LOG" "$SELECTOR_PID_FILE"
wait_for_vllm "$PASA_VLLM_SELECTOR_URL" "SELECTOR vLLM"

print_header "Starting PASA Orchestrator Server"

if [ "$BACKGROUND_MODE" = true ]; then
    print_info "Starting PASA server in background mode..."
    print_info "Log file: $SERVER_LOG"
    nohup python3 "$PASA_SERVER_SCRIPT" > "$SERVER_LOG" 2>&1 &
    echo "$!" > "$SERVER_PID_FILE"
    print_success "PASA server started (PID: $(cat "$SERVER_PID_FILE"))"
    print_info "Tail logs: tail -f $SERVER_LOG"
else
    print_info "Starting PASA server in foreground mode..."
    print_warning "Press Ctrl+C to stop the server"
    echo ""
    python3 "$PASA_SERVER_SCRIPT"
fi

print_header "PASA vLLM Stack Ready"
