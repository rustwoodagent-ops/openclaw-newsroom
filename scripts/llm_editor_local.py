#!/usr/bin/env python3
"""
llm_editor_local.py - AI Editor for Automated News Scanning (LOCAL MODEL VERSION)
================================================================================
Uses local Ollama models (Qwen3 30B or DeepSeek) for story curation instead of cloud APIs.
Zero API costs. Runs entirely on local hardware.
"""

import argparse
import json
import os
import re
import sys
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

try:
    from dedup_db import DedupDB, normalize_url
    HAS_DEDUP_DB = True
except ImportError:
    HAS_DEDUP_DB = False

# ── Paths ────────────────────────────────────────────────────────────
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                os.path.expanduser("~/.openclaw/workspace")))
MEMORY = WORKSPACE / "memory"
EDITORIAL_PROFILE = MEMORY / "editorial_profile.md"
SCANNER_PRESENTED = MEMORY / "scanner_presented.md"
NEWS_LOG = MEMORY / "news_log.md"

# ── Local Model Configuration ────────────────────────────────────────
# Uses Ollama local models - zero API cost
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
LOCAL_MODELS = [
    {
        "name": "GLM 4.7 Flash (Primary Fast)",
        "model": "glm-4.7-flash:latest",
        "timeout": 90,
    },
    {
        "name": "Qwen2.5 Abliterate 14B (Fallback)",
        "model": "huihui_ai/qwen2.5-abliterate:14b",
        "timeout": 120,
    },
    {
        "name": "Qwen3 30B (Deep Fallback)",
        "model": "qwen3:30b-a3b",
        "timeout": 150,
    },
]

TEMPERATURE = 0.3
MAX_ARTICLES = 500

VALID_CATEGORIES = {
    "ai_product", "m_and_a", "model_release", "security", "geopolitics",
    "github_trending", "gaming", "fintech", "hardware", "open_source", "other"
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[llm_editor {ts}] {msg}", file=sys.stderr)


def call_ollama(model, prompt, temperature=0.3, timeout=120):
    """Call local Ollama model via HTTP API with bounded output and hard timeout."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 900,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            obj = json.loads(body)
            return obj.get("response", "")
    except urllib.error.HTTPError as e:
        log(f"Ollama HTTP error {e.code} for {model}")
        return None
    except Exception as e:
        log(f"Error calling Ollama {model}: {e}")
        return None


def estimate_tokens(text):
    return len(text) // 4


def parse_articles(filepath):
    articles = []
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    articles.append({
                        "title": parts[0],
                        "url": parts[1],
                        "source": parts[2],
                        "tier": parts[3] if len(parts) > 3 else "general"
                    })
    except Exception as e:
        log(f"Error parsing articles: {e}")
    return articles


def load_editorial_profile():
    """Load Howard's editorial voice and preferences."""
    default_profile = """# Howard's Editorial Profile

## Voice & Tone
- Australian, direct, sharp wit
- Not mean-spirited, not cynical for its own sake
- Smart observations with dry humor
- Like a mate telling you what happened over coffee
- Confident but not arrogant

## Content Priorities (High to Low)
1. **AI/ML breakthroughs** - Models, research, practical applications
2. **Tech industry moves** - M&A, product launches, strategic shifts
3. **Security issues** - Vulnerabilities, breaches, fixes
4. **Open source** - Important releases, community moves
5. **Hardware/chips** - New processors, efficiency gains
6. **Weird/funny tech** - Absurd AI moments, ridiculous products

## What to Skip
- Incremental product updates (iPhone 17 rumor #47)
- Pure marketing fluff
- Cryptocurrency unless major institutional move
- Celebrity AI opinions

## How to Pick Stories
- Would a smart person care about this in 48 hours?
- Does it change how things work or just iterate?
- Is there genuine insight or just hype?
- Would this be worth explaining to a mate at the pub?

## Category Tags to Use
- ai_product, model_release, security, geopolitics
- github_trending, gaming, fintech, hardware, open_source, other"""
    
    if EDITORIAL_PROFILE.exists():
        return EDITORIAL_PROFILE.read_text()
    return default_profile


def build_prompt(articles, profile, recent_history=""):
    """Build the curation prompt for Howard."""
    
    articles_text = "\n".join([
        f"{i+1}. {a['title']} | {a['url']} | Source: {a['source']}"
        for i, a in enumerate(articles[:50])  # Limit to top 50 for context window
    ])
    
    prompt = f"""You are Howard, an AI news correspondent with Australian wit and sharp insight.

Your editorial profile:
{profile}

Recent stories you've already covered (avoid repeats):
{recent_history[:2000]}

Candidate articles to curate:
{articles_text}

Your task: Select the top 5-7 most interesting, significant, or entertaining stories.

For each selected story, provide:
- rank: 1-7
- title: exact title
- url: exact URL  
- source: where it came from
- summary: 1-2 sentences explaining why it matters (in Howard's voice)
- category: one of [ai_product, model_release, security, geopolitics, github_trending, gaming, fintech, hardware, open_source, other]

Output format: Return ONLY a JSON array, no markdown, no explanation:
[{{"rank": 1, "title": "...", "url": "...", "source": "...", "summary": "...", "category": "..."}}, ...]

Be selective. Quality over quantity. Pick stories a smart person would actually want to know about."""

    return prompt


def parse_llm_output(output):
    """Parse JSON output from local model."""
    if not output:
        return []
    
    # Try to find JSON array in output
    try:
        # Look for array between brackets
        match = re.search(r'\[.*\]', output, re.DOTALL)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError as e:
        log(f"JSON parse error: {e}")
    
    # Fallback: try to parse line by line
    picks = []
    for line in output.strip().split('\n'):
        line = line.strip()
        if line and line.startswith('{'):
            try:
                picks.append(json.loads(line))
            except:
                pass
    
    return picks


def log_presented(articles):
    """Log what was presented to scanner_presented.md"""
    with open(SCANNER_PRESENTED, "a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        for a in articles:
            f.write(f"- {a['title']} ({a['source']})\n")


def main():
    parser = argparse.ArgumentParser(description="Howard's AI News Editor (Local Models)")
    parser.add_argument("--file", required=True, help="Path to candidates file")
    parser.add_argument("--github", help="Path to GitHub candidates file")
    parser.add_argument("--top", type=int, default=7, help="Number of stories to pick")
    args = parser.parse_args()
    
    log(f"Starting Howard's news curation (local models)")
    log(f"Loading candidates from: {args.file}")
    
    # Load articles
    articles = parse_articles(args.file)
    if args.github:
        articles.extend(parse_articles(args.github))
    
    if not articles:
        log("No articles to curate")
        return
    
    log(f"Loaded {len(articles)} candidates")
    
    # Load profile
    profile = load_editorial_profile()
    
    # Load recent history (avoid repeats)
    recent_history = ""
    if NEWS_LOG.exists():
        recent_history = NEWS_LOG.read_text()[-3000:]  # Last 3000 chars
    
    # Build prompt
    prompt = build_prompt(articles, profile, recent_history)
    log(f"Prompt size: ~{len(prompt)} chars (~{estimate_tokens(prompt)} tokens)")
    
    # Try local models in failover chain
    picks = []
    for model_config in LOCAL_MODELS:
        if picks:
            break
        
        log(f"Trying {model_config['name']}...")
        output = call_ollama(
            model_config['model'],
            prompt,
            TEMPERATURE,
            model_config['timeout']
        )
        
        if output:
            picks = parse_llm_output(output)
            if picks:
                log(f"Success with {model_config['name']}: {len(picks)} picks")
                break
    
    if not picks:
        log("ERROR: All local models failed")
        sys.exit(1)
    
    # Validate and clean picks
    valid_picks = []
    for p in picks[:args.top]:
        if 'title' in p and 'url' in p:
            p['type'] = p.get('source', 'rss').lower()
            if p.get('category') not in VALID_CATEGORIES:
                p['category'] = 'other'
            valid_picks.append(p)
    
    # Output JSON
    for pick in valid_picks:
        print(json.dumps(pick))
    
    # Log presented
    log_presented(valid_picks)
    log(f"Curated {len(valid_picks)} stories")


if __name__ == "__main__":
    main()
