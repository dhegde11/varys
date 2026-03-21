# Using with other AI assistants

The research prompts in [`skills/`](../skills/) are plain markdown — they work with any LLM that can run web searches (Gemini, GPT-4o, Copilot, Cursor, etc.). `varys.py` is the reference batch runner and is Anthropic-only; adapting it to another provider requires changes to the API client, tool definitions, message format, and output parsing.

## What needs to change in `varys.py` to support another provider

- **API client** — replace `AsyncAnthropic` with the target SDK (`google-genai`, `openai`)
- **Built-in tools** — `web_search_20260209` and `web_fetch_20260209` are Anthropic-hosted; replace with the provider's equivalent (OpenAI: `web_search_preview` in Responses API; Gemini: Grounding with Google Search)
- **Message and content block format** — Anthropic uses `{"role": "user", "content": [...]}` with typed content blocks; OpenAI and Gemini have different schemas for tool calls and tool results
- **`stop_reason` handling** — replace `"end_turn"`, `"tool_use"`, `"pause_turn"` with the equivalent finish reason strings for the target provider
- **Prompt caching** — `"cache_control": {"type": "ephemeral"}` is Anthropic-specific; remove or replace with the provider's equivalent (OpenAI has no direct equivalent; Gemini has context caching via a separate API)
- **Structured output** — `output_config` with a JSON schema is Anthropic-specific; replace with `response_format` (OpenAI) or `response_schema` (Gemini)

## Sample prompt to adapt `varys.py` with your coding assistant

Paste this into Claude, Gemini, Copilot, or Cursor to get a targeted migration plan:

```
I have a Python CLI tool called varys.py that uses the Anthropic AsyncAnthropic SDK
to run agentic research loops. I want to adapt it to use [OpenAI / Google Gemini] instead.

The tool uses these Anthropic-specific features:
- AsyncAnthropic client with messages.create()
- Built-in server-side tools: web_search_20260209 and web_fetch_20260209
- Anthropic content block format for tool_use and tool_result messages
- stop_reason values: end_turn, tool_use, pause_turn
- Prompt caching via cache_control: {type: ephemeral}
- Structured JSON output via output_config with a JSON schema

Please read varys.py and give me a step-by-step migration plan to replace all
Anthropic-specific code with [OpenAI Responses API / Google Gemini API] equivalents,
keeping the same agentic loop logic and CSV output format.
```
