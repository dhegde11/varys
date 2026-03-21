#!/usr/bin/env python3
"""
Cached integration test for research_entity_async.

On the first run it calls the API and saves each round's state to .cache/.
On subsequent runs it loads from cache and skips already-completed rounds,
so you can iterate on round 2+ bugs without burning API credits on round 1.

Usage:
    # Single entity (quick):
    python3 tests/test_research_cached.py --skill profile-health-it-vendor --entity "Ambience Healthcare"
    python3 tests/test_research_cached.py --skill profile-health-system --entity "Penn Medicine"

    # Multiple entities from CSV:
    python3 tests/test_research_cached.py --skill profile-health-it-vendor --input tests/test_vendor.csv

Clear cache for a fresh run:
    rm -rf .cache/
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# varys.py has a hyphen so standard import doesn't work; use importlib
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "varys",
    Path(__file__).parent.parent / "varys.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

load_skill = _mod.load_skill
parse_json_response = _mod.parse_json_response

from anthropic import AsyncAnthropic

CACHE_DIR = Path(__file__).parent.parent / ".cache"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(skill_name: str, entity_name: str, round_num: int) -> Path:
    safe = re.sub(r"[^a-z0-9]+", "_", entity_name.lower()).strip("_")
    return CACHE_DIR / f"{skill_name}__{safe}__round{round_num}.json"


def _serialize_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if hasattr(item, "model_dump"):
                out.append(item.model_dump())
            elif isinstance(item, dict):
                out.append(item)
            else:
                out.append(str(item))
        return out
    return content


def _save_cache(skill_name, entity_name, round_num, messages, container_id):
    CACHE_DIR.mkdir(exist_ok=True)
    data = {
        "container_id": container_id,
        "messages": [
            {"role": m["role"], "content": _serialize_content(m["content"])}
            for m in messages
        ],
    }
    path = _cache_path(skill_name, entity_name, round_num)
    path.write_text(json.dumps(data, indent=2))
    print(f"  [cache] saved → {path.name}", flush=True)


def _load_cache(skill_name, entity_name):
    """Return (last_round, messages, container_id) or None."""
    for round_num in range(50, 0, -1):
        path = _cache_path(skill_name, entity_name, round_num)
        if path.exists():
            data = json.loads(path.read_text())
            print(f"  [cache] loaded ← {path.name}", flush=True)
            return round_num, data["messages"], data.get("container_id")
    return None


# ---------------------------------------------------------------------------
# Main research loop (same logic as varys.py but with caching)
# ---------------------------------------------------------------------------

async def research_with_cache(entity_name: str, skill_name: str, model: str):
    skill = load_skill(skill_name)

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
        {"type": "web_fetch_20260209",  "name": "web_fetch",  "max_uses": 2},
    ]

    # Load from cache if available
    cached = _load_cache(skill_name, entity_name)
    if cached:
        start_round, messages, _ = cached
        print(f"  Resuming from round {start_round + 1} (skipping {start_round} cached rounds)\n")
    else:
        start_round = 0
        messages = [{"role": "user", "content": skill.prompt_template.format(entity=entity_name)}]

    client = AsyncAnthropic(timeout=600.0)

    for round_num in range(start_round, skill.max_tool_rounds):
        print(f"  [round {round_num + 1}] calling API...", flush=True)
        response = await client.messages.create(
            model=model, max_tokens=4096, tools=tools, messages=messages
        )

        block_types = [getattr(b, "type", "?") for b in response.content]
        print(f"  [round {round_num + 1}] stop_reason={response.stop_reason}", flush=True)
        print(f"  [round {round_num + 1}] blocks={block_types}", flush=True)

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), None)
            if not text:
                raise RuntimeError("end_turn but no text block in response")
            return parse_json_response(text, entity_name)

        messages.append({"role": "assistant", "content": response.content})

        # Save state after each round so next run can resume here
        _save_cache(skill_name, entity_name, round_num + 1, messages, None)

    raise RuntimeError(f"Reached {skill.max_tool_rounds} rounds without end_turn")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True, choices=["profile-health-it-vendor", "profile-health-system"])
    parser.add_argument("--entity", help="Single entity name to research (e.g. \"Ambience Healthcare\")")
    parser.add_argument("--input", help="CSV file with entity_name column (alternative to --entity)")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    args = parser.parse_args()

    if not args.entity and not args.input:
        parser.error("Either --entity or --input is required.")

    if args.entity:
        entities = [args.entity.strip()]
    else:
        import csv
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        with open(input_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if "entity_name" not in (reader.fieldnames or []):
                print("ERROR: Input CSV must have an 'entity_name' column.", file=sys.stderr)
                sys.exit(1)
            entities = [row["entity_name"].strip() for row in reader if row["entity_name"].strip()]

    for entity_name in entities:
        print(f"\nResearching: {entity_name}")
        print(f"Skill:       {args.skill}")
        print(f"Model:       {args.model}\n")

        result = asyncio.run(research_with_cache(entity_name, args.skill, args.model))

        print("\n--- Result ---")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
