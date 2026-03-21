# Skills

These are the skill files that power Varys. Each skill defines a structured research workflow — what to look for, how to prioritize sources, how to handle missing data, and what schema to return. They work with any LLM that can execute web searches.

## What's here

| File | What it does |
|---|---|
| [`profile-health-system.md`](profile-health-system.md) | Profile a hospital or health system for BD prospecting |
| [`profile-health-it-vendor.md`](profile-health-it-vendor.md) | Profile a health IT company for competitive intelligence |
| [`discover-health-it-vendor.md`](discover-health-it-vendor.md) | Discover companies matching a natural language query |

## How to use with AI assistants

These files follow the [Agent Skills](https://agentskills.io) open standard, supported by Claude Code, OpenAI Codex, Cursor, GitHub Copilot, Gemini CLI, and others.

**With a compatible coding assistant** — drop the skill files into your agent's skills directory. They'll be auto-discovered and available by name. See your tool's documentation for the exact path. If you're using Claude Code, this repo's `.claude/skills/` folder is already wired in — clone the repo and they will be active automatically.

**With an AI chat interface** (Claude.ai, ChatGPT, etc.) — paste the skill content (everything below the YAML frontmatter) into your project instructions. Then mention the entity name in your message and the skill will apply automatically.
