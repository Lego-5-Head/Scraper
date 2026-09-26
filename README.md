# Scraper

# Scraper

A Python scraper for [Wahapedia](https://wahapedia.ru) Warhammer 40,000 faction pages. It pulls each detachment's **stratagems** (name, CP cost, type, description) and saves them as structured JSON.

There are two versions, each in its own folder with its own `main.py`:

| Folder | How it parses |
|---|---|
| `Web-Scraper` | Regex/rule-based parsing only. Simple and fast. |
| `Web-Scraper (LLM)` | Uses a local LLM (via [Ollama](https://ollama.com)) to structure each stratagem, falling back to rule-based parsing if the LLM fails. Runs requests in parallel. |

## Requirements

- Python 3.9+
- `requests`, `beautifulsoup4` (`openai` too for the LLM version) — installed automatically on first run
- LLM version only: [Ollama](https://ollama.com) running locally. On Windows the script will try to install/start it for you; on Mac/Linux, install it yourself first (or run with `--no-llm`)

## Usage

**Regex-only version:**
```bash
cd Web-Scraper
python main.py
```
(Edit `TARGET_URL` in the script to change the target page.)

**LLM version:**
```bash
cd "Web-Scraper (LLM)"
python main.py --url "https://wahapedia.ru/wh40k11ed/factions/<faction>/"
```
Flags: `--no-llm` (skip the LLM), `--model` (default `qwen2.5:7b`), `--workers` (default `4`), `--outdir`.

## Output

Both versions write `output.json`:

```json
[
  {
    "detachment": "Detachment Name",
    "stratagems": [
      { "name": "...", "cost": "1CP", "type": "...", "description": "..." }
    ]
  }
]
```

## Notes

Pages are cached locally and only re-fetched if the cache is missing or invalid, to avoid hammering the source site. Personal project built to compare traditional scraping/parsing against LLM-assisted structuring.
