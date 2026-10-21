"""LangGraph state machine — 3 agents + human-in-the-loop approval gate."""

import json
import os
from typing import Optional
from typing_extensions import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

from tools import Source, gather_sources, has_papers


# --- State ---

class Claim(TypedDict):
    text: str
    confidence: float
    supporting_sources: list[str]
    contradictions: list[str]


class ResearchState(TypedDict):
    query: str
    sources: list[Source]
    summary: str
    claims: list[Claim]
    fact_check_passed: bool
    human_approved: Optional[bool]
    revision_feedback: str
    current_agent: str
    error: Optional[str]
    iteration: int
    messages: list[dict]


# --- LLM ---

def get_llm():
    return ChatGroq(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        api_key=os.environ.get("GROQ_API_KEY", ""),
        temperature=0.1,
    )


# --- Agent nodes ---

async def retriever_agent(state: ResearchState) -> dict:
    feedback = state.get("revision_feedback")
    query = f"{state['query']} — focus on: {feedback}" if feedback else state["query"]
    sources = await gather_sources(query)

    paper_count = sum(1 for s in sources if s["source_type"] == "paper")
    web_count = len(sources) - paper_count

    msg = f"Found {len(sources)} sources"
    if paper_count:
        msg += f" ({web_count} web/academic, {paper_count} from uploaded papers)"
    else:
        msg += " across web, academic, and Wikipedia"

    return {
        "sources": sources,
        "current_agent": "retriever",
        "iteration": state["iteration"] + 1,
        "messages": state["messages"] + [{"agent": "retriever", "content": msg}],
    }


async def summarizer_agent(state: ResearchState) -> dict:
    if not state["sources"]:
        return {
            "error": "No sources to summarize.",
            "current_agent": "summarizer",
            "messages": state["messages"] + [{"agent": "summarizer", "content": "No sources available."}],
        }

    web_sources = [s for s in state["sources"] if s["source_type"] != "paper"]
    paper_sources = [s for s in state["sources"] if s["source_type"] == "paper"]

    web_text = "\n\n".join(
        f"[{i+1}] {s['title']} ({s['source_type']})\nURL: {s['url']}\n{s['snippet']}"
        for i, s in enumerate(web_sources)
    )
    paper_text = "\n\n".join(
        f"[P{i+1}] {s['title']}\n{s['snippet']}"
        for i, s in enumerate(paper_sources)
    )

    feedback_note = ""
    if state.get("revision_feedback"):
        feedback_note = f"\n\nREVISION REQUESTED: {state['revision_feedback']}\nAddress this feedback specifically."

    # Switch prompt based on whether papers are uploaded
    if paper_sources:
        prompt = f"""You are a research analyst. Analyze the following sources and uploaded research papers.

QUERY: {state['query']}

WEB/ACADEMIC SOURCES:
{web_text}

UPLOADED RESEARCH PAPERS (excerpts):
{paper_text}
{feedback_note}

Your task:
1. **Summary**: Synthesize key findings from both web sources and papers
2. **Research Gaps**: Identify specific gaps — areas where:
   - The papers propose solutions that aren't yet validated by current web sources
   - Current web findings suggest new directions the papers haven't explored
   - Contradictions exist between papers and current state of the field
   - Topics the papers cite as "future work" that may or may not have been addressed
3. **Recommendations**: Suggest concrete next research directions

Use [1], [2] for web citations and [P1], [P2] for paper citations.
Use markdown formatting with clear ## sections."""
    else:
        prompt = f"""You are a research gap analyst. Analyze these sources to identify research gaps and open problems.

QUERY: {state['query']}

SOURCES:
{web_text}
{feedback_note}

Your task:
1. **Current State**: Brief overview of where the field stands based on sources
2. **Research Gaps**: Identify specific gaps — areas where:
   - Key problems remain unsolved or only partially addressed
   - Contradictions or disagreements exist between sources
   - Topics mentioned as "future work" or "limitations" that haven't been explored
   - Methodological weaknesses or missing evaluations
3. **Open Questions**: List concrete research questions that remain unanswered
4. **Recommendations**: Suggest directions for new research to fill these gaps

Use [1], [2] for citations. Use markdown formatting with clear ## sections."""

    llm = get_llm()
    resp = await llm.ainvoke([
        SystemMessage(content="You are a precise research gap analyst. Identify what's missing, not just what exists."),
        HumanMessage(content=prompt),
    ])
    return {
        "summary": resp.content,
        "current_agent": "summarizer",
        "revision_feedback": "",
        "messages": state["messages"] + [{"agent": "summarizer", "content": "Research gap analysis generated with inline citations."}],
    }


async def fact_checker_agent(state: ResearchState) -> dict:
    if not state["summary"]:
        return {
            "error": "No summary to fact-check.",
            "current_agent": "fact_checker",
            "messages": state["messages"] + [{"agent": "fact_checker", "content": "No summary to verify."}],
        }

    sources_text = "\n".join(
        f"[{i+1}] {s['title']}: {s['snippet']}" for i, s in enumerate(state["sources"])
    )

    prompt = f"""You are a research gap verifier. Analyze the gap analysis below against the provided sources.

GAP ANALYSIS:
{state['summary']}

SOURCES:
{sources_text}

For each identified research gap or claim in the analysis, output a JSON array of objects with:
- "text": the gap or claim (one sentence)
- "confidence": 0.0-1.0 (how well sources support this being a real gap)
- "supporting_sources": list of source URLs that support it
- "contradictions": list of strings describing any conflicts (e.g. a source that already addresses the gap)

Return ONLY a valid JSON array. No markdown fences, no explanation."""

    llm = get_llm()
    resp = await llm.ainvoke([
        SystemMessage(content="You are a meticulous fact-checker. Return only valid JSON."),
        HumanMessage(content=prompt),
    ])

    try:
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        claims = json.loads(raw)
    except (json.JSONDecodeError, IndexError):
        claims = [{"text": "Could not parse claims", "confidence": 0.5, "supporting_sources": [], "contradictions": ["Parse error"]}]

    passed = all(c.get("confidence", 0) >= 0.6 for c in claims)

    return {
        "claims": claims,
        "fact_check_passed": passed,
        "current_agent": "fact_checker",
        "messages": state["messages"] + [{
            "agent": "fact_checker",
            "content": f"Verified {len(claims)} claims. {'All checks passed.' if passed else 'Some claims need stronger evidence — routing back for revision.'}",
        }],
    }


async def human_review_node(state: ResearchState) -> dict:
    decision = interrupt({
        "summary": state["summary"],
        "claims": state["claims"],
        "sources": [dict(s) for s in state["sources"]],
    })

    return {
        "human_approved": decision.get("approved", True),
        "revision_feedback": decision.get("feedback", ""),
        "current_agent": "human_review",
        "iteration": state["iteration"] + 1,
        "messages": state["messages"] + [{
            "agent": "human_review",
            "content": "Approved." if decision.get("approved") else f"Revision requested: {decision.get('feedback', '')}",
        }],
    }


# --- Routing (the multi-stage loop) ---
#
# This is the key interview concept:
#
#   retriever → summarizer → fact_checker ─┐
#       ↑                        ↑          │
#       │  (need more sources)   │          │ passed
#       └────────────────────────┘          ▼
#                                     human_review
#                                       │       │
#                                  approved   rejected
#                                       │       │
#                                      END   summarizer (with feedback)
#
# If fact-checker says claims are weak → back to retriever for more sources
# If human rejects → back to summarizer with feedback
# Iteration counter prevents infinite loops (circuit breaker)

def route_after_retrieval(state: ResearchState) -> str:
    if state.get("sources"):
        return "summarizer"
    if state["iteration"] < 3:
        return "retriever"
    return END


def route_after_fact_check(state: ResearchState) -> str:
    if state.get("fact_check_passed"):
        return "human_review"
    if state["iteration"] < 3:
        return "retriever"  # get more sources to strengthen claims
    return "human_review"  # show what we have rather than loop forever


def route_after_human_review(state: ResearchState) -> str:
    if state.get("human_approved"):
        return END
    if state["iteration"] < 5:
        return "retriever"  # re-fetch sources with feedback, then full pipeline again
    return END


# --- Graph ---

def build_graph(checkpointer=None):
    graph = StateGraph(ResearchState)

    graph.add_node("retriever", retriever_agent)
    graph.add_node("summarizer", summarizer_agent)
    graph.add_node("fact_checker", fact_checker_agent)
    graph.add_node("human_review", human_review_node)

    graph.set_entry_point("retriever")

    graph.add_conditional_edges("retriever", route_after_retrieval, {
        "summarizer": "summarizer",
        "retriever": "retriever",
        END: END,
    })
    graph.add_edge("summarizer", "fact_checker")
    graph.add_conditional_edges("fact_checker", route_after_fact_check, {
        "human_review": "human_review",
        "retriever": "retriever",
    })
    graph.add_conditional_edges("human_review", route_after_human_review, {
        "retriever": "retriever",
        END: END,
    })

    return graph.compile(checkpointer=checkpointer)
