#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# howard_news_scan.sh — Simplified News Scan for Local Models
# ═══════════════════════════════════════════════════════════════════
#
# Uses local Ollama models (Qwen3, DeepSeek) instead of cloud APIs.
# Zero cost. Runs entirely on local hardware.
#
# Usage:
#   ./howard_news_scan.sh              # default: top 5 picks
#   ./howard_news_scan.sh --top 7      # top 7 picks
# ═══════════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
MEMORY="$WORKSPACE/memory"

# Auto-detect Windows-hosted Ollama from WSL default gateway unless already set
if [[ -z "${OLLAMA_BASE_URL:-}" ]]; then
  GW=$(ip route | awk '/default/ {print $3; exit}')
  export OLLAMA_BASE_URL="http://${GW}:11434"
fi

# ── Parse arguments ──────────────────────────────────────────────────
TOP_N=5

while [[ $# -gt 0 ]]; do
  case $1 in
    --top) TOP_N="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--top N]"
      echo "  --top N   Number of stories to curate (default: 5)"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

export TOP_N

# ── Ensure directories exist ─────────────────────────────────────────
mkdir -p "$MEMORY"
mkdir -p "$WORKSPACE/generated"
mkdir -p "$WORKSPACE/.logs"

# ── Temp files ───────────────────────────────────────────────────────
CANDIDATES_FILE=$(mktemp /tmp/howard_news_candidates.XXXXXX)
PICKS_FILE=$(mktemp /tmp/howard_news_picks.XXXXXX)
OUTPUT_JSON="$WORKSPACE/generated/howard-news-$(date +%Y-%m-%d).json"
OUTPUT_SCRIPT="$WORKSPACE/generated/howard-news-$(date +%Y-%m-%d)-script.txt"

cleanup() {
  rm -f "$CANDIDATES_FILE" "$PICKS_FILE"
}
trap cleanup EXIT

echo "═══════════════════════════════════════════════════════════"
echo "  Howard News Scanner (Local Models)"
echo "  Target: $TOP_N stories"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ═════════════════════════════════════════════════════════════════════
# SOURCE 1: RSS Feeds (using existing blogwatcher)
# ═════════════════════════════════════════════════════════════════════
echo "[1/3] Gathering news from RSS feeds..."

# Use blogwatcher if available, otherwise create sample data
if command -v blogwatcher &> /dev/null; then
    blogwatcher fetch --limit 30 --output "$CANDIDATES_FILE" 2>/dev/null || true
fi

# Add some tech news RSS feeds if file is empty
if [[ ! -s "$CANDIDATES_FILE" ]]; then
    echo "TechCrunch AI|https://techcrunch.com/category/artificial-intelligence/|RSS|tech"
    echo "The Verge AI|https://www.theverge.com/ai-artificial-intelligence|RSS|tech" 
    echo "Ars Technica|https://arstechnica.com/tag/artificial-intelligence/|RSS|tech"
    echo "MIT Tech Review|https://www.technologyreview.com/topic/artificial-intelligence/|RSS|tech"
    echo "Wired AI|https://www.wired.com/tag/artificial-intelligence/|RSS|tech"
fi >> "$CANDIDATES_FILE"

CANDIDATES_COUNT=$(wc -l < "$CANDIDATES_FILE" 2>/dev/null || echo "0")
echo "      Found: $CANDIDATES_COUNT candidates"

# ═════════════════════════════════════════════════════════════════════
# SOURCE 2: Web Search (using web_search skill)
# ═════════════════════════════════════════════════════════════════════
echo "[2/3] Searching for breaking news..."

# This would integrate with Howard's web_search tool
# For now, add placeholder entries that will be enriched
{
    echo "Latest AI developments today|https://news.ycombinator.com/|HackerNews|tech"
    echo "OpenAI news and updates|https://openai.com/blog/|OpenAI|ai_product"
    echo "Google AI announcements|https://blog.google/technology/ai/|Google|ai_product"
} >> "$CANDIDATES_FILE"

echo "      Added: Web search candidates"

# ═════════════════════════════════════════════════════════════════════
# STEP 3: AI Curation with Local Models
# ═════════════════════════════════════════════════════════════════════
echo "[3/3] Howard is curating stories with local models..."
echo "      (This may take 2-5 minutes on CPU)"
echo ""

# Check if Ollama is running
if ! curl -s "${OLLAMA_BASE_URL}/api/tags" > /dev/null 2>&1; then
    echo "ERROR: Ollama is not running at ${OLLAMA_BASE_URL}. Start it with: ollama serve"
    exit 1
fi

# Run the local editor
python3 "$SCRIPT_DIR/llm_editor_local.py" \
    --file "$CANDIDATES_FILE" \
    --top "$TOP_N" \
    > "$PICKS_FILE" 2>/dev/null

PICKS_COUNT=$(wc -l < "$PICKS_FILE" 2>/dev/null || echo "0")

if [[ "$PICKS_COUNT" -eq 0 ]]; then
    echo "ERROR: No stories were curated"
    exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "  Howard selected $PICKS_COUNT stories"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ═════════════════════════════════════════════════════════════════════
# OUTPUT: Format for publishing
# ═════════════════════════════════════════════════════════════════════

# Save JSON
if command -v jq &> /dev/null; then
    jq -s '.' "$PICKS_FILE" > "$OUTPUT_JSON"
else
    echo "[" > "$OUTPUT_JSON"
    first=true
    while IFS= read -r line; do
        [[ "$first" == true ]] || echo "," >> "$OUTPUT_JSON"
        echo "$line" >> "$OUTPUT_JSON"
        first=false
    done < "$PICKS_FILE"
    echo "]" >> "$OUTPUT_JSON"
fi

# Generate script for TTS
echo "Howard's News Update — $(date +'%d %B %Y')" > "$OUTPUT_SCRIPT"
echo "" >> "$OUTPUT_SCRIPT"
echo "Morning. Here's your daily briefing." >> "$OUTPUT_SCRIPT"
echo "" >> "$OUTPUT_SCRIPT"

export PICKS_FILE_PATH="$PICKS_FILE"
export OUTPUT_SCRIPT_PATH="$OUTPUT_SCRIPT"
python3 - << 'PY'
import json, os
from pathlib import Path
picks = Path(os.environ.get("PICKS_FILE_PATH", ""))
out = Path(os.environ.get("OUTPUT_SCRIPT_PATH", ""))
if picks.exists() and out.exists():
    with out.open("a") as f:
        for line in picks.read_text().splitlines():
            line=line.strip()
            if not line:
                continue
            try:
                obj=json.loads(line)
                title=(obj.get("title") or "").strip()
                summary=(obj.get("summary") or "").strip()
                if title and summary:
                    f.write(title + "\n")
                    f.write(summary + "\n\n")
            except Exception:
                pass
PY

echo "More tomorrow." >> "$OUTPUT_SCRIPT"
echo "" >> "$OUTPUT_SCRIPT"
echo "— Howard" >> "$OUTPUT_SCRIPT"

echo "Outputs saved:"
echo "  JSON: $OUTPUT_JSON"
echo "  Script: $OUTPUT_SCRIPT"
echo ""
echo "To generate audio:"
echo "  tts --file $OUTPUT_SCRIPT --output howard-news-$(date +%Y-%m-%d).mp3"
echo ""

# Display picks
echo "Today's stories:"
export PICKS_FILE_PATH="$PICKS_FILE"
python3 - << 'PY'
import json, os
from pathlib import Path
p = Path(os.environ.get("PICKS_FILE_PATH", ""))
if p.exists():
    for line in p.read_text().splitlines():
        line=line.strip()
        if not line:
            continue
        try:
            obj=json.loads(line)
            title=obj.get("title","")
            cat=obj.get("category","other")
            if title:
                print(f"  • [{cat}] {title}")
        except Exception:
            pass
PY

echo ""
echo "Done. Howard out."
