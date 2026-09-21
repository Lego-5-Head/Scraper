#!/usr/bin/env python3
"""Scrape detachment stratagems and optionally structure them with an LLM."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_URL = "https://wahapedia.ru/wh40k11ed/factions/emperor-s-children/"
DEFAULT_MODEL = "qwen2.5:7b"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
COST_LINE = re.compile(r"^\s*\d+\s*CP\s*$", re.IGNORECASE)


def ensure_python_packages():
    packages = {"requests": "requests", "bs4": "beautifulsoup4", "openai": "openai"}
    missing = []
    for module, package in packages.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(f"Installing missing Python packages: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


ensure_python_packages()

import requests
from bs4 import BeautifulSoup


def fetch_html(url, cache_path):
    print(f"Fetching {url}...", file=sys.stderr)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    html = response.text
    if len(html) < 50000 or "stratagem" not in html.lower():
        raise RuntimeError("The response does not look like a rules page or was blocked.")
    cache_path.write_text(html, encoding="utf-8")
    return html


def extract_sections(html):
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    for heading in soup.select("h2"):
        name = heading.get_text(" ", strip=True)
        if "outline_header" not in (heading.get("class") or []) or not heading.select_one(".dpPts"):
            continue
        name = re.sub(r"\s+\d+DP$", "", name).strip()
        container = heading.parent
        block = next(
            (child for child in container.find_all("div", recursive=False)
             if child.find("h2", string=lambda value: value and value.strip().lower() == "stratagems")),
            None,
        )
        if block is None:
            # Khorne Daemonkin has no Stratagems h2; its block starts with
            # the word "Stratagems" in a later BreakInsideAvoid div.
            block = next(
                (candidate for candidate in heading.find_all_next("div", class_="BreakInsideAvoid")
                 if candidate.get_text(" ", strip=True).lower().startswith("stratagems")
                 and re.search(r"\d+\s*CP", candidate.get_text(" ", strip=True))),
                None,
            )
        if block:
            sections.append({"name": name, "text": block.get_text("\n", strip=True)})
    return sections


def split_chunks(text):
    lines = text.splitlines()
    starts = []
    for index, line in enumerate(lines):
        if not COST_LINE.match(line):
            continue
        name_index = index - 1
        while name_index >= 0 and not lines[name_index].strip():
            name_index -= 1
        if name_index >= 0:
            starts.append(name_index)

    chunks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def parse_positionally(chunk):
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    cost_index = next((i for i, line in enumerate(lines) if COST_LINE.match(line)), None)
    if cost_index is None:
        return None
    name = lines[cost_index - 1] if cost_index else lines[0]
    type_index = next((i for i in range(cost_index + 1, len(lines))
                       if lines[i].lower().endswith("stratagem")), None)
    description_start = type_index + 1 if type_index is not None else cost_index + 1
    return {
        "name": name,
        "cost": lines[cost_index],
        "type": lines[type_index] if type_index is not None else "",
        "description": " ".join(lines[description_start:]),
    }


class LLMClient:
    def __init__(self, base_url, model):
        from openai import OpenAI

        self.client = OpenAI(
            base_url=base_url,
            api_key=os.getenv("OPENAI_API_KEY", "ollama"),
            timeout=90,
            max_retries=0,
        )
        self.model = model

    def structure(self, chunk):
        prompt = (
            "Extract this single stratagem into JSON with exactly these string keys: "
            "name, cost, type, description. Copy text faithfully; do not invent or summarize.\n\n"
            f"SOURCE:\n{chunk}"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=1000,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.choices[0].message.content)
        required = ("name", "cost", "type", "description")
        if any(key not in data for key in required):
            raise ValueError("LLM response is missing a required field")
        return {key: str(data[key]).strip() for key in required}


def ensure_ollama(model):
    ollama = shutil.which("ollama")
    if not ollama:
        installed_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if installed_path.exists():
            ollama = str(installed_path)
    if not ollama:
        winget = shutil.which("winget")
        if not winget:
            raise RuntimeError(
                "Ollama is not installed. Install it from https://ollama.com/download/windows."
            )
        print("Ollama was not found. Installing it with winget...")
        subprocess.run(
            [winget, "install", "--id", "Ollama.Ollama", "-e",
             "--accept-source-agreements", "--accept-package-agreements"],
            check=True,
        )
        candidates = [
            shutil.which("ollama"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        ]
        ollama = next((str(path) for path in candidates if path and Path(path).exists()), None)
    if not ollama:
        raise RuntimeError("Ollama was installed but is not available yet. Restart PowerShell and run main.py again.")

    try:
        requests.get("http://localhost:11434/api/tags", timeout=2).raise_for_status()
    except requests.RequestException:
        subprocess.Popen([ollama, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(15):
            time.sleep(1)
            try:
                requests.get("http://localhost:11434/api/tags", timeout=2).raise_for_status()
                break
            except requests.RequestException:
                continue
        else:
            raise RuntimeError("Ollama did not start on http://localhost:11434.")

    models = subprocess.run([ollama, "list"], capture_output=True, text=True, check=True).stdout
    if model.lower() not in models.lower():
        print(f"Downloading Ollama model {model}...")
        subprocess.run([ollama, "pull", model], check=True)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel Ollama requests (default: 4)")
    parser.add_argument("--cache", type=Path, default=Path("page_cache.html"))
    parser.add_argument("--outdir", type=Path, default=Path.cwd())
    return parser


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        html = fetch_html(args.url, args.cache)
        sections = extract_sections(html)
        if not sections:
            raise RuntimeError("No detachment sections found in the page.")

        if args.no_llm:
            llm = None
        else:
            if args.base_url == "http://localhost:11434/v1":
                ensure_ollama(args.model)
            llm = LLMClient(args.base_url, args.model)
        output = []
        total_chunks = sum(len(split_chunks(section["text"])) for section in sections)
        completed_chunks = 0
        print(f"Found {len(sections)} detachments and {total_chunks} stratagems.", file=sys.stderr)
        for section in sections:
            entries = []
            chunks = split_chunks(section["text"])

            def process_chunk(chunk):
                fallback = parse_positionally(chunk)
                if fallback is None or llm is None:
                    return fallback
                try:
                    return llm.structure(chunk)
                except Exception as exc:
                    print(f"LLM failed; using source parse: {exc}", file=sys.stderr)
                    return fallback

            if llm is None or len(chunks) < 2:
                entries = [process_chunk(chunk) for chunk in chunks]
            else:
                with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                    futures = {
                        executor.submit(process_chunk, chunk): index
                        for index, chunk in enumerate(chunks)
                    }
                    ordered_entries = {}
                    for future in as_completed(futures):
                        result = future.result()
                        if result is not None:
                            ordered_entries[futures[future]] = result
                        completed_chunks += 1
                        print(f"Processed {completed_chunks}/{total_chunks} stratagems.", file=sys.stderr)
                    entries = [ordered_entries[index] for index in sorted(ordered_entries)]
            if entries:
                output.append({"detachment": section["name"], "stratagems": entries})

        if not output:
            raise RuntimeError("No stratagems were parsed.")
        args.outdir.mkdir(parents=True, exist_ok=True)
        (args.outdir / "output.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        print(f"Saved {args.outdir / 'output.json'}", file=sys.stderr)
    except (requests.RequestException, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
