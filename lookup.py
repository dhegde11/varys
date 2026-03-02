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

    # Profile health IT vendors
    python lookup.py --skill researching-health-it-vendor --input vendors.csv --output results.csv

    # Profile health systems
    python lookup.py --skill researching-health-system --input systems.csv --output results.csv

    # Discover health systems by state (seeds from CMS public data)
    python lookup.py --skill researching-health-system --discover --state CA --output results.csv
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

import yaml
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
    "researching-health-it-vendor": [
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
    "researching-health-system": [
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
                    "Example: .claude/skills/researching-health-it-vendor/references/field-definitions.md"
                ),
            }
        },
        "required": ["path"],
    },
}


async def discover_vendors_via_llm(query: str, model: str) -> list[str]:
    """
    Call the Anthropic Messages API with the discovering-health-it-competitors
    skill to build a company list from a natural language query.

    One-shot (not iterative) — runs a single agentic loop and parses the JSON
    list of company names from the response. Returns a plain list of strings
    ready to feed into the research pipeline.
    """
    skill = load_skill("discovering-health-it-competitors")
    prompt = skill.prompt_template.format(query=query)
    client = AsyncAnthropic(timeout=600.0)
    messages = [{"role": "user", "content": prompt}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
        {"type": "web_fetch_20260209",  "name": "web_fetch",  "max_uses": 3},
        {"type": "code_execution_20250522", "name": "code_execution"},
    ]

    for _ in range(skill.max_tool_rounds):
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            tools=tools,
            messages=messages,
        )

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
        # web_search, web_fetch, and code_execution are server-side: the API
        # executes them automatically and embeds results as server_tool_use blocks.
        # No client-side tool_result handling is needed for discovery.

    raise RuntimeError(
        f"Discovery: reached {skill.max_tool_rounds} tool rounds without end_turn"
    )


async def research_entity_async(
    client: AsyncAnthropic, entity_name: str, skill: Skill, model: str
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
    prompt = skill.prompt_template.format(entity=entity_name)
    messages = [{"role": "user", "content": prompt}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
        {"type": "web_fetch_20260209",  "name": "web_fetch",  "max_uses": 2},
        {"type": "code_execution_20250522", "name": "code_execution"},
        READ_FILE_TOOL,
    ]

    for round_num in range(skill.max_tool_rounds):
        print(f"  [round {round_num+1}] calling API...", flush=True)
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            tools=tools,
            messages=messages,
        )

        block_types = [getattr(b, "type", "?") for b in response.content]
        print(f"  [round {round_num+1}] stop_reason={response.stop_reason} blocks={block_types}", flush=True)

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")),
                None,
            )
            if not text:
                raise RuntimeError("end_turn but no text block in response")
            return parse_json_response(text, entity_name)

        messages.append({"role": "assistant", "content": response.content})

        # web_search, web_fetch, and code_execution are server-side: the API runs
        # them automatically (server_tool_use blocks). read_file is client-side:
        # it uses tool_use blocks and requires the client to execute and return results.
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
    delay: float,
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

    for i, entity_name in enumerate(entities, 1):
        label = f"[{i}/{total}] {entity_name} ... "
        print(label, end="", flush=True)

        try:
            data = await research_entity_async(client, entity_name, skill, model)
            sources_row = profile_to_sources_row(data, fields)
            clean_row   = to_clean_row(sources_row, fields)

            clean_writer.writerow(clean_row)
            sources_writer.writerow(sources_row)
            clean_f.flush()
            src_f.flush()

            _print_summary("", clean_row, skill.mode)
            success_count += 1

        except Exception as e:
            print(f"ERROR: {e}")
            clean_writer.writerow({"entity_name": entity_name})
            sources_writer.writerow(
                {"entity_name": entity_name, "research_notes": f"ERROR: {e}"}
            )
            clean_f.flush()
            src_f.flush()
            error_count += 1

        if i < total and delay > 0:
            await asyncio.sleep(delay)

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

    async def bounded(idx: int, name: str):
        async with sem:
            try:
                data = await research_entity_async(client, name, skill, model)
                return (idx, name, data, None)
            except Exception as e:
                return (idx, name, None, e)

    tasks = [asyncio.create_task(bounded(i, n)) for i, n in enumerate(entities)]
    done_count = 0

    for coro in asyncio.as_completed(tasks):
        idx, entity_name, data, err = await coro
        done_count += 1
        label = f"[{done_count}/{total}] {entity_name} ... "

        if err:
            print(f"{label}ERROR: {err}")
            clean_writer.writerow({"entity_name": entity_name})
            sources_writer.writerow(
                {"entity_name": entity_name, "research_notes": f"ERROR: {err}"}
            )
            error_count += 1
        else:
            fields = SKILL_FIELDS[skill.name]
            sources_row = profile_to_sources_row(data, fields)
            clean_row = to_clean_row(sources_row, fields)
            clean_writer.writerow(clean_row)
            sources_writer.writerow(sources_row)
            _print_summary(label, clean_row, skill.mode)
            success_count += 1

        # Flush after every entity so a crash doesn't lose completed work.
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
    names = [
        row["Hospital Name"].strip()
        for row in reader
        if row.get("State", "").strip().upper() == state.upper()
        and row.get("Hospital Name", "").strip()
    ]
    print(f"Found {len(names)} hospitals in {state}")
    return names


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Health IT market intelligence — profile vendors for competitive analysis "
            "or health systems for BD prospecting."
        )
    )
    parser.add_argument(
        "--skill",
        required=True,
        choices=list(SKILL_FIELDS.keys()),
        help="Skill to use: 'researching-health-it-vendor' or 'researching-health-system'",
    )
    parser.add_argument(
        "--input",
        help="Input CSV path. Must have an 'entity_name' column. Not required with --discover.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Clean output CSV path. A _sources.csv is also written alongside it.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="(health-system skill only) Seed entity list from CMS public data.",
    )
    parser.add_argument(
        "--state",
        help="Two-letter state code for --discover (e.g. CA, NY).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model to use (default: {DEFAULT_MODEL}). Override via ANTHROPIC_MODEL env var.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to sleep between entities in sequential mode (default: 1.0).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent API calls (default: 5). Use 1 for sequential.",
    )
    parser.add_argument(
        "--max-entities",
        type=int,
        dest="max_entities",
        help="Cap the entity list to this many entries. Useful as a cost safety limit.",
    )
    parser.add_argument(
        "--discover-query",
        dest="discover_query",
        help=(
            "Natural language query to discover vendors via LLM "
            "(vendor skill only). E.g. 'AI scribe competitors to Nuance'. "
            "Replaces --input. Runs a lightweight discovery pass, then "
            "feeds the resulting company list into the full research pipeline."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the cost confirmation prompt (for CI / scripted use).",
    )
    args = parser.parse_args()

    # Validate
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    if args.discover and args.skill != "researching-health-system":
        print("ERROR: --discover is only available with --skill researching-health-system.", file=sys.stderr)
        sys.exit(1)

    if args.discover and not args.state:
        print("ERROR: --discover requires --state (e.g. --state CA).", file=sys.stderr)
        sys.exit(1)

    if args.discover_query and args.skill != "researching-health-it-vendor":
        print("ERROR: --discover-query is only available with --skill researching-health-it-vendor.", file=sys.stderr)
        sys.exit(1)

    if args.discover and args.discover_query:
        print("ERROR: Cannot use both --discover and --discover-query.", file=sys.stderr)
        sys.exit(1)

    if not args.discover and not args.discover_query and not args.input:
        print("ERROR: --input is required unless using --discover or --discover-query.", file=sys.stderr)
        sys.exit(1)

    # Load skill
    skill = load_skill(args.skill)

    # Build entity list
    if args.discover:
        entities = discover_health_systems(args.state)
    elif args.discover_query:
        print(f"Discovering vendors for: \"{args.discover_query}\" ...")
        entities = asyncio.run(discover_vendors_via_llm(args.discover_query, args.model))
        preview = ", ".join(entities[:8])
        suffix = f" ... (+{len(entities) - 8} more)" if len(entities) > 8 else ""
        print(f"Discovered {len(entities)} companies: {preview}{suffix}\n")
    else:
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

    # Apply safety cap
    if args.max_entities and len(entities) > args.max_entities:
        print(f"Capping to {args.max_entities} entities per --max-entities (had {len(entities)}).")
        entities = entities[: args.max_entities]

    # ---------------------------------------------------------------------------
    # Cost estimation gate — always shown before any API call is made
    # ---------------------------------------------------------------------------
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

    # ---------------------------------------------------------------------------
    # Run
    # ---------------------------------------------------------------------------
    fields = SKILL_FIELDS[skill.name]
    clean_fieldnames = fields
    src_fieldnames   = sources_fieldnames(fields)

    success_count = 0
    error_count   = 0

    with (
        open(output_path,  "w", newline="", encoding="utf-8") as clean_f,
        open(sources_path, "w", newline="", encoding="utf-8") as src_f,
    ):
        clean_writer   = csv.DictWriter(clean_f,  fieldnames=clean_fieldnames, extrasaction="ignore")
        sources_writer = csv.DictWriter(src_f,    fieldnames=src_fieldnames,   extrasaction="ignore")
        clean_writer.writeheader()
        sources_writer.writeheader()

        if args.concurrency > 1:
            print(f"Running {len(entities)} entities with {args.concurrency} concurrent workers...\n")
            success_count, error_count = asyncio.run(
                _run_batch(
                    entities, skill, args.model, args.concurrency,
                    clean_writer, sources_writer, clean_f, src_f,
                )
            )

        else:
            # Sequential path — one entity at a time with optional delay.
            # Run inside a single asyncio.run() so the AsyncAnthropic client
            # and its httpx session share one event loop for the entire run.
            print(f"Running {len(entities)} entities sequentially (delay={args.delay}s)...\n")
            success_count, error_count = asyncio.run(
                _run_sequential(
                    entities, skill, args.model, args.delay,
                    clean_writer, sources_writer, clean_f, src_f,
                )
            )

    print(f"\nDone. {success_count} succeeded, {error_count} failed.")
    print(f"Clean results:   {output_path}")
    print(f"Sources/verify:  {sources_path}")


if __name__ == "__main__":
    main()
