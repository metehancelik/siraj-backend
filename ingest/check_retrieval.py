#!/usr/bin/env python3
"""Retrieval quality check - run it before involving the LLM.

Runs the app's sample questions through hybrid search and shows the top passages returned.
If the results are on topic, move on to chat; if they are irrelevant or junk, fixing the
chunking or the Turkish FTS setup here is far cheaper than debugging through 1-2 minute
LLM round trips.

    python -m ingest.check_retrieval
    python -m ingest.check_retrieval "your own question"
    python -m ingest.check_retrieval --lang en          # English path (query translation)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import retrieve  # noqa: E402

DEFAULT_QUESTIONS_EN = [
    "How is ablution performed?",
    "What invalidates the fast?",
    "Who is required to pay zakat?",
    "How does Bediuzzaman prove the resurrection?",
    "How do I fix a Python import error?",  # off topic - expected to come back empty
]

DEFAULT_QUESTIONS = [
    "Namaz nasıl kılınır?",
    "Sabır hakkında bir ayet",
    "Orucu bozan şeyler nelerdir?",
    "Zekat kimlere farzdır?",
    "Abdest nasıl alınır?",
    "Bediüzzaman haşri nasıl ispat ediyor?",  # expects risale (Onuncu Söz)
    "Bugünün duası nedir?",  # app state - expected to be irrelevant or empty
]


async def main() -> None:
    args = sys.argv[1:]
    lang = "tr"
    if "--lang" in args:
        i = args.index("--lang")
        lang = args[i + 1]
        del args[i:i + 2]
    questions = args or (DEFAULT_QUESTIONS_EN if lang == "en" else DEFAULT_QUESTIONS)
    for q in questions:
        print(f"\n{'='*70}\nQUESTION [{lang}]: {q}\n{'='*70}")
        passages = await retrieve(q, lang)
        if not passages:
            print("  (no results)")
            continue
        for i, p in enumerate(passages, start=1):
            snippet = " ".join(p.content.split())[:130]
            print(f"  [{i}] {p.source:6s} score={p.score:.4f} | {p.title or ''}")
            print(f"      {snippet}…")


if __name__ == "__main__":
    asyncio.run(main())
