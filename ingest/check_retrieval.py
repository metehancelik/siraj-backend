#!/usr/bin/env python3
"""Getirim (retrieval) kalite kontrolü — LLM'e geçmeden önce çalıştırın.

Uygulamanın örnek sorularını hibrit aramadan geçirir ve dönen ilk pasajları gösterir.
Sonuçlar konuyla ilgiliyse chat'e geçin; alakasız/çöp geliyorsa bölümleme veya Türkçe
FTS ayarını burada düzeltmek, 1-2 dakikalık LLM turuyla hata ayıklamaktan çok ucuzdur.

    python -m ingest.check_retrieval
    python -m ingest.check_retrieval "kendi sorunuz"
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import retrieve  # noqa: E402

DEFAULT_QUESTIONS = [
    "Namaz nasıl kılınır?",
    "Sabır hakkında bir ayet",
    "Orucu bozan şeyler nelerdir?",
    "Zekat kimlere farzdır?",
    "Abdest nasıl alınır?",
    "Bugünün duası nedir?",  # uygulama durumu — alakasız gelmesi/boş kalması beklenir
]


async def main() -> None:
    questions = sys.argv[1:] or DEFAULT_QUESTIONS
    for q in questions:
        print(f"\n{'='*70}\nSORU: {q}\n{'='*70}")
        passages = await retrieve(q)
        if not passages:
            print("  (sonuç yok)")
            continue
        for i, p in enumerate(passages, start=1):
            snippet = " ".join(p.content.split())[:130]
            print(f"  [{i}] {p.source:6s} score={p.score:.4f} | {p.title or ''}")
            print(f"      {snippet}…")


if __name__ == "__main__":
    asyncio.run(main())
