"""Search tools + PDF processing + vector DB. All free, no API keys except Groq."""

import asyncio
import hashlib
import httpx
from typing import TypedDict, Literal

import chromadb


class Source(TypedDict):
    title: str
    url: str
    snippet: str
    source_type: Literal["web", "academic", "wikipedia", "paper"]


# --- Paper store (ChromaDB in-memory) ---

_chroma = chromadb.Client()
_collection = _chroma.get_or_create_collection("papers")
_paper_meta: list[dict] = []  # track uploaded papers for the UI


def process_pdf(filename: str, file_bytes: bytes) -> dict:
    """Extract text from PDF, chunk it, store in ChromaDB. Returns paper metadata."""
    import pymupdf

    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    pages_text = [page.get_text() for page in doc]
    full_text = "\n\n".join(pages_text)
    doc.close()

    chunks = _chunk_text(full_text, chunk_size=800, overlap=150)
    if not chunks:
        return {"name": filename, "pages": len(pages_text), "chunks": 0, "error": "No text extracted"}

    # Deduplicate: use content hash as ID
    ids = [hashlib.md5(f"{filename}_{i}_{c[:50]}".encode()).hexdigest() for i, c in enumerate(chunks)]

    _collection.upsert(
        documents=chunks,
        ids=ids,
        metadatas=[{"source": filename, "chunk_index": i} for i in range(len(chunks))],
    )

    meta = {"name": filename, "pages": len(pages_text), "chunks": len(chunks)}
    _paper_meta.append(meta)
    return meta


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Split text into overlapping chunks by paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > chunk_size and current:
            chunks.append(current.strip())
            # Keep overlap from end of previous chunk
            current = current[-overlap:] + "\n\n" + para if overlap else para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


def search_papers(query: str, n_results: int = 5) -> list[Source]:
    """Search uploaded papers via ChromaDB vector similarity."""
    if _collection.count() == 0:
        return []
    n = min(n_results, _collection.count())
    results = _collection.query(query_texts=[query], n_results=n)
    sources = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        sources.append(Source(
            title=meta.get("source", "Uploaded Paper"),
            url=f"paper://{meta.get('source', 'unknown')}#chunk{meta.get('chunk_index', i)}",
            snippet=doc[:300],
            source_type="paper",
        ))
    return sources


def get_paper_list() -> list[dict]:
    return list(_paper_meta)


def has_papers() -> bool:
    return _collection.count() > 0


# --- Web search tools ---

async def search_duckduckgo(query: str, max_results: int = 5) -> list[Source]:
    from duckduckgo_search import DDGS

    def _search():
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    results = await asyncio.to_thread(_search)
    return [
        Source(
            title=r.get("title", ""),
            url=r.get("href", ""),
            snippet=r.get("body", ""),
            source_type="web",
        )
        for r in results
    ]


async def search_arxiv(query: str, max_results: int = 3) -> list[Source]:
    import arxiv

    def _search():
        client = arxiv.Client()
        search = arxiv.Search(query=query, max_results=max_results, sort_by=arxiv.SortCriterion.Relevance)
        return list(client.results(search))

    results = await asyncio.to_thread(_search)
    return [
        Source(
            title=r.title,
            url=r.entry_id,
            snippet=r.summary[:300],
            source_type="academic",
        )
        for r in results
    ]


async def search_wikipedia(query: str) -> list[Source]:
    import wikipedia

    def _search():
        try:
            titles = wikipedia.search(query, results=2)
            sources = []
            for t in titles:
                try:
                    page = wikipedia.page(t, auto_suggest=False)
                    sources.append(Source(
                        title=page.title,
                        url=page.url,
                        snippet=page.summary[:300],
                        source_type="wikipedia",
                    ))
                except (wikipedia.DisambiguationError, wikipedia.PageError):
                    continue
            return sources
        except Exception:
            return []

    return await asyncio.to_thread(_search)


async def search_semantic_scholar(query: str, max_results: int = 3) -> list[Source]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query, "limit": max_results, "fields": "title,abstract,url"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            Source(
                title=p.get("title", ""),
                url=p.get("url") or f"https://www.semanticscholar.org/paper/{p.get('paperId', '')}",
                snippet=(p.get("abstract") or "")[:300],
                source_type="academic",
            )
            for p in data.get("data", [])
            if p.get("title")
        ]


async def gather_sources(query: str) -> list[Source]:
    """Run all search tools concurrently + paper search. Failures swallowed."""
    tasks = [
        search_duckduckgo(query),
        search_arxiv(query),
        search_wikipedia(query),
        search_semantic_scholar(query),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sources: list[Source] = []
    seen_urls: set[str] = set()
    for r in results:
        if isinstance(r, list):
            for s in r:
                if s["url"] not in seen_urls:
                    seen_urls.add(s["url"])
                    sources.append(s)

    # Also search uploaded papers (sync, fast since in-memory)
    paper_results = search_papers(query, n_results=5)
    for s in paper_results:
        sources.append(s)

    return sources
