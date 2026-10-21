"""FastAPI server — REST + WebSocket + PDF upload + static files."""

import asyncio
import uuid
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver

from agents import build_graph, ResearchState
from tools import process_pdf, get_paper_list, has_papers


tasks: dict[str, dict] = {}
memory = MemorySaver()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    tasks.clear()


app = FastAPI(lifespan=lifespan)


# --- PDF upload ---

@app.post("/api/upload")
async def upload_papers(files: list[UploadFile] = File(...)):
    results = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            results.append({"name": f.filename, "error": "Not a PDF"})
            continue
        content = await f.read()
        if len(content) > 15 * 1024 * 1024:
            results.append({"name": f.filename, "error": "File too large (max 15MB)"})
            continue
        try:
            meta = await asyncio.to_thread(process_pdf, f.filename, content)
            results.append(meta)
        except Exception as e:
            results.append({"name": f.filename, "error": str(e)})
    return {"papers": results}


@app.get("/api/papers")
async def list_papers():
    return {"papers": get_paper_list(), "has_papers": has_papers()}


# --- Research ---

@app.post("/api/research")
async def start_research(body: dict):
    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "Query is required"}, status_code=400)

    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "running",
        "current_agent": "retriever",
        "result": None,
        "ws": None,
        "human_event": asyncio.Event(),
        "human_response": None,
    }
    asyncio.create_task(_run_research(task_id, query))
    return {"task_id": task_id}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    task = tasks.get(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return {
        "task_id": task_id,
        "status": task["status"],
        "current_agent": task.get("current_agent"),
        "result": task.get("result"),
    }


@app.websocket("/ws/{task_id}")
async def ws_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    task = tasks.get(task_id)
    if not task:
        await websocket.close(code=4004, reason="Task not found")
        return

    task["ws"] = websocket
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "human_decision":
                task["human_response"] = data
                task["human_event"].set()
    except WebSocketDisconnect:
        task["ws"] = None
    except Exception:
        task["ws"] = None


async def _send_ws(task_id: str, msg: dict):
    task = tasks.get(task_id)
    ws = task.get("ws") if task else None
    if ws:
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _run_research(task_id: str, query: str):
    graph = build_graph(checkpointer=memory)
    config = {"configurable": {"thread_id": task_id}}

    initial_state: ResearchState = {
        "query": query,
        "sources": [],
        "summary": "",
        "claims": [],
        "fact_check_passed": False,
        "human_approved": None,
        "revision_feedback": "",
        "current_agent": "retriever",
        "error": None,
        "iteration": 0,
        "messages": [],
    }

    try:
        current_input = initial_state
        while True:
            async for event in graph.astream(current_input, config=config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    if node_name.startswith("__"):
                        continue
                    tasks[task_id]["current_agent"] = node_name
                    await _send_ws(task_id, {
                        "type": "agent_update",
                        "agent": node_name,
                        "data": _sanitize(node_output),
                    })

            # Stream ended — check if graph is interrupted or complete
            snapshot = graph.get_state(config)

            if snapshot.next:
                # Graph paused at interrupt (human_review node)
                tasks[task_id]["status"] = "awaiting_review"
                state_vals = snapshot.values or {}
                await _send_ws(task_id, {
                    "type": "human_review_required",
                    "data": {
                        "summary": state_vals.get("summary", ""),
                        "claims": state_vals.get("claims", []),
                        "sources": [dict(s) for s in state_vals.get("sources", [])],
                    },
                })

                # Wait for human decision
                await tasks[task_id]["human_event"].wait()
                tasks[task_id]["human_event"].clear()
                tasks[task_id]["status"] = "running"

                resp = tasks[task_id]["human_response"]
                current_input = Command(resume={
                    "approved": resp.get("approved", True),
                    "feedback": resp.get("feedback", ""),
                })
            else:
                # Graph completed
                final = snapshot.values if snapshot else {}
                tasks[task_id]["status"] = "complete"
                tasks[task_id]["result"] = _sanitize(final)
                await _send_ws(task_id, {"type": "complete", "data": _sanitize(final)})
                break

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["result"] = {"error": str(e)}
        await _send_ws(task_id, {"type": "error", "message": str(e)})


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


# Static files — mounted LAST so API routes take priority
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
