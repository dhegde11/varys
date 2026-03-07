# healthtech-intel

A market intelligence tool for the health IT ecosystem with three skills:

- **Vendor discovery** — Build a competitor list from natural language. "Find AI scribe competitors to Nuance" → curated company list → CSV ready for research.
- **Vendor research** — Profile health IT companies for competitive analysis. Who are they, what do they sell, who have they sold to, how are they funded, and what is their regulatory status?
- **Health system research** — Profile hospitals and health systems for BD prospecting. A free, open-source alternative to [Definitive Healthcare](https://www.definitivehc.com/), powered by public CMS data and Claude's web search.

Research skills share the same architecture: structured JSON output, per-field source URLs, and confidence scores.

---

## Architecture

### Core Design Philosophy

**Anthropic Messages API as the research engine, Python as the orchestrator.**

The orchestrator calls the Anthropic Messages API directly using the built-in
`web_search_20260209` and `web_fetch_20260209` tools. Python handles orchestration
(CSV I/O, looping, error handling, progress) while the model handles research.

**One context window per company** — each entity gets an isolated conversation with
no cross-company context leakage.

Requires `ANTHROPIC_API_KEY` — set it as an environment variable before running.

### Three Interfaces

| Interface | Use case | How |
|---|---|---|
| **Claude Code discovery agent** | Build a competitor list from natural language, iteratively | Invoke `health-it-vendor-discoverer` agent in Claude Code |
| **Claude Code research skill** | Profile a single company or health system, interactive | Invoke `researching-health-it-vendor` or `researching-health-system` skill in Claude Code |
| **CLI batch** | CSV → CSV at any scale, or discover + research in one command | `python lookup.py --skill ... --input ... --output ...` |

The same skill files (`.claude/skills/`) drive both Claude Code and CLI. Claude Code
invokes them interactively; Python loads them as prompt templates for batch runs.

### Components

```
healthtech-intel/
├── lookup.py                          # Python orchestrator — CLI batch runner
├── requirements.txt                   # anthropic>=0.40.0, pyyaml>=6.0
├── sample_vendors.csv                 # Sample health IT vendor names
├── sample_health_systems.csv          # Sample health system names
└── .claude/
    ├── agents/
    │   ├── health-it-vendor-discoverer.md   # Claude Code agent — build competitor lists
    │   ├── health-it-vendor-researcher.md   # Claude Code agent — single company profile
    │   └── health-system-researcher.md      # Claude Code agent — single hospital profile
    └── skills/
        ├── discovering-health-it-competitors/
        │   └── SKILL.md                     # Discovery prompt — NL query → company list
        ├── researching-health-it-vendor/
        │   ├── SKILL.md                     # Research prompt + output schema
        │   └── references/
        │       ├── field-definitions.md     # Enum values, confidence rules
        │       └── source-priority.md       # Which URLs/DBs to check per field
        └── researching-health-system/
            ├── SKILL.md
            └── references/
                ├── field-definitions.md
                └── source-priority.md
```

### Data Flow

**Phase 1 — Discovery** (vendor research only, optional):
```
Natural language query (--discover-query or Claude Code UI)
    ↓
discovering-health-it-competitors skill
    ↓
LLM searches web — market maps, KLAS, Crunchbase, analyst reports
    ↓
Returns JSON list of company names
    ↓
[Claude Code UI: iterative refinement → save discovered_competitors.csv]
[CLI: list feeds directly into Phase 2]
```

**Phase 2 — Research** (all skills):
```
Input CSV (entity_name column)
  —or—  CMS discovery (--discover --state XX)
  —or—  Vendor discovery output from Phase 1
    ↓
lookup.py loads skill file from .claude/skills/<skill>/SKILL.md
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

### Two Output Files

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

### Skills live in `.claude/skills/` — not in Python
The skill files (SKILL.md) are the source of truth for the research prompt and output
schema. Both interfaces read from the same file: Claude Code invokes it interactively;
`lookup.py` loads it as a prompt template for batch runs. A single edit to SKILL.md
propagates to both. No duplication.

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
([lookup.py:344-345](lookup.py#L344-L345)). A mid-run crash — network timeout,
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

### Sequential mode (`--concurrency 1`) with inter-entity delay
When debugging or running on a trial API key with tight per-minute limits, sequential
mode makes logs readable and prevents rate limits entirely. The `--delay` flag (default
1 second) adds breathing room between entities. Set `--delay 0` to remove it.

### `read_file` restricted to `.claude/skills/`
The client-side `read_file` tool ([lookup.py:165-184](lookup.py#L165-L184)) whitelists
only the skills directory. The model can load its own reference documents but cannot
read arbitrary filesystem paths — preventing accidental exposure of credentials, configs,
or other local files if the model is ever prompted adversarially through a web page it fetches.

### Cost gate before any API call
The CLI always prints an estimate and requires confirmation before calling the API
([lookup.py:565-587](lookup.py#L565-L587)). This makes cost visible and intentional.
`--yes` disables it for CI. `--max-entities` provides a hard cap as a secondary guard.

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
- Health systems: `--discover --state XX` seeds from CMS public data
- Vendors: `--discover-query "..."` seeds via the `discovering-health-it-competitors` skill

---

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Discover competitors via natural language, then profile them (two-phase, one command)
python lookup.py --skill researching-health-it-vendor \
  --discover-query "AI scribe competitors to Nuance" \
  --output results.csv

# Profile vendors from a known list
python lookup.py --skill researching-health-it-vendor --input sample_vendors.csv --output results.csv

# Profile health systems from a list
python lookup.py --skill researching-health-system --input sample_health_systems.csv --output results.csv

# Discover all hospitals in California from CMS public data, then profile them
python lookup.py --skill researching-health-system --discover --state CA --output ca_results.csv

# Profile a large batch with 10 parallel workers
python lookup.py --skill researching-health-it-vendor --input vendors.csv --output results.csv --concurrency 10

# Skip confirmation prompt (CI / scripted use)
python lookup.py --skill researching-health-it-vendor --input vendors.csv --output results.csv --yes
```

### Discover competitors via Claude Code (conversational)

Open Claude Code in this project and use the discovery agent to build a list interactively:

```
Find me AI scribe competitors to Nuance
```

The agent will propose a list, let you refine it ("remove Nuance itself", "add Suki",
"only keep Series B+"), then save `discovered_competitors.csv`. Follow up with:

```bash
python lookup.py --skill researching-health-it-vendor \
  --input discovered_competitors.csv \
  --output results.csv
```

### Profile a single company or health system via Claude Code

```
Use the researching-health-it-vendor skill to profile Abridge
```

```
Use the researching-health-system skill to profile Mayo Clinic
```

### CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--skill` | _(required)_ | `researching-health-it-vendor` or `researching-health-system` |
| `--input` | — | Input CSV path. Must have an `entity_name` column. Not required with `--discover` or `--discover-query`. |
| `--output` | _(required)_ | Clean output CSV path. A `_sources.csv` is auto-written alongside it. |
| `--discover-query` | — | Vendor skill only. Natural language query to discover companies via LLM, then research them. E.g. `"AI scribe competitors to Nuance"`. |
| `--discover` | false | Health-system skill only. Seed entity list from CMS Hospital General Information. |
| `--state` | — | Two-letter state code for `--discover` (e.g. `CA`, `NY`). |
| `--model` | `claude-sonnet-4-6` | Anthropic model name. Override via `ANTHROPIC_MODEL` env var. |
| `--delay` | `1.0` | Seconds between entities in sequential mode (`--concurrency 1`). |
| `--concurrency` | `5` | Number of parallel API calls. Recommended range: 5–10. |
| `--max-entities` | — | Safety cap on entity count. Useful for test runs. |
| `--yes` | false | Skip the cost confirmation prompt. |

---

## Output Schemas

### Vendor skill

**Clean output** (`results.csv`):
```
entity_name, product_category, primary_customer, ehr_integrations,
notable_health_system_customers, business_model, fda_status,
clinical_evidence, funding_stage, total_funding, key_investors,
num_employees, headquarters, founded_year
```

**Sources output** (`results_sources.csv`):
```
entity_name,
product_category,                product_category_source,               product_category_confidence,
primary_customer,                primary_customer_source,               primary_customer_confidence,
ehr_integrations,                ehr_integrations_source,               ehr_integrations_confidence,
notable_health_system_customers, notable_health_system_customers_source, notable_health_system_customers_confidence,
business_model,                  business_model_source,                 business_model_confidence,
fda_status,                      fda_status_source,                     fda_status_confidence,
clinical_evidence,               clinical_evidence_source,              clinical_evidence_confidence,
funding_stage,                   funding_stage_source,                  funding_stage_confidence,
total_funding,                   total_funding_source,                  total_funding_confidence,
key_investors,                   key_investors_source,                  key_investors_confidence,
num_employees,                   num_employees_source,                  num_employees_confidence,
headquarters,                    headquarters_source,                   headquarters_confidence,
founded_year,                    founded_year_source,                   founded_year_confidence,
research_notes
```

### Health system skill

**Clean output** (`results.csv`):
```
entity_name, health_system, bed_count, ownership_type, ehr_vendor,
cms_star_rating, teaching_hospital, vbc_participation, payer_mix,
annual_revenue, innovation_program, recent_tech_announcements,
cio_name, geographic_region
```

**Sources output** (`results_sources.csv`): same pattern — every field gets
`_source` and `_confidence` columns.

---

## Field Vocabulary

### Vendor skill

| Field | Allowed values |
|---|---|
| `product_category` | AI Scribe / EHR / RCM / Care Management / CDT / Patient Engagement / Clinical Decision Support / Interoperability / Other |
| `primary_customer` | Provider / Payer / Employer / DTC |
| `business_model` | SaaS / Per-Seat / PMPM / Implementation Fee / Usage-Based / Other |
| `fda_status` | Not Required / Cleared / Breakthrough Device / PMA / Pending / Unknown |
| `funding_stage` | Seed / Series A / Series B / Series C / Series D+ / Public / Profitable / Unknown |
| `clinical_evidence` | true / false |

### Health system skill

| Field | Allowed values |
|---|---|
| `ownership_type` | Non-profit / For-profit / Academic / Government / Unknown |
| `ehr_vendor` | Epic / Oracle Health / Meditech / Allscripts / athenahealth / Other / Unknown |
| `cms_star_rating` | 1 / 2 / 3 / 4 / 5 / null |
| `teaching_hospital` | true / false |
| `vbc_participation` | true / false |
| `innovation_program` | true / false |
| `geographic_region` | Northeast / Southeast / Midwest / Southwest / West |

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

### Vendor discovery — LLM-powered (`--discover-query`)

Accepts a natural language query and uses the `discovering-health-it-competitors`
skill to find candidate companies via web search:

```bash
python lookup.py \
  --skill researching-health-it-vendor \
  --discover-query "Epic-integrated RCM vendors that have raised Series B+" \
  --output results.csv \
  --max-entities 10    # recommended for first runs
```

The discovery phase runs first (~30–60 seconds), prints the discovered company list
and a brief rationale, then flows immediately into the full research pipeline. The
cost gate covers both phases.

Discovery sources: CB Insights market maps, KLAS rankings, HIMSS exhibitor lists,
Crunchbase category searches, Rock Health reports, analyst roundups.

**Alternatively, use the Claude Code UI for interactive refinement** — the
`health-it-vendor-discoverer` agent lets you refine the list before committing
to a full research run.

### Health system discovery — CMS public data (`--discover --state`)

`--discover --state XX` downloads the CMS Hospital General Information dataset
(~6,000 hospitals, publicly available) and filters to the requested state, then
feeds those hospital names into the research loop.

```bash
python lookup.py --skill researching-health-system --discover --state CA --output ca_results.csv
```

This enables prospecting an entire state's hospital landscape without maintaining
your own contact list — no equivalent free tool exists for this.

**Note:** CMS Hospital General Information does not include bed count. Filtering by
`--min-beds` requires joining with the CMS Provider of Services file (planned for v2).

---

## Cost Estimation

Before every run the CLI prints an estimate and requires confirmation:

```
Skill:          researching-health-it-vendor
Entities:       50
Model:          claude-sonnet-4-6
Concurrency:    5
Est. cost:      $8 – $20
Est. runtime:   ~8 min
Clean output:   results.csv
Sources output: results_sources.csv

Proceed? [y/N]
```

Use `--yes` to skip the prompt in CI or scripted workflows.

### Token breakdown per company

| Phase | Input tokens | Output tokens |
|---|---|---|
| Initial research prompt | ~800 | — |
| Per `web_search` result (avg) | ~3,000 | — |
| Per `web_fetch` page (avg) | ~8,000–15,000 | — |
| Final structured JSON response | — | ~1,500 |

**Typical profile:** ~6–8 web searches, ~2–4 web fetches, all within 1–2 API calls
(server-side tools run inside the API call; no client round-trips per tool use).

### Estimated cost at scale

Assumes `claude-sonnet-4-6` at $3.00/M input · $15.00/M output, plus ~$0.01/web search.

| Companies | Estimated total |
|---|---|
| 1 | ~$0.15 – $0.40 |
| 10 | ~$1.50 – $4.00 |
| 100 | ~$15 – $40 |
| 500 | ~$75 – $200 |
| 1,000 | ~$150 – $400 |

**Practical range: $0.15–$0.40 per company.** Use ~$0.25 as a planning number.

Key cost levers:
- **Web fetches dominate** — a single fetched page (SEC filing, press release) can be 10K–20K tokens
- **Obscure companies cost more** — more search rounds before useful data is found
- **`--concurrency`** does not change per-company cost, only wall-clock time
- Verify current model pricing at [anthropic.com/pricing](https://anthropic.com/pricing)

### Tuning research depth (`max_tool_rounds`)

Each skill file (`.claude/skills/*/SKILL.md`) has a `max_tool_rounds` YAML field that
caps how many API call rounds the model may use per entity before being forced to return
whatever it has found. Each round can make up to 3 web searches and 2 web fetches.

**Defaults ship set for testing (low cost, not production quality):**

| Skill | Testing default | Balanced | High rigor |
|---|---|---|---|
| `researching-health-it-vendor` | 2 | **6** | 10 |
| `researching-health-system` | 5 | **5** | 8 |

**To change it**, edit the frontmatter of the relevant skill file:

```yaml
# .claude/skills/researching-health-system/SKILL.md
---
name: researching-health-system
max_tool_rounds: 5   ← change this
---
```

**Trade-offs:**

| Setting | Effect | When to use |
|---|---|---|
| 2–3 | Fast, cheap (~$0.15/entity), may miss VBC, payer mix, IRS 990 data | Testing, spot checks |
| 5–6 | Balanced — covers most fields from primary sources (~$0.25–0.35/entity) | Standard production runs |
| 8–10 | Maximum rigor — model retries on CMS failures, fetches IRS 990s (~$0.40–0.60/entity) | High-value BD lists, audits |

Higher `max_tool_rounds` does not guarantee better results — it gives the model
more attempts to find primary sources before it gives up. For well-known health systems
and major vendors, 5–6 rounds is typically sufficient. For smaller or obscure entities,
10 rounds may still return nulls if no public data exists.

---

## Constraints

- **`ANTHROPIC_API_KEY` required** — set in environment before running
- **`anthropic>=0.40.0`** — uses the Messages API with built-in tool types
- **`pyyaml>=6.0`** — used to parse skill file frontmatter
- **Null over guess** — if a field can't be found with confidence, it returns null
  rather than a plausible-sounding but unverified answer
- **Source URLs required** — every non-null value must have a source URL
- **Two output files always written** — clean CSV and sources CSV are written in tandem;
  flushed row-by-row so a crash mid-run preserves partial results

---

## Future Work

### Checkpointing
SQLite file tracking `(entity_name, skill, status, timestamp)`.
On `--resume`, skip already-completed entities.

### Discovery: bed count filtering
Join CMS Hospital General Information with the CMS Provider of Services file
to enable `--min-beds N` filtering in discovery mode.

### Additional CLI flags
```
--resume          # skip already-completed entities
--dry-run         # print what would be researched, don't call the API
--fields FIELDS   # comma-separated subset of fields to populate
```

### Additional output formats
JSON, Google Sheets via API, SQLite database.
