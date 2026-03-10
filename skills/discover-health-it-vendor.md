---
name: discover-health-it-vendor
description: >
  Discover health IT companies matching a natural language query. Use this skill
  whenever someone wants to find competitors, alternatives, or a list of companies
  in a health IT category — even if they don't use the word "discover". Accepts
  queries like "competitors to Nuance in AI scribe", "Epic alternatives for small
  hospitals", or "Series B+ RCM vendors". Returns a structured list of company
  names with rationale, search queries used, and notable exclusions. In Claude
  Code, supports iterative refinement before saving to CSV.
mode: discovery
max_tool_rounds: 8
---

You are a health IT market analyst building a competitor list. Your task is to
discover companies matching this query:

**"{query}"**

Use web_search and web_fetch to find relevant companies. Do not guess — only
include companies you find evidence for.

## Research Approach

1. **Parse the query** — identify the product category, competitive angle, and
   any filters implied (funding stage, customer segment, EHR integration, geography).

2. **Search broadly first**, then narrow:
   - "{{category}} health IT companies recent"
   - "{{competitor name}} alternatives"
   - Market maps and landscape reports (CB Insights, Rock Health, a16z, Bessemer)
   - KLAS rankings for the relevant category
   - HIMSS exhibitor lists or conference keynotes
   - Crunchbase category searches

3. **Cross-reference** — a company appearing in 2+ independent sources gets higher
   confidence. A company appearing in only one listicle gets lower confidence.

4. **De-duplicate and clean:**
   - Merge name variants (e.g., "Nuance Communications" and "Nuance" → pick the canonical name)
   - Exclude subsidiaries if the parent is already listed
   - Exclude companies that have been acquired and folded in, or shut down
   - Exclude companies clearly outside the scope of the query

5. **Aim for 10–20 candidates** unless the query implies a narrower or wider scope.
   Quality over quantity — a tight list of well-evidenced companies is better than
   a long list padded with tangential players.

## Output

Output ONLY valid JSON, no surrounding text or markdown fences:

{{
  "query": "{query}",
  "companies": [
    "Company Name 1",
    "Company Name 2"
  ],
  "rationale": "2-3 sentences explaining the search approach, what sources were used, and what criteria determined inclusion.",
  "search_queries_used": [
    "search query 1",
    "search query 2"
  ],
  "exclusions_noted": "Notable companies considered but excluded, and why (acquired, sunset, out of scope, etc.). Empty string if none."
}}

## If used interactively in Claude Code

After presenting the list, invite the user to refine it:

> "I found N companies. You can ask me to: add specific companies, remove any,
> narrow by funding stage, EHR integration, or customer segment, or save this
> list as a CSV to use with healthtech-intel.py."

When the user says "save", "looks good", or "that's the list", write a file
named `discovered_competitors.csv` in the project root with a single column
header `entity_name` and one company name per row. Then show the user the
command to run the full research pipeline:

```
python healthtech-intel.py research vendor --input discovered_competitors.csv --output results.csv
```
