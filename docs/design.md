# Architecture & Design

## Overview

**Anthropic Messages API as the research engine, Python as the orchestrator.**

The orchestrator calls the Anthropic Messages API directly using the built-in
`web_search_20260209` and `web_fetch_20260209` tools. Python handles orchestration
(CSV I/O, looping, error handling, progress) while the model handles research.

**One context window per company** — each entity gets an isolated conversation with
no cross-company context leakage.

Requires `ANTHROPIC_API_KEY` — set it as an environment variable before running.

## Three Interfaces

| Interface | Use case | How |
|---|---|---|
| **Claude Code discovery agent** | Build a competitor list from natural language, iteratively | Invoke `health-it-vendor-discoverer` agent in Claude Code |
| **Claude Code profile skill** | Profile a single company or health system, interactive | Invoke `profile-health-it-vendor` or `profile-health-system` skill in Claude Code |
| **CLI batch** | CSV → CSV at any scale, or discover + profile in one command | `python healthtech-intel.py profile vendor --input ... --output ...` |

The same skill files (`.claude/skills/`) drive both Claude Code and CLI. Claude Code
invokes them interactively; Python loads them as prompt templates for batch runs.

## Components

```
healthtech-intel/
├── healthtech-intel.py                  # Python orchestrator — CLI batch runner
├── requirements.txt                     # anthropic>=0.40.0, pyyaml>=6.0
├── sample_vendors.csv                   # Sample health IT vendor names
├── sample_health_systems.csv            # Sample health system names
├── skills/                              # Flat skill files — load into any AI assistant
│   ├── discover-health-it-vendor.md     # Vendor discovery prompt (ChatGPT, Gemini, etc.)
│   ├── profile-health-it-vendor.md      # Vendor profiling prompt
│   └── profile-health-system.md         # Health system profiling prompt
└── .claude/                             # Claude Code–specific files
    ├── agents/
    │   ├── health-it-vendor-discoverer.md   # Claude Code agent — build competitor lists
    │   ├── health-it-vendor-researcher.md   # Claude Code agent — single company profile
    │   └── health-system-researcher.md      # Claude Code agent — single hospital profile
    └── skills/                              # Extended skills with reference files
        ├── discover-health-it-vendor/
        │   └── SKILL.md                     # Discovery prompt — NL query → company list
        ├── profile-health-it-vendor/
        │   ├── SKILL.md                     # Profiling prompt + output schema
        │   └── references/
        │       ├── field-definitions.md     # Enum values, confidence rules
        │       └── source-priority.md       # Which URLs/DBs to check per field
        └── profile-health-system/
            ├── SKILL.md
            └── references/
                ├── field-definitions.md
                └── source-priority.md
```

## Data Flow

**Phase 1 — Discovery** (vendor only — health systems use CMS data instead, optional):
```
Natural language query (interactive prompt or Claude Code UI)
    ↓
discover-health-it-vendor skill
    ↓
LLM searches web — market maps, KLAS, Crunchbase, analyst reports
    ↓
Returns JSON list of company names
    ↓
[Claude Code UI: iterative refinement → save discovered_competitors.csv]
[CLI: list feeds directly into Phase 2]
```

**Phase 2 — Profiling** (all skills):
```
Input CSV (entity_name column)
  —or—  CMS discovery (discover health-system --state XX)
  —or—  Vendor discovery output from Phase 1
    ↓
healthtech-intel.py loads skill file from .claude/skills/<skill>/SKILL.md
    ↓
Cost + runtime estimate shown — user must confirm before any API call
    ↓
For each entity (parallel, bounded by --concurrency semaphore):
    └── Fresh context window per company — no cross-company leakage
            ↓
        Anthropic Messages API (model + web_search + web_fetch tools)
            ↓
        Model researches the web natively
            ↓
        Returns structured JSON
    ↓
Parse JSON, write two rows: clean output + full sources row (flushed immediately)
    ↓
Output CSV pair: results.csv + results_sources.csv
```

## Output Files

Every run writes a pair of CSVs:

| File | Contents | Use Case |
|---|---|---|
| `results.csv` | Clean values only | Downstream consumption, import, sharing |
| `results_sources.csv` | Values + source URLs + confidence levels | QA, verification, auditing |

---

## Design Decisions

### One context window per company
Each entity gets a fresh conversation with no shared history. This prevents context
leakage — a company researched early in a batch can't "bleed" into a later one through
accumulated context. It also means errors are isolated: one failed lookup doesn't
corrupt the next.

### Skills are the source of truth — not Python
Skill files define the prompt and output schema. There are two forms:

- **`skills/`** — flat `.md` files. Load into any AI assistant (ChatGPT, Gemini, Copilot, etc.) for interactive use.
- **`.claude/skills/`** — extended versions with reference files (`field-definitions.md`, `source-priority.md`). Used by Claude Code interactively and loaded by `healthtech-intel.py` for batch runs.

A single edit to a skill propagates to both the interactive and batch interfaces. No duplication.

### Progressive disclosure for reference files
The skill instructs Claude to load `field-definitions.md` and `source-priority.md`
*only when uncertain* about a specific field — not upfront every time. This avoids
burning tokens on reference material the model already knows well for common fields
(e.g., `founded_year`, `headquarters`) while still providing a safety net for
ambiguous cases.

### Null over low-confidence guess
Every field returns `null` rather than a plausible-sounding but unverified value.
A null is honest. A wrong value stored in a CSV gets treated as true, shared downstream,
and is expensive to discover and correct later.

### Flush after every entity
Both output CSVs are flushed row-by-row immediately after each entity completes
([healthtech-intel.py:344-345](../healthtech-intel.py#L344-L345)). A mid-run crash — network timeout,
API error, Ctrl+C — preserves every result written so far. Without this, the output
buffers wouldn't be written until the process exits cleanly.

### Two output files (clean + sources)
Keeping source URLs and confidence levels in a separate `_sources.csv` avoids polluting
the clean output with 3× as many columns. Consumers who want to import or share results
use the clean file. QA and verification use the sources file.

### Concurrency default of 5, not 10 or 20
`web_search` and `web_fetch` are server-side tools: all searching happens inside a single
API call, not across multiple round-trips. Each entity therefore makes 1–2 API calls total.
At high concurrency those calls are still token-heavy, and many simultaneous large requests
hit Anthropic rate limits (429). At 20 concurrent workers, a mis-specified input CSV could
exhaust significant API budget before you can interrupt the run. 5 is conservative enough
to rarely rate-limit while still being fast: 50 companies completes in ~7 minutes.

### Sequential mode (`--concurrency 1`)
When debugging or running on a trial API key with tight per-minute limits, sequential
mode makes logs readable and prevents rate limits entirely. Rate limit errors are handled
automatically with exponential backoff retries.

### `read_file` restricted to `.claude/skills/`
The client-side `read_file` tool ([healthtech-intel.py:165-184](../healthtech-intel.py#L165-L184)) whitelists
only the skills directory. The model can load its own reference documents but cannot
read arbitrary filesystem paths — preventing accidental exposure of credentials, configs,
or other local files if the model is ever prompted adversarially through a web page it fetches.

### Cost gate before any API call
The CLI always prints an estimate and requires confirmation before calling the API
([healthtech-intel.py:565-587](../healthtech-intel.py#L565-L587)). This makes cost visible and intentional.
`--yes` disables it for CI.

### Python as orchestrator, not an LLM

The batch runner uses Python to orchestrate the research loop rather than an LLM
orchestrator that spawns subagents per entity. This was a deliberate choice:

**Why Python wins for this workload:**
- **Zero orchestration cost** — Python loops are free; an LLM orchestrator burns tokens deciding what to do next
- **Deterministic** — every entity in the CSV gets researched exactly once; an LLM orchestrator might skip, reorder, or decide 95/100 is "good enough"
- **Crash durability** — Python controls flush timing precisely; state in an LLM orchestrator's context window is lost on crash
- **Predictable cost** — the pre-run estimate is only possible because Python knows the entity count before any API call; an LLM orchestrator can spawn unpredictably
- **Explicit error handling** — Python catches exceptions, writes error rows, and continues without ambiguity

**Where an LLM orchestrator would win:**
- The task requires adaptive planning (discovering *what* to research, not just *how*)
- Cross-entity reasoning matters (e.g., "these 3 companies share the same investors")
- The input is natural language rather than a structured list

**The middle ground — two-phase pipeline:**
The right answer is to split responsibility: a lightweight LLM discovery agent handles
the open-ended "find me candidates" phase and produces a CSV; the Python orchestrator
handles the deterministic "research each entity in depth" phase. This keeps Python's
guarantees where they matter while adding LLM flexibility where it's needed.
Both skills now implement this:
- Health systems: `discover health-system --state XX` seeds from CMS public data
- Vendors: `discover vendor` (interactive query) seeds via the `discover-health-it-vendor` skill

---

## Source Priority

### Vendor skill (most to least authoritative)
1. Company IR / official website
2. SEC filings (10-K, S-1)
3. FDA 510(k) / PMA / De Novo databases
4. Press releases, Crunchbase
5. News coverage

### Health system skill (most to least authoritative)
1. CMS Care Compare — bed count, star ratings
2. Hospital website — ownership, leadership
3. IRS 990 filings (via ProPublica Nonprofit Explorer) — revenue, financials
4. CMS ACO participant lists — VBC participation
5. Press releases, Becker's Health IT, HIMSS News

See `.claude/skills/*/references/source-priority.md` for field-by-field source guidance.

---

## Discovery Mode

Both skills support a discovery phase that seeds the entity list without requiring
a pre-built CSV.

### Vendor discovery — LLM-powered (`discover vendor`)

Prompts for a natural language query and uses the `discover-health-it-vendor`
skill to find candidate companies via web search. Use `discover` to write a list to CSV
first, or `pipeline` to discover and profile in one shot:

```bash
# Two-step: discover then profile
python healthtech-intel.py discover vendor --output vendors.csv
python healthtech-intel.py profile vendor --input vendors.csv --output results.csv

# One-shot: discover + profile (interactive query prompt)
python healthtech-intel.py pipeline vendor --output results.csv
```

The discovery phase runs first (~30–60 seconds), prints the discovered company list
and a brief rationale, then flows into the full research pipeline. The
cost gate covers both phases.

Discovery sources: CB Insights market maps, KLAS rankings, HIMSS exhibitor lists,
Crunchbase category searches, Rock Health reports, analyst roundups.

**Alternatively, use the Claude Code UI for interactive refinement** — the
`health-it-vendor-discoverer` agent lets you refine the list before committing
to a full research run.

### Health system discovery — CMS public data (`discover health-system`)

`discover health-system --state XX` downloads the CMS Hospital General Information dataset
(~6,000 hospitals, publicly available) and filters to the requested state, writing a CSV
of hospital names ready for the research pipeline.

```bash
python healthtech-intel.py discover health-system --state CA --output ca_hospitals.csv
python healthtech-intel.py profile health-system --input ca_hospitals.csv --output ca_results.csv
```

This enables prospecting an entire state's hospital landscape without maintaining
your own contact list — no equivalent free tool exists for this.

**Note:** CMS Hospital General Information does not include bed count. Filtering by
`--min-beds` requires joining with the CMS Provider of Services file (planned for v2).

---

## Tuning Research Depth (`max_tool_rounds`)

Each skill file (`.claude/skills/*/SKILL.md`) has a `max_tool_rounds` YAML field that
caps how many API call rounds the model may use per entity. Each round can make up to
3 web searches and 2 web fetches.

**To change it**, edit the frontmatter of the relevant skill file:

```yaml
# .claude/skills/profile-health-system/SKILL.md
---
name: profile-health-system
max_tool_rounds: 5   ← change this
---
```

**Trade-offs:**

| Setting | Effect | When to use |
|---|---|---|
| 2–3 | Fast, cheap (~$0.15/entity), may miss VBC, payer mix, IRS 990 data | Spot checks, large low-stakes lists |
| 5–6 | Balanced — covers most fields from primary sources (~$0.25–0.35/entity) | Standard runs |
| 8–10 | Maximum rigor — model retries on CMS failures, fetches IRS 990s (~$0.40–0.60/entity) | High-value BD lists, audits |

Higher `max_tool_rounds` does not guarantee better results — it gives the model
more attempts to find primary sources before giving up. For well-known health systems
and major vendors, 5–6 rounds is typically sufficient. For smaller or obscure entities,
10 rounds may still return nulls if no public data exists.
