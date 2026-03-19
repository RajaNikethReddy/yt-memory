import asyncio
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

load_dotenv()  # reads backend/.env when running locally outside Docker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase client — module-level singleton, created once on startup
# Uses service_role key to bypass RLS for status updates and pipeline writes
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ---------------------------------------------------------------------------
# App factory with lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI starting — Supabase client ready")
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


# ---------------------------------------------------------------------------
# Background pipeline — expands in each subsequent Phase 2 step
# ---------------------------------------------------------------------------
async def _run_pipeline(video_id: str) -> None:
    """
    Pipeline stub. Currently only updates status to 'processing'.
    Each Phase 2 step will add a stage here:
      Step 2: fetch YouTube transcript
      Step 3: semantic chunking
      Step 4: concept & entity extraction (LLM)
      Step 5: embeddings generation + store in chunks/concepts tables
      Final:  update status to 'completed'
    """
    try:
        logger.info(f"Pipeline started for video {video_id}")

        supabase.table("videos").update({"status": "processing"}).eq("id", video_id).execute()
        logger.info(f"Video {video_id} → status=processing")

        # TODO Phase 2 Step 2: fetch transcript (YouTube API + Whisper fallback)
        # TODO Phase 2 Step 3: semantic chunking (~500–1000 tokens per chunk)
        # TODO Phase 2 Step 4: concept & entity extraction via LLM
        # TODO Phase 2 Step 5: generate embeddings + store in chunks/concepts tables
        # TODO: supabase.table("videos").update({"status": "completed"}).eq("id", video_id).execute()

    except Exception as exc:
        logger.exception(f"Pipeline failed for video {video_id}: {exc}")
        supabase.table("videos").update({
            "status": "failed",
            "error_message": str(exc),
        }).eq("id", video_id).execute()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "yt-memory-backend"}


@app.post("/process-video", status_code=202)
async def process_video(body: ProcessVideoRequest) -> ProcessVideoResponse:
    video_id = str(body.video_id)

    # Verify the row exists before accepting the job
    check = (
        supabase
        .table("videos")
        .select("id, status")
        .eq("id", video_id)
        .maybe_single()
        .execute()
    )

    if check.data is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")

    # Fire-and-forget: schedule pipeline without blocking the HTTP response
    asyncio.create_task(_run_pipeline(video_id))

    return ProcessVideoResponse(accepted=True, video_id=video_id)
