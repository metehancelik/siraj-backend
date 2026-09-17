#!/usr/bin/env python3
"""Getirim (retrieval) kalite kontrolü - LLM'e geçmeden önce çalıştırın.

Uygulamanın örnek sorularını hibrit aramadan geçirir ve dönen ilk pasajları gösterir.
Sonuçlar konuyla ilgiliyse chat'e geçin; alakasız/çöp geliyorsa bölümleme veya Türkçe
FTS ayarını burada düzeltmek, 1-2 dakikalık LLM turuyla hata ayıklamaktan çok ucuzdur.

    python -m ingest.check_retrieval
    python -m ingest.check_retrieval "kendi sorunuz"
    python -m ingest.check_retrieval --lang en          # İngilizce yol (sorgu çevirisi)
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
    "How do I fix a Python import error?",  # alakasız - boş kalması beklenir
]

DEFAULT_QUESTIONS = [
    "Namaz nasıl kılınır?",
    "Sabır hakkında bir ayet",
    "Orucu bozan şeyler nelerdir?",
    "Zekat kimlere farzdır?",
    "Abdest nasıl alınır?",
    "Bediüzzaman haşri nasıl ispat ediyor?",  # risale (Onuncu Söz) beklenir
    "Bugünün duası nedir?",  # uygulama durumu - alakasız gelmesi/boş kalması beklenir
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
        print(f"\n{'='*70}\nSORU [{lang}]: {q}\n{'='*70}")
        passages = await retrieve(q, lang)
        if not passages:
            print("  (sonuç yok)")
            continue
        for i, p in enumerate(passages, start=1):
            snippet = " ".join(p.content.split())[:130]
            print(f"  [{i}] {p.source:6s} score={p.score:.4f} | {p.title or ''}")
            print(f"      {snippet}…")


if __name__ == "__main__":
    asyncio.run(main())
