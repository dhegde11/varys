# Contributing

Thanks for your interest in contributing to Varys.

## Setup

```bash
git clone https://github.com/dhegde11/varys.git
cd varys
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

## Running tests

Unit tests (no API key required):

```bash
python3 -m pytest tests/test_varys.py -v
```

Live API tests (requires `ANTHROPIC_API_KEY`, costs ~$0.50):

```bash
python3 -m pytest tests/ -v --ignore=tests/test_research_cached.py
```

## What to contribute

Good areas for contribution:

- **Skill improvements** — better source-priority rules, new fields, improved prompt instructions in `.claude/skills/*/SKILL.md`
- **Bug fixes** in `varys.py` — error handling, edge cases in the agentic loop
- **New output formats** — JSON, SQLite
- **Checkpointing / `--resume`** — skip already-completed entities on re-run
- **Tests** — unit tests with mocked API responses

## Pull request guidelines

- Keep PRs focused — one feature or fix per PR
- If you change a skill prompt, include a before/after example of output quality
- If you change `varys.py`, run the unit tests before submitting
- Update the README if you add a flag, field, or feature

## Skill file format

Each skill in `.claude/skills/*/SKILL.md` has a YAML frontmatter block followed by the prompt body. The frontmatter fields used by `varys.py` are:

- `name` — skill identifier, matches the subcommand target (`vendor` or `health-system`)
- `mode` — `vendor`, `health-system`, or `discovery` — controls CSV column mapping
- `max_tool_rounds` — max API call rounds per entity (raise for higher rigor, lower for speed/cost)

The prompt body uses `{entity}` and `{query}` as template variables substituted at runtime.
