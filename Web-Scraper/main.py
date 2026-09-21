import json
import re
import sys
import textwrap
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# no OpenAI key so scrapped it
# from openai import OpenAI


TARGET_URL = "https://wahapedia.ru/wh40k11ed/factions/world-eaters/"
CACHE_PATH = Path.cwd() / "wahapedia_cache.html"


def is_valid_page(html):
    return len(html) >= 50000 and "stratagem" in html.lower()


def fetch_page(url, cache_path=CACHE_PATH):
    if cache_path.exists():
        cached_html = cache_path.read_text(encoding="utf-8")
        if is_valid_page(cached_html):
            print(f"Using cached page: {cache_path}")
            return cached_html

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    html = response.text

    if not is_valid_page(html):
        raise ValueError("Page fetch looks invalid or blocked. Check the URL and anti-bot protections.")

    cache_path.write_text(html, encoding="utf-8")
    print(f"Saved page cache to: {cache_path}")
    return html


def extract_detachment_sections(html):
    soup = BeautifulSoup(html, "html.parser")
    detachment_sections = []

    for h2 in soup.select("h2"):
        title = h2.get_text(" ", strip=True)
        if (
            "detachment" not in title.lower()
            and not re.search(r"\d+\s*DP\b", title, re.IGNORECASE)
        ):
            continue

        stratagem_block = None
        for sibling in h2.find_next_siblings():
            if sibling.name == "h2":
                break

            if sibling.name == "div":
                text = sibling.get_text(" ", strip=True)
                if "stratagems" in text.lower():
                    stratagem_block = sibling.get_text("\n", strip=True)
                    break

        if stratagem_block and len(stratagem_block) > 200:
            detachment_sections.append({
                "name": title,
                "text": stratagem_block,
            })

    return detachment_sections


def clean_structured_text(text):
    text = re.sub(r"\s+\|\s+", " | ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_stratagems(text, detachment_name):
    pattern = re.compile(
        r"(?ims)^(?P<name>[A-Z][A-Z0-9'’ !?-]+)\n"
        r"(?P<cost>\d+\s*CP)\n"
        r"(?P<type>.+?Stratagem)\n"
        r"(?P<description>.*?)(?=^[A-Z][A-Z0-9'’ !?-]+\n\d+\s*CP\n|\Z)"
    )

    entries = []
    for match in pattern.finditer(text):
        description = match.group("description")
        description = re.split(r"\bErrata\s+Show\b", description, maxsplit=1, flags=re.IGNORECASE)[0]

        entries.append({
            "name": match.group("name").strip(),
            "cost": re.sub(r"\s+", "", match.group("cost")),
            "type": match.group("type").strip(),
            "description": clean_structured_text(description),
        })

    if not entries:
        raise ValueError(f"No stratagem entries found for {detachment_name}.")

    return entries


def format_description(description):
    if not description.strip():
        return ""

    description = re.sub(r"\s+", " ", description).strip()
    description = re.sub(r"\s+([.,!?;:])", r"\1", description)
    description = re.sub(
        r"(?:^|\s+)(WHEN:|TARGET:|EFFECT:|RESTRICTIONS:)\s*",
        r"\n\1 ",
        description,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    lines = []
    for line in description.splitlines():
        wrapped = textwrap.wrap(line, width=93, subsequent_indent="    ")
        lines.extend(wrapped or [""])

    return "\n".join(lines)


def format_text_section(detachment_name, stratagems):
    lines = [
        f"DETACHMENT: {detachment_name}",
        "=" * 96,
        f"Stratagems: {len(stratagems)}",
        "",
    ]

    for index, stratagem in enumerate(stratagems, start=1):
        lines.extend([
            f"{index}. {stratagem['name']}",
            f"   Cost: {stratagem['cost']}",
            f"   Type: {stratagem['type']}",
            "   Description:",
        ])

        description = format_description(stratagem["description"])
        lines.extend(f"   {line}" for line in description.splitlines())
        lines.extend(["", "-" * 96, ""])

    return "\n".join(lines).rstrip()


# def extract_with_llm(detachment_name, detachment_text):
#     if not LLM_API_KEY:
#         raise ValueError("No OpenAI API key found. Set OPENAI_API_KEY before running this function.")
#
#     client = OpenAI(api_key=LLM_API_KEY)
#
#     prompt = f"""
#     You are extracting Warhammer 40,000 stratagems from a detachment section.
#
#     Return only valid JSON in this exact structure:
#     {{
#       "detachment": "{detachment_name}",
#       "stratagems": [
#         {{
#           "name": "stratagem name",
#           "cost": "1CP",
#           "type": "Battle Tactic Stratagem",
#           "description": "full description text"
#         }}
#       ]
#     }}
#
#     Rules:
#     - Only extract stratagem entries from the detachment text below.
#     - Ignore detachment descriptions, rules, and unrelated sections.
#     - Keep the original description text as closely as possible.
#     - Include all stratagems you can find.
#     - Return only raw JSON.
#
#     Detachment text:
#     {clean_structured_text(detachment_text)[:12000]}
#     """
#
#     response = client.chat.completions.create(
#         model="gpt-4o-mini",
#         max_tokens=1200,
#         response_format={"type": "json_object"},
#         messages=[{"role": "user", "content": prompt}],
#     )
#
#     return json.loads(response.choices[0].message.content)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cache_path = Path.cwd() / "wahapedia_cache.html"
    if cache_path.exists():
        cache_path.unlink()
        print(f"Cleared page cache: {cache_path}")

    try:
        html = fetch_page(TARGET_URL, cache_path)
    except Exception as exc:
        print(f"Fetch failed: {exc}")
        raise SystemExit(1)

    sections = extract_detachment_sections(html)
    if not sections:
        print("No detachment sections found. Check selectors or page format.")
        raise SystemExit(1)

    output_lines = []

    json_output = []
    for section in sections:
        text_block = section["text"]
        try:
            entries = extract_stratagems(text_block, section["name"])
        except ValueError as exc:
            print(f"Skipping {section['name']}: {exc}")
            continue

        block = format_text_section(section["name"], entries)
        output_lines.append(block)
        print(f"\n{block}")

        json_output.append({
            "detachment": section["name"],
            "stratagems": entries,
        })

    if not json_output:
        print("No valid detachment sections could be parsed.")
        raise SystemExit(1)

    output_directory = Path.cwd()
    output_path = output_directory / "output.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(output_lines))

    json_output_path = output_directory / "output.json"
    with open(json_output_path, "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)

    print(f"\nSaved text output to: {output_path} ({output_path.stat().st_size} bytes)")
    print(f"Saved JSON output to: {json_output_path} ({json_output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()