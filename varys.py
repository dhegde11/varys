#!/usr/bin/env python3
"""
Health IT market intelligence orchestrator.

Profiles health IT vendors (competitive intelligence) and health systems
(BD prospecting) using the Anthropic Messages API with built-in web search.

Skill files in .claude/skills/ define the research prompts and are also
invokable directly from Claude Code for single-company lookups.

Requires ANTHROPIC_API_KEY environment variable.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...

    # Discover health IT vendors (interactive query prompt)
    python varys.py discover vendor --output vendors.csv

    # Discover health systems by state (seeds from CMS public data)
    python varys.py discover health-system --state CA --output ca-hospitals.csv

    # Profile health IT vendors
    python varys.py profile vendor --input vendors.csv --output results.csv

    # Profile health systems
    python varys.py profile health-system --input ca-hospitals.csv --output results.csv
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
import anthropic
from anthropic import AsyncAnthropic

# Load .env file if present (so ANTHROPIC_API_KEY doesn't need to be exported manually)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            if not os.environ.get(_k.strip()):
                os.environ[_k.strip()] = _v.strip()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Skill name → output field list mapping (defines CSV schema)
SKILL_FIELDS = {
    "profile-health-it-vendor": [
        "entity_name",
        "product_category",
        "primary_customer",
        "ehr_integrations",
        "notable_health_system_customers",
        "business_model",
        "fda_status",
        "clinical_evidence",
        "funding_stage",
        "total_funding",
        "key_investors",
        "num_employees",
        "headquarters",
        "founded_year",
    ],
    "profile-health-system": [
        "entity_name",
        "health_system",
        "bed_count",
        "ownership_type",
        "ehr_vendor",
        "cms_star_rating",
        "teaching_hospital",
        "vbc_participation",
        "payer_mix",
        "annual_revenue",
        "innovation_program",
        "recent_tech_announcements",
        "cio_name",
        "geographic_region",
    ],
}

# ---------------------------------------------------------------------------
# Structured output schemas — enforced via output_config on each API call.
# Guarantees valid JSON at end_turn so we can use json.loads() safely.
# ---------------------------------------------------------------------------

_FIELD_VALUE_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {
            "anyOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
            ]
        },
        "source_url": {"type": ["string", "null"]},
        "confidence": {"type": ["string", "null"]},
    },
    "required": ["value", "source_url", "confidence"],
    "additionalProperties": False,
}


def _make_output_schema(field_names: list[str]) -> dict:
    """Build a JSON schema for a research profile with the given field names."""
    props = {"entity_name": {"type": "string"}}
    props.update({f: {"$ref": "#/$defs/FieldValue"} for f in field_names})
    props["research_notes"] = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
        "$defs": {"FieldValue": _FIELD_VALUE_SCHEMA},
    }


# Built once at import time; keyed by skill name
SKILL_OUTPUT_SCHEMAS = {
    skill_name: _make_output_schema(fields[1:])  # fields[0] is entity_name, added separately
    for skill_name, fields in SKILL_FIELDS.items()
}


# Cost estimate constants (rough median per entity, claude-sonnet-4-6 with web tools)
COST_PER_ENTITY_LOW  = 0.15   # USD
COST_PER_ENTITY_HIGH = 0.40   # USD
AVG_SECONDS_PER_ENTITY = 45   # wall-clock seconds at typical tool round count


# ---------------------------------------------------------------------------
# Skill loader
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    name: str
    description: str
    mode: str
    max_tool_rounds: int
    prompt_template: str  # Markdown body with {entity} placeholder


def load_skill(skill_name: str) -> Skill:
    """
    Load a skill from .claude/skills/<skill_name>/SKILL.md.

    Parses YAML frontmatter (between the first two '---' lines) and uses
    the remaining Markdown as the prompt template.
    """
    skill_path = (
        Path(__file__).parent / ".claude" / "skills" / skill_name / "SKILL.md"
    )
    if not skill_path.exists():
        print(
            f"ERROR: Skill file not found: {skill_path}\n"
            f"Available skills are in .claude/skills/",
            file=sys.stderr,
        )
        sys.exit(1)

    text = skill_path.read_text(encoding="utf-8")

    # Split on frontmatter delimiters: "---\n...\n---\n<body>"
    parts = text.split("---", maxsplit=2)
    if len(parts) < 3:
        print(
            f"ERROR: Skill file {skill_path} is missing YAML frontmatter.",
            file=sys.stderr,
        )
        sys.exit(1)

    meta = yaml.safe_load(parts[1])

    if not meta or not isinstance(meta, dict):
        print(
            f"ERROR: Skill file {skill_path} has empty or invalid YAML frontmatter.",
            file=sys.stderr,
        )
        sys.exit(1)

    if "name" not in meta:
        print(
            f"ERROR: Skill file {skill_path} YAML frontmatter is missing required 'name' field.",
            file=sys.stderr,
        )
        sys.exit(1)

    prompt_body = parts[2].strip()

    return Skill(
        name=meta["name"],
        description=meta.get("description", ""),
        mode=meta.get("mode", "vendor"),
        max_tool_rounds=meta.get("max_tool_rounds", 12),
        prompt_template=prompt_body,
    )


# ---------------------------------------------------------------------------
# Output schema helpers
# ---------------------------------------------------------------------------

def sources_fieldnames(fields: list[str]) -> list[str]:
    """Expand field list into (value, source_url, confidence) triples for sources CSV."""
    result = ["entity_name"]
    for f in fields[1:]:  # entity_name is already first
        result += [f, f"{f}_source", f"{f}_confidence"]
    result.append("research_notes")
    return result


# ---------------------------------------------------------------------------
# API call — single async implementation used for both sequential and batch
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# read_file tool — client-side execution for progressive disclosure
# ---------------------------------------------------------------------------

# Paths the agent is allowed to read (whitelist prevents arbitrary filesystem access)
_ALLOWED_READ_PREFIX = Path(__file__).parent / ".claude" / "skills"


def _execute_read_file(path_str: str) -> str:
    """
    Execute a read_file tool call from the agent.

    Only files under .claude/skills/ are readable — the agent should only
    need reference documents, not arbitrary filesystem paths.
    """
    requested = (_ALLOWED_READ_PREFIX.parent.parent / path_str).resolve()
    allowed   = _ALLOWED_READ_PREFIX.resolve()

    if not str(requested).startswith(str(allowed)):
        return f"ERROR: read_file is restricted to .claude/skills/ — cannot read {path_str}"

    if not requested.exists():
        return f"ERROR: File not found: {path_str}"

    return requested.read_text(encoding="utf-8")


READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Read a local reference file. Use this to look up field definitions, "
        "allowed enum values, confidence calibration rules, or source priority "
        "guidance when you are uncertain about a field. "
        "Only files under .claude/skills/ are accessible."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to the file, relative to the project root. "
                    "Example: .claude/skills/profile-health-it-vendor/references/field-definitions.md"
                ),
            }
        },
        "required": ["path"],
    },
}


async def _heartbeat(prefix: str, interval: float = 60.0) -> None:
    """Print a progress line every `interval` seconds while an API call is in flight."""
    elapsed = 0
    while True:
        await asyncio.sleep(interval)
        elapsed += interval
        print(f"  {prefix} | {int(elapsed)}s waiting...", flush=True)


async def discover_vendors_via_llm(query: str, model: str) -> list[str]:
    """
    Call the Anthropic Messages API with the discover-health-it-vendor
    skill to build a company list from a natural language query.

    One-shot (not iterative) — runs a single agentic loop and parses the JSON
    list of company names from the response. Returns a plain list of strings
    ready to feed into the research pipeline.
    """
    skill = load_skill("discover-health-it-vendor")
    prompt = skill.prompt_template.format(query=query)
    client = AsyncAnthropic(timeout=600.0)
    messages = [{"role": "user", "content": prompt}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5, "allowed_callers": ["direct"]},
        {"type": "web_fetch_20260209",  "name": "web_fetch",  "max_uses": 3, "allowed_callers": ["direct"]},
    ]

    container_id = None
    for _ in range(skill.max_tool_rounds):
        stream_kwargs = dict(model=model, max_tokens=2048, tools=tools, messages=messages)
        if container_id:
            stream_kwargs["container"] = container_id
        response = await client.messages.create(**stream_kwargs)
        if response.container:
            container_id = response.container.id

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")),
                None,
            )
            if not text:
                raise RuntimeError("Discovery: end_turn but no text block in response")
            data = parse_json_response(text, query)
            companies = data.get("companies", [])
            if not companies:
                raise ValueError("Discovery returned an empty company list")
            rationale = data.get("rationale", "")
            if rationale:
                print(f"  {rationale[:140]}{'...' if len(rationale) > 140 else ''}")
            return [c.strip() for c in companies if c.strip()]

        messages.append({"role": "assistant", "content": response.content})

        # pause_turn: server-side tools hit iteration limit. Resume automatically.
        if response.stop_reason == "pause_turn":
            continue

        # Any other stop_reason (e.g. max_tokens) is unexpected — fail fast.
        raise RuntimeError(
            f"Discovery: unexpected stop_reason={response.stop_reason!r} at round {_+1}"
        )

    raise RuntimeError(
        f"Discovery: reached {skill.max_tool_rounds} tool rounds without end_turn"
    )


def _trim_tool_results(messages: list[dict], keep_rounds: int = 1) -> None:
    """Truncate large tool-result content from older rounds in-place.

    By the time round R runs, the model has already synthesized raw web
    content from rounds 0..R-2 into its assistant text blocks. Keeping full
    page bodies in context just burns tokens. We keep `keep_rounds` most
    recent assistant rounds intact and truncate everything older.
    """
    MAX_CHARS = 500
    NOTE = " [truncated]"
    assistant_positions = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(assistant_positions) <= keep_rounds:
        return
    cutoff_idx = assistant_positions[-(keep_rounds + 1)]
    for msg in messages[: cutoff_idx + 1]:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in ("server_tool_result", "tool_result"):
                continue
            inner = block.get("content")
            if isinstance(inner, str) and len(inner) > MAX_CHARS:
                block["content"] = inner[:MAX_CHARS] + NOTE
            elif isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text", "")
                        if len(text) > MAX_CHARS:
                            item["text"] = text[:MAX_CHARS] + NOTE


async def research_entity_async(
    client: AsyncAnthropic, entity_name: str, skill: Skill, model: str,
    *,
    entity_idx: int = 1,
    total: int = 1,
    run_start: Optional[float] = None,
) -> dict:
    """
    Call the Anthropic Messages API with built-in web search/fetch tools and a
    client-side read_file tool for progressive disclosure of reference documents.

    Runs the agentic loop until the model returns end_turn with a text response.
    Each call is isolated: a fresh message history is created per entity so
    no context leaks between companies.

    Progressive disclosure: the agent reads reference files (field definitions,
    source priority) only when it decides it needs them — not upfront every time.
    """
    # Split the skill template into a cacheable static block (same for all entities)
    # and a dynamic block (entity-specific). The static block is cached for 1 hour
    # by the API after the first request — subsequent entities pay ~10% of normal cost.
    static_text = skill.prompt_template.replace("{entity}", "[ENTITY]")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": static_text,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                },
                {
                    "type": "text",
                    "text": f'Research: "{entity_name}"',
                },
            ],
        }
    ]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3, "allowed_callers": ["direct"]},
        {"type": "web_fetch_20260209",  "name": "web_fetch",  "max_uses": 2, "allowed_callers": ["direct"]},
        READ_FILE_TOOL,
    ]
    output_schema = SKILL_OUTPUT_SCHEMAS[skill.name]
    container_id = None
    if run_start is None:
        run_start = time.time()

    for round_num in range(skill.max_tool_rounds):
        elapsed_total = int(time.time() - run_start)
        print(f"  [{entity_idx}/{total}] {entity_name} | round {round_num+1} | {elapsed_total}s elapsed", flush=True)

        stream_kwargs = dict(
            model=model,
            max_tokens=4096,
            tools=tools,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": output_schema}},
        )
        if container_id:
            stream_kwargs["container"] = container_id

        heartbeat_prefix = f"[{entity_idx}/{total}] {entity_name} | round {round_num+1}"
        heartbeat = asyncio.create_task(_heartbeat(heartbeat_prefix))
        try:
            # Retry this round with exponential backoff on 429 rate limit errors.
            # Use create() (not stream()) so response.container is populated,
            # which is required for multi-round calls when code execution runs.
            for attempt in range(3):
                try:
                    response = await client.messages.create(**stream_kwargs)
                    break
                except anthropic.RateLimitError as e:
                    if attempt == 2:
                        raise
                    wait = 60 * (2 ** attempt)  # 60s, 120s
                    print(
                        f"  [{entity_idx}/{total}] {entity_name} | round {round_num+1}"
                        f" rate-limited, retrying in {wait}s...",
                        flush=True,
                    )
                    await asyncio.sleep(wait)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

        if response.container:
            container_id = response.container.id

        block_types = [getattr(b, "type", "?") for b in response.content]
        print(f"  [{entity_idx}/{total}] {entity_name} | round {round_num+1} done | stop_reason={response.stop_reason} blocks={block_types}", flush=True)

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")),
                None,
            )
            if not text:
                raise RuntimeError("end_turn but no text block in response")
            data = json.loads(text)
            data["entity_name"] = entity_name  # always authoritative from the caller
            return data

        # Convert SDK objects to dicts so we can mutate them for trimming.
        assistant_content = [
            b.model_dump() if hasattr(b, "model_dump") else b
            for b in response.content
        ]
        messages.append({"role": "assistant", "content": assistant_content})

        # Trim raw tool-result bodies from older rounds to keep context bounded.
        # The model has already synthesized earlier rounds into its text blocks,
        # so keeping full page bodies around just burns tokens.
        _trim_tool_results(messages)

        # pause_turn: server-side tools hit their iteration limit.
        # Re-send without a new user message — server resumes automatically.
        if response.stop_reason == "pause_turn":
            continue

        # web_search and web_fetch are server-side: the API runs them automatically
        # (server_tool_use blocks). read_file is client-side: it uses tool_use blocks
        # and requires the client to execute and return results.
        tool_results = []
        for b in response.content:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == "read_file":
                path = (b.input or {}).get("path", "")
                content = _execute_read_file(path)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": content,
                })
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            raise RuntimeError(
                f"Unexpected stop_reason={response.stop_reason!r} for {entity_name!r} at round {round_num+1}"
            )

    raise RuntimeError(
        f"Reached {skill.max_tool_rounds} tool rounds without end_turn"
    )


# ---------------------------------------------------------------------------
# Sequential runner — shares one event loop / client across all entities
# ---------------------------------------------------------------------------

async def _run_sequential(
    entities: list[str],
    skill: "Skill",
    model: str,
    clean_writer,
    sources_writer,
    clean_f,
    src_f,
) -> tuple[int, int]:
    """
    Run entities one at a time, sharing a single AsyncAnthropic client and
    event loop so the httpx connection pool is never closed mid-run.
    """
    client = AsyncAnthropic(timeout=600.0)
    fields = SKILL_FIELDS[skill.name]
    total = len(entities)
    success_count = 0
    error_count = 0
    run_start = time.time()
    entity_times: list[float] = []

    for i, entity_name in enumerate(entities, 1):
        entity_start = time.time()
        try:
            data = await research_entity_async(
                client, entity_name, skill, model,
                entity_idx=i, total=total, run_start=run_start,
            )
            sources_row = profile_to_sources_row(data, fields)
            clean_row   = to_clean_row(sources_row, fields)

            clean_writer.writerow(clean_row)
            sources_writer.writerow(sources_row)
            clean_f.flush()
            src_f.flush()

            entity_times.append(time.time() - entity_start)
            avg = sum(entity_times) / len(entity_times)
            remaining_entities = total - i
            eta = f"~{int(avg * remaining_entities / 60)}m remaining" if remaining_entities else "done"
            _print_summary(f"[{i}/{total}] {entity_name} | {eta} | ", clean_row, skill.mode)
            success_count += 1

        except Exception as e:
            print(f"  [{i}/{total}] {entity_name} ERROR: {e}")
            clean_writer.writerow({"entity_name": entity_name})
            sources_writer.writerow(
                {"entity_name": entity_name, "research_notes": f"ERROR: {e}"}
            )
            clean_f.flush()
            src_f.flush()
            error_count += 1

    return success_count, error_count


# ---------------------------------------------------------------------------
# Batch runner — writes results as each completes for crash durability
# ---------------------------------------------------------------------------

async def _run_batch(
    entities: list[str],
    skill: Skill,
    model: str,
    concurrency: int,
    clean_writer,
    sources_writer,
    clean_f,
    src_f,
) -> tuple[int, int]:
    """
    Run entities concurrently bounded by semaphore.
    Results are written to CSV as each entity completes, so a mid-run crash
    preserves all work done up to that point.
    """
    client = AsyncAnthropic(timeout=600.0)
    sem = asyncio.Semaphore(concurrency)
    total = len(entities)
    success_count = 0
    error_count = 0
    run_start = time.time()
    entity_times: list[float] = []

    async def bounded(idx: int, name: str):
        async with sem:
            entity_start = time.time()
            try:
                data = await research_entity_async(
                    client, name, skill, model,
                    entity_idx=idx + 1, total=total, run_start=run_start,
                )
                return (name, data, None, time.time() - entity_start)
            except Exception as e:
                return (name, None, e, time.time() - entity_start)

    tasks = [asyncio.create_task(bounded(i, n)) for i, n in enumerate(entities)]
    done_count = 0

    for coro in asyncio.as_completed(tasks):
        entity_name, data, err, elapsed = await coro
        done_count += 1

        if err:
            print(f"  [{done_count}/{total}] {entity_name} ERROR: {err}")
            clean_writer.writerow({"entity_name": entity_name})
            sources_writer.writerow(
                {"entity_name": entity_name, "research_notes": f"ERROR: {err}"}
            )
            error_count += 1
        else:
            entity_times.append(elapsed)
            avg = sum(entity_times) / len(entity_times)
            remaining_entities = total - done_count
            eta = f"~{int(avg * remaining_entities / 60 / concurrency)}m remaining" if remaining_entities else "done"
            fields = SKILL_FIELDS[skill.name]
            sources_row = profile_to_sources_row(data, fields)
            clean_row = to_clean_row(sources_row, fields)
            clean_writer.writerow(clean_row)
            sources_writer.writerow(sources_row)
            _print_summary(f"[{done_count}/{total}] {entity_name} | {eta} | ", clean_row, skill.mode)
            success_count += 1

        # Flush after every entity so a crash doesn't lose completed work.
        clean_f.flush()
        src_f.flush()

    return success_count, error_count


# ---------------------------------------------------------------------------
# Batches API runner — 50% cost discount, async, one request per entity
# ---------------------------------------------------------------------------

async def _run_batches_api(
    entities: list[str],
    skill: Skill,
    model: str,
    clean_writer,
    sources_writer,
    clean_f,
    src_f,
) -> tuple[int, int]:
    """
    Submit all entities to the Anthropic Messages Batches API in a single batch,
    then poll until processing is complete and write results to CSV.

    Trade-offs vs real-time mode:
    - ~50% cost discount
    - No multi-round agentic loop (single request per entity)
    - No read_file progressive disclosure
    - Results arrive asynchronously (minutes to ~1 hour)
    """
    client = AsyncAnthropic(timeout=600.0)
    total = len(entities)
    output_schema = SKILL_OUTPUT_SCHEMAS[skill.name]
    fields = SKILL_FIELDS[skill.name]

    # Build one request per entity
    # custom_id must match ^[a-zA-Z0-9_-]{1,64}$, so sanitize entity names
    # and keep a mapping back to the originals.
    import re as _re
    def _to_custom_id(name: str, idx: int) -> str:
        sanitized = _re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:60]
        return f"{sanitized}_{idx}"

    custom_id_to_entity: dict[str, str] = {}
    requests = []
    for idx, entity_name in enumerate(entities):
        custom_id = _to_custom_id(entity_name, idx)
        custom_id_to_entity[custom_id] = entity_name
        static_text = skill.prompt_template.replace("{entity}", "[ENTITY]")
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": static_text,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    },
                    {
                        "type": "text",
                        "text": f'Research: "{entity_name}"',
                    },
                ],
            }
        ]
        requests.append({
            "custom_id": custom_id,
            "params": {
                "model": model,
                "max_tokens": 4096,
                "thinking": {"type": "adaptive"},
                "tools": [
                    {"type": "web_search_20260209", "name": "web_search", "max_uses": 3, "allowed_callers": ["direct"]},
                    {"type": "web_fetch_20260209",  "name": "web_fetch",  "max_uses": 2, "allowed_callers": ["direct"]},
                ],
                "messages": messages,
                "output_config": {"format": {"type": "json_schema", "schema": output_schema}},
            },
        })

    print(f"Submitting {total} entities to Messages Batches API...")
    batch = await client.messages.batches.create(requests=requests)
    batch_id = batch.id
    print(f"Batch submitted: {batch_id}")
    print("Polling for results (this may take minutes to ~1 hour)...\n")

    # Poll until batch ends — adaptive: start fast, back off to 5 min cap.
    poll_interval = 30   # seconds: initial
    poll_max      = 300  # seconds: cap at 5 minutes
    while True:
        await asyncio.sleep(poll_interval)
        batch = await client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"  Batch {batch_id[:16]}... | processing: {counts.processing} | "
            f"succeeded: {counts.succeeded} | errored: {counts.errored}",
            flush=True,
        )
        if batch.processing_status == "ended":
            break
        poll_interval = min(poll_interval * 2, poll_max)

    print(f"\nBatch complete. Retrieving results...")
    success_count = 0
    error_count = 0

    async for result in await client.messages.batches.results(batch_id):
        entity_name = custom_id_to_entity.get(result.custom_id, result.custom_id)
        if result.result.type == "succeeded":
            text = next(
                (b.text for b in result.result.message.content if getattr(b, "type", None) == "text"),
                None,
            )
            if text:
                try:
                    data = json.loads(text)
                    data["entity_name"] = entity_name
                    sources_row = profile_to_sources_row(data, fields)
                    clean_row = to_clean_row(sources_row, fields)
                    clean_writer.writerow(clean_row)
                    sources_writer.writerow(sources_row)
                    _print_summary(f"{entity_name} | ", clean_row, skill.mode)
                    success_count += 1
                except Exception as e:
                    print(f"  {entity_name} parse ERROR: {e}")
                    clean_writer.writerow({"entity_name": entity_name})
                    sources_writer.writerow({"entity_name": entity_name, "research_notes": f"ERROR: {e}"})
                    error_count += 1
            else:
                print(f"  {entity_name} ERROR: no text in response")
                clean_writer.writerow({"entity_name": entity_name})
                sources_writer.writerow({"entity_name": entity_name, "research_notes": "ERROR: no text in response"})
                error_count += 1
        else:
            err = getattr(result.result, "error", result.result)
            print(f"  {entity_name} ERROR: {err}")
            clean_writer.writerow({"entity_name": entity_name})
            sources_writer.writerow({"entity_name": entity_name, "research_notes": f"ERROR: {err}"})
            error_count += 1

        clean_f.flush()
        src_f.flush()

    return success_count, error_count


# ---------------------------------------------------------------------------
# Parsing and flattening
# ---------------------------------------------------------------------------

def parse_json_response(raw: str, entity_name: str) -> dict:
    """Extract and parse the JSON object from the model's response."""
    text = raw.strip()
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object in response: {raw[:300]}")

    data = json.loads(text[start:end])
    data["entity_name"] = entity_name
    return data


def profile_to_sources_row(data: dict, fields: list[str]) -> dict:
    """Flatten nested JSON profile into a flat sources-row dict."""

    def extract(field_name: str) -> tuple[str, str, str]:
        field = data.get(field_name, {})
        if not isinstance(field, dict):
            return ("", "", "")
        return (
            "" if field.get("value") is None else str(field["value"]),
            field.get("source_url") or "",
            field.get("confidence") or "",
        )

    row = {"entity_name": data.get("entity_name", "")}
    for f in fields[1:]:
        val, src, conf = extract(f)
        row[f] = val
        row[f"{f}_source"] = src
        row[f"{f}_confidence"] = conf
    row["research_notes"] = data.get("research_notes", "")
    return row


def to_clean_row(sources_row: dict, fields: list[str]) -> dict:
    """Strip a sources row down to clean-output columns only."""
    return {k: sources_row.get(k, "") for k in fields}


def _print_summary(label: str, clean_row: dict, mode: str) -> None:
    if mode == "vendor":
        cat = clean_row.get("product_category") or "—"
        cust = clean_row.get("primary_customer") or "—"
        stage = clean_row.get("funding_stage") or "—"
        print(f"{label}OK  |  {cat}  |  customer: {cust}  |  stage: {stage}")
    else:
        ehr = clean_row.get("ehr_vendor") or "—"
        beds = clean_row.get("bed_count") or "—"
        stars = clean_row.get("cms_star_rating") or "—"
        print(f"{label}OK  |  EHR: {ehr}  |  beds: {beds}  |  CMS stars: {stars}")


# ---------------------------------------------------------------------------
# Discovery mode — seeds entity list from CMS Hospital General Information
# ---------------------------------------------------------------------------

# NOTE: This URL contains a hardcoded timestamp and may rotate when CMS republishes
# the dataset. If it returns a 404, visit https://data.cms.gov/provider-data/dataset/xubh-q36u
# to find the current download link and update this constant.
CMS_HOSPITAL_CSV_URL = (
    "https://data.cms.gov/provider-data/sites/default/files/resources/"
    "092256becd267d9eecca49d4af3bba78_1671055008/Hospital_General_Information.csv"
)


def discover_health_systems(state: str) -> list[str]:
    """
    Download the CMS Hospital General Information CSV and return hospital names
    for the given two-letter state code (e.g. "CA", "NY").
    """
    print(f"Downloading CMS Hospital General Information for state={state} ...")
    try:
        with urllib.request.urlopen(CMS_HOSPITAL_CSV_URL, timeout=30) as resp:
            lines = resp.read().decode("utf-8").splitlines()
    except Exception as e:
        print(
            f"ERROR: Could not fetch CMS hospital data: {e}\n"
            "The CMS file URL may have rotated. Visit:\n"
            "  https://data.cms.gov/provider-data/dataset/xubh-q36u\n"
            "to get the current download URL and update CMS_HOSPITAL_CSV_URL in this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    reader = csv.DictReader(lines)
    required_cols = {"Hospital Name", "State"}
    actual_cols = set(reader.fieldnames or [])
    missing = required_cols - actual_cols
    if missing:
        print(
            f"ERROR: CMS CSV is missing expected columns: {missing}.\n"
            f"Columns found: {actual_cols}\n"
            f"The CMS schema may have changed. Update the column names in discover_health_systems().",
            file=sys.stderr,
        )
        sys.exit(1)

    names = [
        row["Hospital Name"].strip()
        for row in reader
        if row.get("State", "").strip().upper() == state.upper()
        and row.get("Hospital Name", "").strip()
    ]
    print(f"Found {len(names)} hospitals in {state}")
    return names


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _needs_rerun(sources_row: dict, fields: list[str]) -> bool:
    """Return True if any field has an empty value or explicitly low confidence.

    "medium" confidence is acceptable — re-running agentic for a medium-confidence
    result wastes cost without guaranteed improvement. Only re-run when confidence
    is "low" (model flagged the source as weak) or "" (no data found at all).
    """
    for f in fields[1:]:  # skip entity_name
        value      = sources_row.get(f, "")
        confidence = sources_row.get(f"{f}_confidence", "")
        if value == "" or confidence == "low":
            return True
    return False


def _rewrite_csvs(
    output_path: str,
    sources_path: str,
    clean_fieldnames: list[str],
    src_fieldnames: list[str],
    clean_rows: list[dict],
    sources_rows: list[dict],
) -> None:
    """Overwrite both CSVs with the given rows (used after agentic follow-up merges results)."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=clean_fieldnames, extrasaction="raise")
        w.writeheader()
        w.writerows(clean_rows)
    with open(sources_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=src_fieldnames, extrasaction="raise")
        w.writeheader()
        w.writerows(sources_rows)


def _run_research(entities: list[str], skill, args, output_path, sources_path) -> None:
    """Open output CSVs and run the appropriate research runner (batch/concurrent/sequential)."""
    fields = SKILL_FIELDS[skill.name]
    clean_fieldnames = fields
    src_fieldnames   = sources_fieldnames(fields)
    success_count = 0
    error_count   = 0

    with (
        open(output_path,  "w", newline="", encoding="utf-8") as clean_f,
        open(sources_path, "w", newline="", encoding="utf-8") as src_f,
    ):
        clean_writer   = csv.DictWriter(clean_f,  fieldnames=clean_fieldnames, extrasaction="raise")
        sources_writer = csv.DictWriter(src_f,    fieldnames=src_fieldnames,   extrasaction="raise")
        clean_writer.writeheader()
        sources_writer.writeheader()

        if args.batch:
            print(f"Running {len(entities)} entities via Messages Batches API (50% discount, async)...\n")
            success_count, error_count = asyncio.run(
                _run_batches_api(entities, skill, args.model, clean_writer, sources_writer, clean_f, src_f)
            )
        elif args.concurrency > 1:
            print(f"Running {len(entities)} entities with {args.concurrency} concurrent workers...\n")
            success_count, error_count = asyncio.run(
                _run_batch(entities, skill, args.model, args.concurrency, clean_writer, sources_writer, clean_f, src_f)
            )
        else:
            print(f"Running {len(entities)} entities sequentially...\n")
            success_count, error_count = asyncio.run(
                _run_sequential(entities, skill, args.model, clean_writer, sources_writer, clean_f, src_f)
            )

    # ---------------------------------------------------------------------------
    # Hybrid follow-up: after batch, re-run agentic on low-confidence entities
    # ---------------------------------------------------------------------------
    if args.batch:
        with open(sources_path, newline="", encoding="utf-8") as f:
            sources_rows = list(csv.DictReader(f))

        rerun_entities = [
            row["entity_name"] for row in sources_rows
            if _needs_rerun(row, fields)
        ]

        if rerun_entities:
            print(f"\n{len(rerun_entities)} entities have low/missing confidence — running agentic follow-up:")
            for name in rerun_entities:
                print(f"  • {name}")
            print()

            # Collect agentic results into temporary in-memory CSV buffers
            import io
            clean_buf   = io.StringIO()
            sources_buf = io.StringIO()
            tmp_clean_w   = csv.DictWriter(clean_buf,   fieldnames=clean_fieldnames, extrasaction="raise")
            tmp_sources_w = csv.DictWriter(sources_buf, fieldnames=src_fieldnames,   extrasaction="raise")
            tmp_clean_w.writeheader()
            tmp_sources_w.writeheader()

            rerun_success, rerun_error = asyncio.run(
                _run_sequential(
                    rerun_entities, skill, args.model,
                    tmp_clean_w, tmp_sources_w,
                    clean_buf, sources_buf,
                )
            )
            success_count += rerun_success
            error_count   += rerun_error

            # Build updated rows: start from batch results, overwrite re-run entities
            clean_buf.seek(0)
            sources_buf.seek(0)
            rerun_clean_rows   = {r["entity_name"]: r for r in csv.DictReader(clean_buf)}
            rerun_sources_rows = {r["entity_name"]: r for r in csv.DictReader(sources_buf)}

            with open(output_path, newline="", encoding="utf-8") as f:
                merged_clean = [
                    rerun_clean_rows.get(r["entity_name"], r)
                    for r in csv.DictReader(f)
                ]
            merged_sources = [
                rerun_sources_rows.get(r["entity_name"], r)
                for r in sources_rows
            ]

            _rewrite_csvs(output_path, sources_path, clean_fieldnames, src_fieldnames,
                          merged_clean, merged_sources)
            print(f"Agentic follow-up complete. {rerun_success} updated, {rerun_error} failed.")
        else:
            print("\nAll fields have high confidence — no agentic follow-up needed.")

    print(f"\nDone. {success_count} succeeded, {error_count} failed.")
    print(f"Clean results:   {output_path}")
    print(f"Sources/verify:  {sources_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Health IT market intelligence — discover vendors or health systems, "
            "then profile them for competitive analysis or BD prospecting."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -------------------------------------------------------------------------
    # discover
    # -------------------------------------------------------------------------
    discover_parser = subparsers.add_parser(
        "discover", help="Build an entity list (vendor or health-system)."
    )
    discover_sub = discover_parser.add_subparsers(dest="target", required=True)

    # discover vendor
    dv = discover_sub.add_parser(
        "vendor", help="Discover health IT vendors via natural language query."
    )
    dv.add_argument(
        "--output",
        default="vendor-results.csv",
        help="Output CSV path (default: vendor-results.csv).",
    )
    dv.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model to use (default: {DEFAULT_MODEL}).",
    )

    # discover health-system
    dhs = discover_sub.add_parser(
        "health-system", help="Discover health systems from CMS data for a given state."
    )
    dhs.add_argument(
        "--state",
        required=True,
        help="Two-letter state code (e.g. CA, NY).",
    )
    dhs.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: <state>-health-systems.csv).",
    )

    # -------------------------------------------------------------------------
    # profile
    # -------------------------------------------------------------------------
    profile_parser = subparsers.add_parser(
        "profile", help="Profile entities from an input CSV."
    )
    profile_sub = profile_parser.add_subparsers(dest="target", required=True)

    # profile vendor
    rv = profile_sub.add_parser(
        "vendor", help="Profile health IT vendors for competitive intelligence."
    )
    rv.add_argument("--input", required=True, help="Input CSV with entity_name column.")
    rv.add_argument(
        "--output",
        default="varys-vendor-research-results.csv",
        help="Clean output CSV (default: varys-vendor-research-results.csv).",
    )
    rv.add_argument(
        "--batch",
        action="store_true",
        help="Use Messages Batches API (~50%% cost discount, async, no agentic loop).",
    )
    rv.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Concurrent API calls (default: 5). Use 1 for sequential.",
    )
    rv.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model to use (default: {DEFAULT_MODEL}).",
    )
    rv.add_argument(
        "--yes",
        action="store_true",
        help="Skip cost confirmation prompt (for CI / scripted use).",
    )

    # profile health-system
    rhs = profile_sub.add_parser(
        "health-system", help="Profile health systems for BD prospecting."
    )
    rhs.add_argument("--input", required=True, help="Input CSV with entity_name column.")
    rhs.add_argument(
        "--output",
        default="varys-health-system-research-results.csv",
        help="Clean output CSV (default: varys-health-system-research-results.csv).",
    )
    rhs.add_argument(
        "--batch",
        action="store_true",
        help="Use Messages Batches API (~50%% cost discount, async, no agentic loop).",
    )
    rhs.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Concurrent API calls (default: 5). Use 1 for sequential.",
    )
    rhs.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model to use (default: {DEFAULT_MODEL}).",
    )
    rhs.add_argument(
        "--yes",
        action="store_true",
        help="Skip cost confirmation prompt (for CI / scripted use).",
    )

    # -------------------------------------------------------------------------
    # pipeline
    # -------------------------------------------------------------------------
    pipeline_parser = subparsers.add_parser(
        "pipeline", help="Discover then profile in one shot (no intermediate CSV)."
    )
    pipeline_sub = pipeline_parser.add_subparsers(dest="target", required=True)

    # pipeline vendor
    pv = pipeline_sub.add_parser(
        "vendor", help="Discover vendors via query, then profile them."
    )
    pv.add_argument(
        "--output",
        default="vendor-pipeline-results.csv",
        help="Clean output CSV (default: vendor-pipeline-results.csv).",
    )
    pv.add_argument("--batch", action="store_true",
        help="Use Messages Batches API (~50%% cost discount, async, no agentic loop).")
    pv.add_argument("--concurrency", type=int, default=5,
        help="Concurrent API calls (default: 5). Use 1 for sequential.")
    pv.add_argument("--model", default=DEFAULT_MODEL,
        help=f"Anthropic model to use (default: {DEFAULT_MODEL}).")
    pv.add_argument("--yes", action="store_true",
        help="Skip cost confirmation prompt.")

    # pipeline health-system
    phs = pipeline_sub.add_parser(
        "health-system", help="Discover health systems by state, then profile them."
    )
    phs.add_argument("--state", required=True,
        help="Two-letter state code (e.g. CA, NY).")
    phs.add_argument(
        "--output",
        default=None,
        help="Clean output CSV (default: <state>-pipeline-results.csv).",
    )
    phs.add_argument("--batch", action="store_true",
        help="Use Messages Batches API (~50%% cost discount, async, no agentic loop).")
    phs.add_argument("--concurrency", type=int, default=5,
        help="Concurrent API calls (default: 5). Use 1 for sequential.")
    phs.add_argument("--model", default=DEFAULT_MODEL,
        help=f"Anthropic model to use (default: {DEFAULT_MODEL}).")
    phs.add_argument("--yes", action="store_true",
        help="Skip cost confirmation prompt.")

    args = parser.parse_args()

    # -------------------------------------------------------------------------
    # Infer skill name from target subcommand
    # -------------------------------------------------------------------------
    skill_name = {
        "vendor": "profile-health-it-vendor",
        "health-system": "profile-health-system",
    }[args.target]

    # Guard concurrency early so pipeline and profile both get a clean error message.
    if getattr(args, "concurrency", None) is not None and args.concurrency < 1:
        print("ERROR: --concurrency must be at least 1.", file=sys.stderr)
        sys.exit(1)

    # -------------------------------------------------------------------------
    # discover branch — build entity list and write to CSV, then exit
    # -------------------------------------------------------------------------
    if args.command == "discover":
        if args.target == "vendor":
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
                sys.exit(1)
            query = input("Query: ").strip()
            if not query:
                print("ERROR: Query cannot be empty.", file=sys.stderr)
                sys.exit(1)
            print(f'\nDiscovering vendors for: "{query}" ...')
            entities = asyncio.run(discover_vendors_via_llm(query, args.model))
            preview = ", ".join(entities[:8])
            suffix = f" ... (+{len(entities) - 8} more)" if len(entities) > 8 else ""
            print(f"Discovered {len(entities)} companies: {preview}{suffix}\n")
            output_path = Path(args.output)

        else:  # health-system
            output_path = Path(
                args.output if args.output else f"{args.state.lower()}-health-systems.csv"
            )
            entities = discover_health_systems(args.state)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["entity_name"])
            writer.writeheader()
            for name in entities:
                writer.writerow({"entity_name": name})
        print(f"Wrote {len(entities)} entities to {output_path}")
        return

    # -------------------------------------------------------------------------
    # pipeline branch — discover in memory then research
    # -------------------------------------------------------------------------
    if args.command == "pipeline":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
            sys.exit(1)

        skill = load_skill(skill_name)

        if args.target == "vendor":
            query = input("Query: ").strip()
            if not query:
                print("ERROR: Query cannot be empty.", file=sys.stderr)
                sys.exit(1)
            print(f'\nDiscovering vendors for: "{query}" ...')
            entities = asyncio.run(discover_vendors_via_llm(query, args.model))
            if not entities:
                print("ERROR: Discovery returned no companies. Try a broader query.", file=sys.stderr)
                sys.exit(1)
            preview = ", ".join(entities[:8])
            suffix = f" ... (+{len(entities) - 8} more)" if len(entities) > 8 else ""
            print(f"Discovered {len(entities)} companies: {preview}{suffix}\n")
            output_path = Path(args.output)

        else:  # health-system
            entities = discover_health_systems(args.state)
            if not entities:
                print(f"ERROR: No hospitals found for state '{args.state}'. Check the state code.", file=sys.stderr)
                sys.exit(1)
            output_path = Path(
                args.output if args.output else f"{args.state.lower()}-pipeline-results.csv"
            )

        sources_path = output_path.with_stem(output_path.stem + "_sources")

        est_cost_low  = len(entities) * COST_PER_ENTITY_LOW
        est_cost_high = len(entities) * COST_PER_ENTITY_HIGH
        est_minutes   = (len(entities) / args.concurrency) * AVG_SECONDS_PER_ENTITY / 60

        print()
        print(f"Skill:          {skill.name}")
        print(f"Entities:       {len(entities)}")
        print(f"Model:          {args.model}")
        print(f"Concurrency:    {args.concurrency}")
        print(f"Est. cost:      ${est_cost_low:.0f} – ${est_cost_high:.0f}")
        print(f"Est. runtime:   ~{est_minutes:.0f} min")
        print(f"Clean output:   {output_path}")
        print(f"Sources output: {sources_path}")
        print()

        if not args.yes:
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                sys.exit(0)

        print()
        _run_research(entities, skill, args, output_path, sources_path)
        return

    # -------------------------------------------------------------------------
    # profile branch — load input CSV and profile entities
    # -------------------------------------------------------------------------
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    skill = load_skill(skill_name)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "entity_name" not in (reader.fieldnames or []):
            print("ERROR: Input CSV must have an 'entity_name' column.", file=sys.stderr)
            sys.exit(1)
        entities = [
            row["entity_name"].strip()
            for row in reader
            if row["entity_name"].strip()
        ]
    if not entities:
        print("ERROR: Input CSV contains no entity names.", file=sys.stderr)
        sys.exit(1)

    # Cost estimation gate
    est_cost_low  = len(entities) * COST_PER_ENTITY_LOW
    est_cost_high = len(entities) * COST_PER_ENTITY_HIGH
    est_minutes   = (len(entities) / args.concurrency) * AVG_SECONDS_PER_ENTITY / 60

    output_path  = Path(args.output)
    sources_path = output_path.with_stem(output_path.stem + "_sources")

    print()
    print(f"Skill:          {skill.name}")
    print(f"Entities:       {len(entities)}")
    print(f"Model:          {args.model}")
    print(f"Concurrency:    {args.concurrency}")
    print(f"Est. cost:      ${est_cost_low:.0f} – ${est_cost_high:.0f}")
    print(f"Est. runtime:   ~{est_minutes:.0f} min")
    print(f"Clean output:   {output_path}")
    print(f"Sources output: {sources_path}")
    print()

    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    print()

    _run_research(entities, skill, args, output_path, sources_path)


if __name__ == "__main__":
    main()
