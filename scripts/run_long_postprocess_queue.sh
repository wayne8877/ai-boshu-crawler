#!/usr/bin/env bash
# 替代 dragon-hh 原 run_long_postprocess_queue.ps1（PowerShell）。
# macOS / Linux 通用。
#
# 用法：
#   ./scripts/run_long_postprocess_queue.sh [bvid1 bvid2 ...]
#   ./scripts/run_long_postprocess_queue.sh --from-file bvid-list.txt
#
# 走 mlx-whisper / openai-whisper ASR（5 分钟超时，超时自动 fallback）。
# 单条 > 1 小时的视频跳过（ASR 策略限制）。

set -uo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly DOWNLOADS_ROOT="${HS_CC_DOWNLOADS_ROOT:-$HOME/Downloads/hs-creator-crawler}"
readonly MANIFEST_DIR="$DOWNLOADS_ROOT/manifests"
readonly LOG_FILE="$MANIFEST_DIR/long-postprocess-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$MANIFEST_DIR"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

# 解析参数
declare -a BVIDS=()
if [[ $# -eq 0 ]]; then
    log "ERROR: 请提供至少一个 bvid，或 --from-file <list.txt>"
    exit 2
fi

if [[ "$1" == "--from-file" ]]; then
    if [[ ! -f "$2" ]]; then
        log "ERROR: 文件不存在 $2"
        exit 2
    fi
    while IFS= read -r line; do
        [[ -n "$line" && "$line" != \#* ]] && BVIDS+=("$line")
    done < "$2"
else
    BVIDS=("$@")
fi

log "QUEUE_START bvid_count=${#BVIDS[@]} log=$LOG_FILE"

EXIT_CODE=0
for bvid in "${BVIDS[@]}"; do
    log "START $bvid"
    cd "$PROJECT_ROOT"
    if hs-crawler ingest \
        --config "$PROJECT_ROOT/config.json" \
        --max-creators 0 \
        --no-skip-existing \
        --no-transcribe \
        --no-sync-to-feishu \
        2>&1 | tee -a "$LOG_FILE"; then
        log "END $bvid exit=0"
    else
        rc=$?
        log "END $bvid exit=$rc"
        EXIT_CODE=1
    fi
done

log "QUEUE_DONE exit=$EXIT_CODE"
exit $EXIT_CODE
