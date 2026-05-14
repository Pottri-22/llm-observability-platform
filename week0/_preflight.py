"""Throwaway preflight: verify Groq catalog model IDs + Ollama availability.

Not part of the sprint artifact; safe to delete after Block 2 completes.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")

CATALOG_GROQ_IDS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]


def check_groq() -> None:
    print("\n[Groq] /v1/models preflight")
    key = os.environ.get("GROQ_API_KEY", "")
    if not key.startswith("gsk_"):
        print("  GROQ_API_KEY missing/malformed in .env")
        return
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        print("  network error:", exc)
        return
    live_ids = sorted(m["id"] for m in data["data"])
    for cat_id in CATALOG_GROQ_IDS:
        status = "OK (live)" if cat_id in live_ids else "MISSING (decommissioned!)"
        print("  " + cat_id.ljust(40) + status)
    print("\n  All Groq llama-family models currently available:")
    for i in live_ids:
        if "llama" in i.lower():
            print("    " + i)


def check_ollama() -> None:
    print("\n[Ollama] localhost:11434 preflight")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        print("  Ollama not reachable:", exc)
        return
    pulled = [m["name"] for m in data.get("models", [])]
    print("  models pulled locally:")
    for name in pulled:
        print("    " + name)
    if "llama3.2:3b" in pulled:
        print("  llama3.2:3b OK")
    else:
        print("  llama3.2:3b NOT pulled (run: ollama pull llama3.2:3b)")


def main() -> int:
    check_groq()
    check_ollama()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
