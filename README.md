# Research Gap Finder

Multi-agent system that identifies research gaps, contradictions, and unexplored directions in any field. Upload academic papers and ask a question — the system cross-references your papers with live web and academic sources.

**Live demo**: [research-agent-b8k4.onrender.com](https://research-agent-b8k4.onrender.com)

## Architecture

```
retriever → summarizer → fact_checker ─┐
    ↑                                    │ passed
    │  (weak claims OR human rejects)    ▼
    └──────────────────────────── human_review
                                     │
                                  approved
                                     │
                                    END
```

Three specialized agents orchestrated by [LangGraph](https://github.com/langchain-ai/langgraph) with conditional routing loops and human-in-the-loop approval:

| Agent | Role |
|-------|------|
| **Retriever** | Searches DuckDuckGo, ArXiv, Wikipedia, Semantic Scholar concurrently. Retrieves relevant chunks from uploaded PDFs via ChromaDB vector search. |
| **Gap Analyzer** | Cross-references web sources against uploaded papers to identify unsolved problems, contradictions, and "future work" that hasn't been explored. |
| **Fact Checker** | Verifies each identified gap against sources, assigns confidence scores, flags contradictions. Routes back to retriever if evidence is weak. |

**Human-in-the-loop**: after fact-checking, the user reviews the analysis. Approve to finalize, or reject with feedback — the full pipeline re-runs with the feedback as context.

**Circuit breaker**: an iteration counter caps loops at 3–5 to prevent infinite retries.

## Stack

- **Orchestration**: LangGraph StateGraph with conditional edges and `interrupt()` for human approval
- **LLM**: Groq free tier (`openai/gpt-oss-120b`)
- **Vector DB**: ChromaDB (in-memory) for semantic search over uploaded PDFs
- **PDF parsing**: PyMuPDF — extract text, chunk into ~500 char pieces
- **Search tools**: DuckDuckGo, ArXiv, Wikipedia, Semantic Scholar (all free, no API keys)
- **Backend**: FastAPI + WebSocket for real-time agent progress streaming
- **Frontend**: Vanilla HTML/CSS/JS — Claude-inspired dark/light theme, pipeline visualizer
- **Deploy**: Render free tier (single service, static files served from FastAPI)

## Run locally

```bash
git clone https://github.com/TDahiya/research-agent.git
cd research-agent
python -m venv venv && venv\Scripts\activate  # or source venv/bin/activate
pip install -r requirements.txt
```

Create `.env`:
```
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

```bash
uvicorn app:app --reload
```

Open `http://localhost:8000`.

## Project structure

```
├── app.py            # FastAPI server, WebSocket, task management
├── agents.py         # LangGraph state machine, 3 agents, routing logic
├── tools.py          # Search tools, PDF processing, ChromaDB vector store
├── static/
│   ├── index.html    # Single-page app
│   ├── style.css     # Dark/light theme (CSS custom properties)
│   └── app.js        # WebSocket client, pipeline visualizer, UI logic
├── requirements.txt
├── render.yaml       # Render deploy config
└── Procfile
```

## Key design decisions

**Why LangGraph over plain chains?** Conditional edges create loops — fact-checker can reject and route back to retriever, human can reject and trigger a full re-run. Plain chains can't loop without messy recursion.

**Why `interrupt()` for human-in-the-loop?** LangGraph's native pattern. The graph state serializes and pauses mid-node. Resume with `Command(resume=value)`. Could persist to Redis for hours-long reviews.

**Why `asyncio.gather(return_exceptions=True)`?** One broken API (ArXiv down) doesn't kill the other 3 searches.

**Why vanilla JS?** No build step, no node_modules. For 6 files, React is overhead.

**Why Render over Vercel?** This app needs persistent WebSocket connections and long-running background tasks (30–60s agent pipeline). Vercel's serverless functions can't support either.
