import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()  # must run before pipeline import so OPENROUTER_API_KEY is set when _openrouter client is created

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

from pipeline import (
    fetch_transcript,
    fetch_video_metadata,
    analyze_transcript,
    analyze_knowledge_graph,
    flatten_transcript,
    chunk_transcript,
    format_analysis_chunks,
    generate_embedding,
    generate_embeddings_batch,
    generate_chat_answer,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase client — uses the anon key.
# All pipeline DB writes go through SECURITY DEFINER RPC functions that are
# protected by PIPELINE_SECRET (stored in Supabase Vault, never in git).
# No service_role key is needed.
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
PIPELINE_SECRET = os.environ["PIPELINE_SECRET"]


supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


# ---------------------------------------------------------------------------
# Auth dependency — validates the user's Supabase JWT
# ---------------------------------------------------------------------------
async def get_current_user(request: Request) -> dict:
    """Extract and validate the user's Supabase JWT from Authorization header."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")
    token = auth_header[7:]
    try:
        response = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if not response.user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"id": str(response.user.id)}


# ---------------------------------------------------------------------------
# Helpers — thin wrappers around the pipeline_* RPC functions
# ---------------------------------------------------------------------------
def _rpc_get_video(video_id: str) -> dict | None:
    """Returns the video row dict, or None if not found."""
    result = supabase.rpc("pipeline_get_video", {
        "p_video_id": video_id,
        "p_secret": PIPELINE_SECRET,
    }).execute()
    return result.data[0] if result.data else None


def _rpc_reset_video_data(video_id: str) -> None:
    """Deletes existing chunks, concepts, analyses, and quizzes for a video.
    Makes every pipeline run idempotent — retries start from a clean slate."""
    supabase.rpc("pipeline_reset_video_data", {
        "p_video_id": video_id,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_update_video(
    video_id: str,
    status: str,
    title: str | None = None,
    thumbnail_url: str | None = None,
    error_message: str | None = None,
) -> None:
    supabase.rpc("pipeline_update_video", {
        "p_video_id": video_id,
        "p_status": status,
        "p_title": title,
        "p_thumbnail_url": thumbnail_url,
        "p_error_message": error_message,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_save_analysis(
    video_id: str,
    summary_short: str,
    summary_detailed: str,
    key_insights: list[str],
    action_items: list[str],
    glossary: list[dict],
    misconceptions: list[dict],
) -> None:
    supabase.rpc("pipeline_save_analysis", {
        "p_video_id": video_id,
        "p_summary_short": summary_short,
        "p_summary_detailed": summary_detailed,
        "p_key_insights": key_insights,
        "p_action_items": action_items,
        "p_glossary": glossary,
        "p_misconceptions": misconceptions,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_save_quizzes(video_id: str, all_items: list[dict]) -> None:
    """Save both MCQ quizzes and flashcards into the quizzes table."""
    supabase.rpc("pipeline_save_quizzes", {
        "p_video_id": video_id,
        "p_quizzes": all_items,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_save_chunk(
    video_id: str,
    content: str,
    start_time: float,
    end_time: float,
    embedding: list[float],
) -> None:
    supabase.rpc("pipeline_save_chunk", {
        "p_video_id": video_id,
        "p_content": content,
        "p_start_time": start_time,
        "p_end_time": end_time,
        "p_embedding": embedding,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_get_analysis(video_id: str) -> dict | None:
    """Returns the video_analyses row dict, or None if not found."""
    result = supabase.rpc("pipeline_get_analysis", {
        "p_video_id": video_id,
        "p_secret": PIPELINE_SECRET,
    }).execute()
    return result.data[0] if result.data else None


def _rpc_get_quizzes(video_id: str) -> list[dict]:
    """Returns all quiz rows for a video."""
    result = supabase.rpc("pipeline_get_quizzes", {
        "p_video_id": video_id,
        "p_secret": PIPELINE_SECRET,
    }).execute()
    return result.data or []


def _rpc_save_kg_extraction(
    video_id: str,
    user_id: str,
    nodes: list[dict],
    edges: list[dict],
) -> None:
    """Save knowledge graph extraction (nodes + edges) for a video."""
    supabase.rpc("pipeline_save_kg_extraction", {
        "p_video_id": video_id,
        "p_user_id": user_id,
        "p_nodes": nodes,
        "p_edges": edges,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_reset_kg_video(video_id: str) -> None:
    """Clean up KG data for a video (decrement counts, remove orphans)."""
    supabase.rpc("pipeline_reset_kg_video", {
        "p_video_id": video_id,
        "p_secret": PIPELINE_SECRET,
    }).execute()


def _rpc_get_kg_graph(user_id: str) -> dict:
    """Returns the full knowledge graph for a user."""
    result = supabase.rpc("pipeline_get_kg_graph", {
        "p_user_id": user_id,
        "p_secret": PIPELINE_SECRET,
    }).execute()
    data = result.data[0] if result.data else {"nodes": [], "edges": []}
    return {
        "nodes": data.get("nodes") or [],
        "edges": data.get("edges") or [],
    }


# ---------------------------------------------------------------------------
# App factory with lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI starting — Supabase anon key + Vault-backed pipeline secret")
    yield
    logger.info("FastAPI shutting down")


app = FastAPI(title="yt-memory backend", version="0.1.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow Next.js dev server (and production domain when deployed)
# ---------------------------------------------------------------------------
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # Add your Vercel domain here when deploying:
    # "https://your-app.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class ProcessVideoRequest(BaseModel):
    video_id: UUID = Field(..., description="UUID of the videos row to process")


class ProcessVideoResponse(BaseModel):
    accepted: bool
    video_id: str


class SaveToMemoryRequest(BaseModel):
    video_id: UUID = Field(..., description="UUID of the video to embed and save")


class SaveToMemoryResponse(BaseModel):
    accepted: bool
    video_id: str


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="User's question")
    video_id: UUID | None = Field(None, description="Optional video ID for per-video chat")


class ChatSource(BaseModel):
    video_id: str
    title: str
    youtube_id: str
    start_time: float
    end_time: float
    content: str
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]


# ---------------------------------------------------------------------------
# Background pipeline — Step 2 (transcript + metadata) + Step 3 (LLM analysis)
# ---------------------------------------------------------------------------
async def _run_pipeline(video_id: str) -> None:
    """
    Main processing pipeline (fire-and-forget from /process-video).

    Step 2: fetch transcript + metadata
    Step 3: LLM analysis — 4 concurrent calls (summary, insights, quizzes, flashcards)
    Final:  status = 'completed'
    """
    try:
        logger.info(f"Pipeline started for video {video_id}")

        # Clear any partial data from a previous run so retries start clean
        _rpc_reset_video_data(video_id)

        _rpc_update_video(video_id, status="processing")
        logger.info(f"[{video_id}] status=processing")

        # -------------------------------------------------------------------
        # Step 2: Fetch transcript + metadata
        # -------------------------------------------------------------------
        row = _rpc_get_video(video_id)
        if row is None:
            raise ValueError(f"Video row {video_id} disappeared after status update")

        youtube_id: str = row["youtube_id"]

        # Save metadata first — title/thumbnail show on the card even if transcript fails
        metadata = await fetch_video_metadata(youtube_id)
        _rpc_update_video(
            video_id,
            status="processing",
            title=metadata["title"],
            thumbnail_url=metadata["thumbnail_url"],
        )
        logger.info(f"[{video_id}] Metadata saved: '{metadata['title']}'")

        snippets = await fetch_transcript(youtube_id)
        logger.info(f"[{video_id}] Step 2 complete — {len(snippets)} transcript snippets")

        # -------------------------------------------------------------------
        # Step 3: LLM analysis — 7 concurrent OpenRouter calls
        # -------------------------------------------------------------------
        logger.info(f"[{video_id}] Step 3 starting — LLM analysis")
        analysis = await analyze_transcript(snippets)

        # Guard: if summary is empty all 7 LLM calls likely failed (bad/missing API key).
        # Raise so the pipeline marks this video 'failed' instead of silently
        # completing with no content.
        if not analysis.get("summary_short") and not analysis.get("summary_detailed"):
            raise RuntimeError(
                "LLM analysis returned no content — all API calls failed. "
                "Verify OPENROUTER_API_KEY is set and the model is available on your plan."
            )

        logger.info(
            f"[{video_id}] Step 3 LLM calls complete — "
            f"summary={'yes' if analysis.get('summary_short') else 'EMPTY'}, "
            f"{len(analysis.get('key_insights', []))} insights, "
            f"{len(analysis.get('quizzes', []))} MCQs, "
            f"{len(analysis.get('flashcards', []))} flashcards, "
            f"{len(analysis.get('action_items', []))} action items, "
            f"{len(analysis.get('glossary', []))} glossary terms, "
            f"{len(analysis.get('misconceptions', []))} misconceptions"
        )

        # Persist summary + insights + new analysis fields
        _rpc_save_analysis(
            video_id,
            summary_short=analysis["summary_short"],
            summary_detailed=analysis["summary_detailed"],
            key_insights=analysis["key_insights"],
            action_items=analysis["action_items"],
            glossary=analysis["glossary"],
            misconceptions=analysis["misconceptions"],
        )

        # Persist quizzes + flashcards (combined into quizzes table)
        all_quiz_items = analysis["quizzes"] + analysis["flashcards"]
        if all_quiz_items:
            _rpc_save_quizzes(video_id, all_quiz_items)

        logger.info(
            f"[{video_id}] Step 3 complete — "
            f"{len(analysis['key_insights'])} insights, "
            f"{len(analysis['quizzes'])} MCQs, "
            f"{len(analysis['flashcards'])} flashcards, "
            f"{len(analysis['action_items'])} action items, "
            f"{len(analysis['glossary'])} glossary terms, "
            f"{len(analysis['misconceptions'])} misconceptions"
        )

        _rpc_update_video(video_id, status="completed")
        logger.info(f"[{video_id}] Pipeline complete — status=completed")

    except Exception as exc:
        logger.exception(f"Pipeline failed for video {video_id}: {exc}")
        _rpc_update_video(video_id, status="failed", error_message=str(exc))


# ---------------------------------------------------------------------------
# Background pipeline — Step 5 (semantic chunking + embeddings)
# ---------------------------------------------------------------------------
async def _run_save_to_memory(video_id: str, user_id: str) -> None:
    """
    Save-to-memory pipeline (fire-and-forget from /save-to-memory).

    1. Re-fetch the YouTube transcript (no need to store raw snippets separately)
    2. Semantically chunk the transcript
    3. Generate embeddings for all chunks (batched, parallel)
    4. Store each chunk + embedding in the chunks table via RPC
    5. Update video status to 'saved'
    """
    try:
        logger.info(f"[{video_id}] Save-to-memory pipeline started for user {user_id}")

        row = _rpc_get_video(video_id)
        if row is None:
            raise ValueError(f"Video row {video_id} not found")

        youtube_id: str = row["youtube_id"]

        # Delete only existing chunks before re-embedding (idempotent).
        # We use pipeline_reset_chunks_only — NOT pipeline_reset_video_data —
        # to preserve video_analyses and quizzes which were set by the LLM pipeline.
        supabase.rpc("pipeline_reset_chunks_only", {
            "p_video_id": video_id,
            "p_secret": PIPELINE_SECRET,
        }).execute()

        # Re-fetch transcript (avoids storing raw snippets in a separate DB table)
        snippets = await fetch_transcript(youtube_id)
        logger.info(f"[{video_id}] Re-fetched {len(snippets)} snippets for embedding")

        # Chunk the transcript
        chunks = chunk_transcript(snippets)

        # Fetch analysis + quizzes and create additional embeddable chunks
        analysis = _rpc_get_analysis(video_id)
        quizzes = _rpc_get_quizzes(video_id)
        video_title = row.get("title") or youtube_id
        analysis_chunks = format_analysis_chunks(analysis, quizzes, video_title)
        transcript_count = len(chunks)
        chunks.extend(analysis_chunks)

        if not chunks:
            raise ValueError("No chunks generated — transcript may be empty")

        logger.info(
            f"[{video_id}] {len(chunks)} total chunks "
            f"({transcript_count} transcript + {len(analysis_chunks)} analysis)"
        )

        # Generate all embeddings in parallel batches
        texts = [c["content"] for c in chunks]
        embeddings = await generate_embeddings_batch(texts, batch_size=5)

        # Store each chunk + embedding via RPC
        for chunk, embedding in zip(chunks, embeddings):
            _rpc_save_chunk(
                video_id=video_id,
                content=chunk["content"],
                start_time=chunk["start_time"],
                end_time=chunk["end_time"],
                embedding=embedding,
            )

        logger.info(f"[{video_id}] Stored {len(chunks)} chunks with embeddings")

        # -------------------------------------------------------------------
        # Knowledge Graph extraction (non-blocking — failure does not prevent save)
        # -------------------------------------------------------------------
        try:
            transcript_text = flatten_transcript(snippets)
            analysis_context = {
                "glossary": (analysis or {}).get("glossary") or [],
                "key_insights": (analysis or {}).get("key_insights") or [],
            }
            kg_result = await analyze_knowledge_graph(transcript_text, analysis_context)
            kg_entities = kg_result.get("entities", [])
            kg_relationships = kg_result.get("relationships", [])

            if kg_entities:
                _rpc_reset_kg_video(video_id)
                _rpc_save_kg_extraction(video_id, user_id, kg_entities, kg_relationships)
                logger.info(
                    f"[{video_id}] KG saved — {len(kg_entities)} entities, "
                    f"{len(kg_relationships)} relationships"
                )
            else:
                logger.warning(f"[{video_id}] KG extraction returned no entities")
        except Exception as kg_exc:
            logger.error(f"[{video_id}] KG extraction failed (non-blocking): {kg_exc}")

        _rpc_update_video(video_id, status="saved")
        logger.info(f"[{video_id}] Save-to-memory complete — status=saved")

    except Exception as exc:
        logger.exception(f"Save-to-memory failed for video {video_id}: {exc}")
        _rpc_update_video(video_id, status="failed", error_message=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "yt-memory-backend"}


@app.get("/debug/transcript/{youtube_id}")
async def debug_transcript(youtube_id: str) -> dict:
    """Dev-only endpoint to verify transcript content for a given YouTube ID."""
    snippets = await fetch_transcript(youtube_id)
    total_words = sum(len(s["text"].split()) for s in snippets)
    return {
        "youtube_id": youtube_id,
        "total_snippets": len(snippets),
        "total_words": total_words,
        "first_5": snippets[:5],
        "last_5": snippets[-5:],
    }


@app.post("/process-video", status_code=202)
async def process_video(body: ProcessVideoRequest, user: dict = Depends(get_current_user)) -> ProcessVideoResponse:
    video_id = str(body.video_id)

    row = _rpc_get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
    if row.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="You do not own this video")

    asyncio.create_task(_run_pipeline(video_id))

    return ProcessVideoResponse(accepted=True, video_id=video_id)


# ---------------------------------------------------------------------------
# Synchronous RAG chat pipeline
# ---------------------------------------------------------------------------
async def _run_chat(query: str, user_id: str, video_id: str | None = None) -> dict:
    """
    RAG pipeline (synchronous — caller awaits the result):
    1. Embed the query
    2. match_chunks RPC (vector similarity search)
    3. LLM grounded answer from top-k chunks
    4. Return answer + sources
    """
    logger.info(f"Chat query from user {user_id}: {query[:80]}...")

    # 1. Embed the query
    query_embedding = await generate_embedding(query)

    # 2. Call match_chunks RPC
    rpc_params: dict = {
        "query_embedding": query_embedding,
        "match_count": 8,
        "p_user_id": user_id,
        "p_secret": PIPELINE_SECRET,
    }
    if video_id:
        rpc_params["p_video_id"] = video_id

    SIMILARITY_THRESHOLD = 0.4

    result = supabase.rpc("match_chunks", rpc_params).execute()
    chunks = [c for c in (result.data or []) if c.get("similarity", 0) >= SIMILARITY_THRESHOLD]
    logger.info(f"match_chunks returned {len(result.data or [])} results, {len(chunks)} above threshold")

    # 3. No relevant chunks → canned response
    if not chunks:
        return {
            "answer": "I don't have enough information from your saved videos to answer this question. "
                      "Try saving more videos to memory first.",
            "sources": [],
        }

    # 4. Generate grounded answer
    answer = await generate_chat_answer(query, chunks)

    # 5. Format sources
    sources = [
        {
            "video_id": c["video_id"],
            "title": c.get("video_title") or "Untitled",
            "youtube_id": c.get("youtube_id") or "",
            "start_time": c.get("start_time") or 0,
            "end_time": c.get("end_time") or 0,
            "content": c["content"],
            "similarity": c.get("similarity") or 0,
        }
        for c in chunks
    ]

    return {"answer": answer, "sources": sources}


@app.post("/chat")
async def chat(body: ChatRequest, user: dict = Depends(get_current_user)) -> ChatResponse:
    """Synchronous RAG chat — embeds query, retrieves chunks, generates answer."""
    query = body.query.strip()
    user_id = user["id"]  # From validated JWT, not body
    video_id = str(body.video_id) if body.video_id else None

    try:
        result = await _run_chat(query, user_id, video_id)
        return ChatResponse(**result)
    except Exception as exc:
        logger.exception(f"Chat failed: {exc}")
        raise HTTPException(status_code=500, detail="Chat request failed. Please try again.")


@app.get("/knowledge-graph")
async def knowledge_graph(user: dict = Depends(get_current_user)) -> dict:
    """Returns the full knowledge graph for the authenticated user."""
    try:
        return _rpc_get_kg_graph(user["id"])
    except Exception as exc:
        logger.exception(f"Knowledge graph fetch failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch knowledge graph.")


@app.post("/save-to-memory", status_code=202)
async def save_to_memory(body: SaveToMemoryRequest, user: dict = Depends(get_current_user)) -> SaveToMemoryResponse:
    video_id = str(body.video_id)
    user_id = user["id"]  # From validated JWT, not body

    row = _rpc_get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
    if row.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You do not own this video")

    if row.get("status") not in ("completed", "saved"):
        raise HTTPException(
            status_code=400,
            detail="Video must have finished processing (status: completed or saved) before saving to memory",
        )

    asyncio.create_task(_run_save_to_memory(video_id, user_id))

    return SaveToMemoryResponse(accepted=True, video_id=video_id)
