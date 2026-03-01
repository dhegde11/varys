# healthtech-intel

A market intelligence tool for the health IT ecosystem with two research skills:

- **Vendor research** — Profile health IT companies for competitive analysis. Who are they, what do they sell, who have they sold to, how are they funded, and what is their regulatory status?
- **Health system research** — Profile hospitals and health systems for BD prospecting. A free, open-source alternative to [Definitive Healthcare](https://www.definitivehc.com/), powered by public CMS data and Claude's web search.

Both skills share the same architecture: structured JSON output, per-field source URLs, and confidence scores.

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

### Two Interfaces

| Interface | Use case | How |
|---|---|---|
| **Claude Code skill** | Single company, interactive | Invoke `researching-health-it-vendor` or `researching-health-system` skill in Claude Code |
| **CLI batch** | CSV → CSV, any scale | `python lookup.py --skill ... --input ... --output ...` |

The same skill file (`.claude/skills/`) drives both interfaces. Claude Code invokes it
interactively; Python loads it as the research prompt for batch runs.

### Components

```
healthtech-intel/
├── lookup.py                          # Python orchestrator — CLI batch runner
├── requirements.txt                   # anthropic>=0.40.0, pyyaml>=6.0
├── sample_vendors.csv                 # Sample health IT vendor names
├── sample_health_systems.csv          # Sample health system names
└── .claude/
    ├── agents/
    │   ├── health-it-vendor-researcher.md   # Claude Code agent (single company)
    │   └── health-system-researcher.md      # Claude Code agent (single hospital)
    └── skills/
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

```
Input CSV (entity_name column)  —or—  CMS discovery (--discover --state XX)
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

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Profile health IT vendors
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

### Single company via Claude Code

Open Claude Code in this project and invoke a skill directly:

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
| `--input` | — | Input CSV path. Must have an `entity_name` column. Not required with `--discover`. |
| `--output` | _(required)_ | Clean output CSV path. A `_sources.csv` is auto-written alongside it. |
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

`--discover --state XX` downloads the CMS Hospital General Information dataset
(~6,000 hospitals, publicly available) and filters to the requested state, then
feeds those hospital names into the research loop.

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
| Per `web_search` result (avg) | ~3,000 | ~150 |
| Per `web_fetch` page (avg) | ~8,000–15,000 | ~150 |
| Final structured JSON response | — | ~1,500 |

**Typical profile:** ~6–8 web searches, ~2–4 web fetches, ~8–10 total rounds.

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
