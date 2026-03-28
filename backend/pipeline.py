"""
pipeline.py — Background processing pipeline for yt-memory.

Each Phase 2 step lives here as a standalone async function so that
main.py stays clean and each step can be tested independently.

Implemented steps:
  Step 2: fetch_transcript / fetch_video_metadata        ← done
  Step 3: LLM analysis (summary, insights, quizzes, flashcards) ← done
  Step 5: semantic chunking + embedding generation       ← done
"""

import asyncio
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
from openai import AsyncOpenAI
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    NoTranscriptFound,
    TranscriptsDisabled,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenRouter client (OpenAI-compatible API, supports both LLM + embeddings)
# ---------------------------------------------------------------------------
_openrouter = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    default_headers={
        "HTTP-Referer": "https://yt-memory.app",
        "X-Title": "yt-memory",
    },
)

LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-3-flash-preview")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "qwen/qwen3-embedding-8b")

# Max transcript chars sent to LLM — Gemini has a huge context window but
# we cap here to keep latency predictable and avoid runaway cost.
TRANSCRIPT_MAX_CHARS = 100_000

# Target characters per semantic chunk (~500 tokens ≈ 2,000 chars)
CHUNK_TARGET_CHARS = 2_000

# Thread pool for blocking I/O (youtube-transcript-api is synchronous)
_executor = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# Step 2 — Transcript + Metadata
# ---------------------------------------------------------------------------

def _sync_fetch_transcript(youtube_id: str) -> list[dict]:
    """
    Synchronous transcript fetch — must run inside a thread pool.
    Returns a list of {"text", "start", "duration"} dicts.
    Uses the instance API (youtube-transcript-api >= 0.6.3).
    """
    api = YouTubeTranscriptApi()
    transcript = api.fetch(youtube_id)
    return [
        {"text": s.text, "start": s.start, "duration": s.duration}
        for s in transcript
    ]


async def fetch_transcript(youtube_id: str) -> list[dict]:
    """
    Async wrapper around the synchronous youtube-transcript-api fetch.
    Returns list of {"text", "start", "duration"} dicts.
    """
    loop = asyncio.get_event_loop()
    try:
        snippets = await loop.run_in_executor(
            _executor, _sync_fetch_transcript, youtube_id
        )
    except TranscriptsDisabled:
        raise RuntimeError(
            f"Transcripts are disabled for video {youtube_id}. "
            "Whisper fallback not yet implemented."
        )
    except NoTranscriptFound:
        raise RuntimeError(
            f"No transcript found for video {youtube_id}. "
            "Whisper fallback not yet implemented."
        )

    logger.info(f"[{youtube_id}] Fetched {len(snippets)} transcript snippets")
    return snippets


async def fetch_video_metadata(youtube_id: str) -> dict:
    """
    Fetch video title and thumbnail via YouTube oEmbed endpoint.
    No API key required — plain HTTP GET.
    Returns {"title": str, "thumbnail_url": str}.
    """
    oembed_url = (
        "https://www.youtube.com/oembed"
        f"?url=https://www.youtube.com/watch%3Fv%3D{youtube_id}"
        "&format=json"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(oembed_url)
        resp.raise_for_status()
        data = resp.json()

    metadata = {
        "title": data.get("title", ""),
        "thumbnail_url": data.get("thumbnail_url", ""),
    }
    logger.info(f"[{youtube_id}] Metadata fetched: title='{metadata['title']}'")
    return metadata


# ---------------------------------------------------------------------------
# Step 3 — LLM Analysis helpers
# ---------------------------------------------------------------------------

def flatten_transcript(snippets: list[dict]) -> str:
    """
    Join transcript snippet texts into a single string.
    Truncates at TRANSCRIPT_MAX_CHARS to stay within LLM context limits.
    """
    text = " ".join(s["text"] for s in snippets)
    if len(text) > TRANSCRIPT_MAX_CHARS:
        text = text[:TRANSCRIPT_MAX_CHARS]
        logger.warning(f"Transcript truncated to {TRANSCRIPT_MAX_CHARS} chars for LLM")
    return text


def extract_json_from_llm(text: str) -> dict:
    """
    Robustly parse JSON from an LLM response.

    Handles three common output patterns:
      1. Plain JSON text
      2. ```json\\n...\\n``` code fences (with language tag)
      3. ```\\n...\\n``` code fences (without language tag)

    If parsing still fails, attempts to extract the first {...} block.
    """
    text = text.strip()

    # Strip code fences if present
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text, re.DOTALL)
    json_str = m.group(1).strip() if m else text

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Last resort: find first { … last }
        s, e = json_str.find("{"), json_str.rfind("}")
        if s != -1 and e != -1:
            return json.loads(json_str[s : e + 1])
        raise ValueError(
            f"Failed to parse JSON from LLM response.\n"
            f"Raw (first 500 chars): {text[:500]}"
        )


async def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """
    Single LLM call via OpenRouter. Returns the raw text of the first choice.
    """
    response = await _openrouter.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Step 3 — Four segregated LLM calls (run concurrently via asyncio.gather)
# ---------------------------------------------------------------------------

async def analyze_summary(transcript_text: str) -> dict:
    """
    Call 1/4 — Generate a short TL;DR and a detailed summary.

    Returns:
      {
        "summary_short": "2-3 sentence TL;DR",
        "summary_detailed": "200-400 word comprehensive summary"
      }
    """
    system = (
        "You are an expert at summarizing educational and technical YouTube videos. "
        "Your summaries are clear, accurate, and focused on the most valuable information. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and produce a JSON summary.

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "summary_short": "A concise 2-3 sentence TL;DR that captures the core message and main takeaway of the video.",
  "summary_detailed": "A comprehensive 200-400 word summary covering: the main topic, key concepts explained, important arguments or demonstrations, and the conclusion. Write in clear prose paragraphs."
}}"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)
    logger.info("LLM summary complete")
    return result


async def analyze_insights(transcript_text: str) -> dict:
    """
    Call 2/4 — Extract 5-10 key actionable insights.

    Returns:
      {
        "key_insights": ["insight 1", "insight 2", ...]
      }
    """
    system = (
        "You are an expert at extracting actionable insights and important takeaways from educational content. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and extract the most important insights.

Requirements for each insight:
- Phrase it as a concrete, standalone statement a learner can act on or remember
- Do NOT write vague observations like "This video covers X" — say what was actually learned about X
- Order from most to least important
- Generate 5 to 10 insights total

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "key_insights": [
    "insight 1",
    "insight 2"
  ]
}}"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)
    logger.info(f"LLM insights complete ({len(result.get('key_insights', []))} insights)")
    return result


async def analyze_quizzes(transcript_text: str) -> dict:
    """
    Call 3/4 — Generate 3-5 multiple-choice quiz questions.

    Returns:
      {
        "quizzes": [
          {
            "type": "mcq",
            "question": "...",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "explanation": "..."
          }
        ]
      }
    """
    system = (
        "You are an expert educator who creates high-quality multiple-choice questions to test comprehension. "
        "Questions should test genuine understanding, not just memory of specific words. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and create multiple-choice quiz questions.

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "quizzes": [
    {{
      "type": "mcq",
      "question": "A clear question that tests understanding of a key concept from the video.",
      "options": [
        "The correct answer",
        "A plausible but incorrect distractor",
        "Another plausible but incorrect distractor",
        "Another plausible but incorrect distractor"
      ],
      "answer": "The correct answer",
      "explanation": "A brief explanation of why this answer is correct and why the others are wrong."
    }}
  ]
}}

Requirements:
- Generate 3 to 5 questions
- Each question must have exactly 4 options
- The "answer" field must exactly match one of the strings in "options"
- Questions should cover different parts of the video
- Shuffle the position of the correct answer across questions"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)
    # Ensure all quiz items have type="mcq"
    for q in result.get("quizzes", []):
        q["type"] = "mcq"
    logger.info(f"LLM quizzes complete ({len(result.get('quizzes', []))} questions)")
    return result


async def analyze_flashcards(transcript_text: str) -> dict:
    """
    Call 4/4 — Generate 5-8 flashcards for key terms and concepts.

    The LLM prompt uses "front"/"back" terminology (natural for flashcards),
    but we remap to "question"/"answer" before returning so the data matches
    the quizzes table schema (which uses question/answer for both MCQ and flashcard).

    Returns:
      {
        "flashcards": [
          {"type": "flashcard", "question": "term (front)", "answer": "definition (back)"}
        ]
      }
    """
    system = (
        "You are an expert at creating effective flashcards for spaced repetition learning. "
        "Good flashcards have a specific, testable front and a concise, complete back. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and create flashcards for key concepts.

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "flashcards": [
    {{
      "type": "flashcard",
      "front": "A specific term, concept, or question from the video",
      "back": "The clear definition, explanation, or answer — concise but complete (1-3 sentences)"
    }}
  ]
}}

Requirements:
- Generate 5 to 8 flashcards
- Front: a single term, concept name, or short question
- Back: a concise but complete explanation (not just one word)
- Cover the most important vocabulary and concepts introduced in the video
- Avoid trivial facts; focus on things a learner would actually want to memorize"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)

    # Remap front→question and back→answer to match the quizzes table schema
    # (both MCQ and flashcard rows use "question" and "answer" columns).
    # The NOT NULL constraints on those columns mean we MUST map correctly.
    remapped = []
    for fc in result.get("flashcards", []):
        remapped.append({
            "type": "flashcard",
            "question": fc.get("front") or fc.get("question") or "",
            "answer":   fc.get("back")  or fc.get("answer")  or "",
        })
    result["flashcards"] = remapped
    logger.info(f"LLM flashcards complete ({len(remapped)} cards)")
    return result


async def analyze_action_items(transcript_text: str) -> dict:
    """
    Call 5/7 — Extract 3-7 concrete next steps the viewer should take.

    Returns:
      {
        "action_items": [
          "Try Google Stitch at stitch.withgoogle.com with a real project prompt",
          ...
        ]
      }
    """
    system = (
        "You are an expert at turning educational video content into concrete, actionable plans. "
        "You focus on specific, immediately executable tasks rather than vague goals. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and extract concrete action items — \
specific things the viewer should do after watching.

Requirements for each action item:
- Start with an imperative verb (Try, Build, Set up, Download, Read, Practice, etc.)
- Be specific enough that the viewer knows exactly what to do — NOT vague like "Learn more about X"
- Should be achievable within hours or days, not months
- Directly based on what was demonstrated or recommended in the video
- Generate 3 to 7 action items, ordered by priority

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "action_items": [
    "action item 1",
    "action item 2"
  ]
}}"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)
    logger.info(f"LLM action items complete ({len(result.get('action_items', []))} items)")
    return result


async def analyze_glossary(transcript_text: str) -> dict:
    """
    Call 6/7 — Build a reference glossary of key terms introduced in the video.

    Returns:
      {
        "glossary": [
          {"term": "Google Stitch", "definition": "A Google AI tool that generates ..."}
        ]
      }
    """
    system = (
        "You are an expert technical writer who creates precise, accessible glossaries. "
        "You only define terms that are actually introduced or explained in the source material. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and build a glossary of key terms.

Requirements for each entry:
- Only include terms that are actually defined, demonstrated, or meaningfully used in the video
- Definition: 1-2 clear sentences — precise enough to be useful, accessible to someone new to the topic
- Match the technical level of the video (don't over-simplify or over-complicate)
- Prioritize domain-specific vocabulary, tools, frameworks, concepts, and acronyms
- Generate 5 to 10 terms

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "glossary": [
    {{
      "term": "term name",
      "definition": "clear, precise 1-2 sentence definition"
    }}
  ]
}}"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)
    logger.info(f"LLM glossary complete ({len(result.get('glossary', []))} terms)")
    return result


async def analyze_misconceptions(transcript_text: str) -> dict:
    """
    Call 7/7 — Identify common misconceptions that the video addresses or corrects.

    Returns:
      {
        "misconceptions": [
          {
            "misconception": "You need to write full HTML/CSS before testing UI ideas",
            "reality": "Google Stitch can generate production-ready UI directly from a text prompt"
          }
        ]
      }
    """
    system = (
        "You are an expert at identifying the gap between what people commonly believe and what experts know. "
        "You only surface misconceptions that the video itself directly addresses or implicitly corrects. "
        "You MUST respond with valid JSON only — no extra text, no markdown, no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and identify common misconceptions \
that the video corrects, challenges, or implicitly contradicts.

Requirements:
- The misconception must be something people plausibly believe BEFORE watching the video
- The reality must be directly supported by what is said or shown in the video — no speculation
- Phrase the misconception as a belief someone would actually hold ("X requires Y", "You can't Z without W")
- Phrase the reality as the corrected truth revealed by the video
- Only include genuine misconceptions — if the video doesn't challenge any false beliefs, return fewer or none
- Generate 2 to 5 misconceptions

TRANSCRIPT:
{transcript_text}

Respond with ONLY this JSON structure (no other text):
{{
  "misconceptions": [
    {{
      "misconception": "the false belief people commonly hold",
      "reality": "what the video reveals to be true"
    }}
  ]
}}"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)
    logger.info(f"LLM misconceptions complete ({len(result.get('misconceptions', []))} items)")
    return result


async def analyze_transcript(snippets: list[dict]) -> dict:
    """
    Step 3 entry point — run all 7 LLM calls concurrently.

    Returns a merged dict:
      {
        "summary_short": str,
        "summary_detailed": str,
        "key_insights": list[str],
        "quizzes": list[dict],
        "flashcards": list[dict],
        "action_items": list[str],
        "glossary": list[{"term", "definition"}],
        "misconceptions": list[{"misconception", "reality"}],
      }

    Each call is wrapped in _safe so a partial failure logs the error and
    returns an empty default without aborting the rest of the pipeline.
    """
    transcript_text = flatten_transcript(snippets)

    async def _safe(coro, label: str, fallback):
        try:
            return await coro
        except Exception as exc:
            logger.error(f"LLM call '{label}' failed: {exc}")
            return fallback

    (
        summary_res,
        insights_res,
        quizzes_res,
        flashcards_res,
        action_items_res,
        glossary_res,
        misconceptions_res,
    ) = await asyncio.gather(
        _safe(analyze_summary(transcript_text),       "summary",       {"summary_short": "", "summary_detailed": ""}),
        _safe(analyze_insights(transcript_text),      "insights",      {"key_insights": []}),
        _safe(analyze_quizzes(transcript_text),       "quizzes",       {"quizzes": []}),
        _safe(analyze_flashcards(transcript_text),    "flashcards",    {"flashcards": []}),
        _safe(analyze_action_items(transcript_text),  "action_items",  {"action_items": []}),
        _safe(analyze_glossary(transcript_text),      "glossary",      {"glossary": []}),
        _safe(analyze_misconceptions(transcript_text),"misconceptions",{"misconceptions": []}),
    )

    return {
        "summary_short":    summary_res.get("summary_short", ""),
        "summary_detailed": summary_res.get("summary_detailed", ""),
        "key_insights":     insights_res.get("key_insights", []),
        "quizzes":          quizzes_res.get("quizzes", []),
        "flashcards":       flashcards_res.get("flashcards", []),
        "action_items":     action_items_res.get("action_items", []),
        "glossary":         glossary_res.get("glossary", []),
        "misconceptions":   misconceptions_res.get("misconceptions", []),
    }


# ---------------------------------------------------------------------------
# Step 5 — Semantic Chunking + Embedding Generation
# ---------------------------------------------------------------------------

def chunk_transcript(snippets: list[dict], target_chars: int = CHUNK_TARGET_CHARS) -> list[dict]:
    """
    Group consecutive transcript snippets into semantic chunks of ~target_chars each.

    Each chunk preserves the start/end timestamp of the snippets it contains.
    Returns list of {"content": str, "start_time": float, "end_time": float}.
    """
    chunks = []
    current_texts: list[str] = []
    current_chars = 0
    chunk_start = snippets[0]["start"] if snippets else 0.0
    last_end = 0.0

    for snippet in snippets:
        text = snippet["text"].strip()
        snippet_end = snippet["start"] + snippet["duration"]

        if current_chars + len(text) > target_chars and current_texts:
            # Flush current chunk
            chunks.append({
                "content":    " ".join(current_texts),
                "start_time": chunk_start,
                "end_time":   last_end,
            })
            current_texts = []
            current_chars = 0
            chunk_start = snippet["start"]

        current_texts.append(text)
        current_chars += len(text) + 1  # +1 for the space
        last_end = snippet_end

    # Flush remaining text
    if current_texts:
        chunks.append({
            "content":    " ".join(current_texts),
            "start_time": chunk_start,
            "end_time":   last_end,
        })

    logger.info(f"Chunked transcript into {len(chunks)} chunks (target {target_chars} chars each)")
    return chunks


def format_analysis_chunks(
    analysis: dict | None,
    quizzes: list[dict],
    video_title: str,
) -> list[dict]:
    """Format analysis data into text chunks for embedding alongside transcript chunks.

    Each chunk groups related analysis (summary, insights, glossary, etc.) so that
    vector search can find the right knowledge when a user asks about it.
    Returns list of {"content": str, "start_time": 0.0, "end_time": 0.0}.
    """
    chunks: list[dict] = []

    if not analysis:
        return chunks

    # 1. Summary chunk
    parts: list[str] = []
    if analysis.get("summary_short"):
        parts.append(f"Brief Summary: {analysis['summary_short']}")
    if analysis.get("summary_detailed"):
        parts.append(f"Detailed Summary: {analysis['summary_detailed']}")
    if parts:
        chunks.append({
            "content": f'Video: "{video_title}"\n\n' + "\n\n".join(parts),
            "start_time": 0.0,
            "end_time": 0.0,
        })

    # 2. Key Insights chunk
    insights = analysis.get("key_insights") or []
    if insights:
        numbered = "\n".join(f"{i+1}. {ins}" for i, ins in enumerate(insights))
        chunks.append({
            "content": f'Key Insights from "{video_title}":\n\n{numbered}',
            "start_time": 0.0,
            "end_time": 0.0,
        })

    # 3. Action Items chunk
    items = analysis.get("action_items") or []
    if items:
        bullet = "\n".join(f"- {item}" for item in items)
        chunks.append({
            "content": f'Action Items from "{video_title}":\n\n{bullet}',
            "start_time": 0.0,
            "end_time": 0.0,
        })

    # 4. Glossary chunk
    glossary = analysis.get("glossary") or []
    if glossary:
        entries = "\n".join(f"- {g['term']}: {g['definition']}" for g in glossary)
        chunks.append({
            "content": f'Glossary from "{video_title}":\n\n{entries}',
            "start_time": 0.0,
            "end_time": 0.0,
        })

    # 5. Misconceptions chunk
    misconceptions = analysis.get("misconceptions") or []
    if misconceptions:
        entries = "\n".join(
            f"- Misconception: {m['misconception']}\n  Reality: {m['reality']}"
            for m in misconceptions
        )
        chunks.append({
            "content": f'Common Misconceptions from "{video_title}":\n\n{entries}',
            "start_time": 0.0,
            "end_time": 0.0,
        })

    # 6. Quiz & Flashcard chunks
    mcqs = [q for q in quizzes if q.get("type") == "mcq"]
    flashcards = [q for q in quizzes if q.get("type") == "flashcard"]

    if mcqs:
        qa_text = "\n\n".join(
            f"Q: {q['question']}\nA: {q['answer']}"
            + (f"\nExplanation: {q['explanation']}" if q.get("explanation") else "")
            for q in mcqs
        )
        chunks.append({
            "content": f'Quiz Questions from "{video_title}":\n\n{qa_text}',
            "start_time": 0.0,
            "end_time": 0.0,
        })

    if flashcards:
        fc_text = "\n\n".join(
            f"Q: {q['question']}\nA: {q['answer']}" for q in flashcards
        )
        chunks.append({
            "content": f'Flashcards from "{video_title}":\n\n{fc_text}',
            "start_time": 0.0,
            "end_time": 0.0,
        })

    logger.info(f"Formatted {len(chunks)} analysis chunks for embedding")
    return chunks


async def generate_embedding(text: str) -> list[float]:
    """
    Generate a single embedding vector via OpenRouter.
    Returns a list of floats (4096-dim for qwen3-embedding-8b).
    """
    response = await _openrouter.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        encoding_format="float",
    )
    return response.data[0].embedding


async def generate_embeddings_batch(texts: list[str], batch_size: int = 5) -> list[list[float]]:
    """
    Generate embeddings for a list of texts, processing in parallel batches
    to balance throughput against rate limits.
    """
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_results = await asyncio.gather(*[generate_embedding(t) for t in batch])
        embeddings.extend(batch_results)
        logger.info(f"Embeddings: {len(embeddings)}/{len(texts)} done")
    return embeddings


# ---------------------------------------------------------------------------
# Phase 3 — RAG Chat (grounded answer generation)
# ---------------------------------------------------------------------------

def _format_timestamp(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS format."""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Knowledge Graph — Entity / Relationship Extraction
# ---------------------------------------------------------------------------

# Common aliases for entity name normalisation (keeps the graph clean)
_ENTITY_ALIASES: dict[str, str] = {
    "ml": "Machine Learning",
    "ai": "Artificial Intelligence",
    "dl": "Deep Learning",
    "nlp": "Natural Language Processing",
    "llm": "Large Language Model",
    "llms": "Large Language Model",
    "gpt": "GPT",
    "js": "JavaScript",
    "ts": "TypeScript",
    "py": "Python",
    "react.js": "React",
    "reactjs": "React",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "k8s": "Kubernetes",
    "postgres": "PostgreSQL",
    "css3": "CSS",
    "html5": "HTML",
    "api": "API",
    "apis": "API",
    "rest api": "REST API",
    "graphql api": "GraphQL",
    "sql": "SQL",
    "nosql": "NoSQL",
    "aws": "AWS",
    "gcp": "Google Cloud Platform",
    "azure": "Microsoft Azure",
}


def normalize_entity_name(name: str) -> str:
    """Normalise an entity name for consistent dedup across videos."""
    name = name.strip()
    lower = name.lower()
    if lower in _ENTITY_ALIASES:
        return _ENTITY_ALIASES[lower]
    # If the name is entirely lowercase, title-case it for display
    if name == lower and not any(c.isdigit() for c in name):
        return name.title()
    return name


async def analyze_knowledge_graph(transcript_text: str, analysis_context: dict) -> dict:
    """
    Extract entities and relationships from a video transcript for the knowledge graph.

    Uses existing glossary terms and key insights as hints for consistent naming.
    Returns {"entities": [...], "relationships": [...]}.
    """
    # Build context hints from existing analysis
    glossary_terms = [g.get("term", "") for g in (analysis_context.get("glossary") or [])]
    key_insights = analysis_context.get("key_insights") or []

    hints_section = ""
    if glossary_terms:
        hints_section += f"KEY TERMS ALREADY IDENTIFIED (use these exact names when referring to the same concepts):\n{', '.join(glossary_terms)}\n\n"
    if key_insights:
        hints_section += f"KEY INSIGHTS ALREADY EXTRACTED:\n" + "\n".join(f"- {i}" for i in key_insights[:10]) + "\n\n"

    system = (
        "You are an expert knowledge engineer who extracts entities and relationships "
        "from educational video content to build a knowledge graph. You identify "
        "concepts, people, technologies, and practices, and how they relate to each "
        "other. You MUST respond with valid JSON only — no extra text, no markdown, "
        "no explanation outside the JSON. "
        "If you use a code block, wrap the JSON inside ```json ... ```."
    )

    user = f"""Analyze the following YouTube video transcript and extract entities and relationships for a knowledge graph.

{hints_section}TRANSCRIPT:
{transcript_text}

Extract:
1. ENTITIES — the most important concepts discussed. For each:
   - name: canonical name (Title Case, e.g. "Machine Learning" not "machine learning" or "ML")
   - type: one of "topic", "person", "technology", "practice"
   - description: 1-2 sentence explanation of what this is in context of this video
   - relevance: 0.0-1.0 how central this entity is to the video (1.0 = main subject)
   - context: a brief phrase about how this video discusses it

2. RELATIONSHIPS between entities:
   - source: entity name (must exactly match an entity name from your list)
   - target: entity name (must exactly match an entity name from your list)
   - relationship: one of "relates_to", "is_part_of", "uses", "contrasts_with", "builds_on"

Rules:
- Extract 8 to 15 entities per video
- Extract 5 to 12 relationships
- Only include entities genuinely discussed, not just briefly mentioned in passing
- Use consistent Title Case naming (e.g. "Python" not "python language")
- People should use full names when known (e.g. "Andrej Karpathy" not "Karpathy")
- "topic": abstract subjects/fields (e.g. "Machine Learning", "Data Structures")
- "person": named individuals
- "technology": specific tools, frameworks, languages, platforms
- "practice": methodologies, techniques, workflows (e.g. "Test-Driven Development")

Respond with ONLY this JSON:
{{
  "entities": [
    {{"name": "...", "type": "...", "description": "...", "relevance": 0.9, "context": "..."}}
  ],
  "relationships": [
    {{"source": "...", "target": "...", "relationship": "..."}}
  ]
}}"""

    raw = await _call_llm(system, user)
    result = extract_json_from_llm(raw)

    # Post-process: normalise entity names
    entities = result.get("entities", [])
    for entity in entities:
        entity["name"] = normalize_entity_name(entity.get("name", ""))

    # Also normalise relationship source/target names
    relationships = result.get("relationships", [])
    for rel in relationships:
        rel["source"] = normalize_entity_name(rel.get("source", ""))
        rel["target"] = normalize_entity_name(rel.get("target", ""))

    # Filter out any entities with empty names or invalid types
    valid_types = {"topic", "person", "technology", "practice"}
    entities = [e for e in entities if e.get("name") and e.get("type") in valid_types]

    # Deduplicate entities by name_lower (keep first occurrence)
    seen_names: set[str] = set()
    deduped_entities: list[dict] = []
    for e in entities:
        key = e["name"].lower()
        if key not in seen_names:
            seen_names.add(key)
            deduped_entities.append(e)
    entities = deduped_entities

    # Ensure all optional fields have sensible defaults
    for e in entities:
        e.setdefault("description", "")
        e.setdefault("relevance", 1.0)
        e.setdefault("context", "")

    # Filter relationships: must reference extracted entities, no self-loops,
    # and relationship type must be in the allowed set
    entity_names_lower = {e["name"].lower() for e in entities}
    valid_relationships = {"relates_to", "is_part_of", "uses", "contrasts_with", "builds_on"}
    relationships = [
        r for r in relationships
        if r.get("source", "").lower() in entity_names_lower
        and r.get("target", "").lower() in entity_names_lower
        and r.get("source", "").lower() != r.get("target", "").lower()
        and r.get("relationship") in valid_relationships
    ]

    logger.info(
        f"KG extraction complete: {len(entities)} entities, {len(relationships)} relationships"
    )
    return {"entities": entities, "relationships": relationships}


async def generate_chat_answer(query: str, context_chunks: list[dict]) -> str:
    """
    Generate a grounded answer to a user query based on retrieved transcript chunks.

    Each chunk dict must have: content, video_title, start_time, end_time.
    Returns the LLM answer text with [Source N] citations.
    """
    # Build numbered source context
    sources_text = []
    for i, chunk in enumerate(context_chunks, 1):
        start = _format_timestamp(chunk["start_time"] or 0)
        end = _format_timestamp(chunk["end_time"] or 0)
        title = chunk.get("video_title") or "Untitled"
        sources_text.append(
            f'[Source {i}] Video: "{title}" ({start}–{end})\n{chunk["content"]}'
        )

    context = "\n\n".join(sources_text)

    system = (
        "You are a knowledgeable assistant that answers questions based ONLY on the "
        "provided video transcript excerpts.\n\n"
        "Rules:\n"
        "- Answer based exclusively on the provided context. Do not use external knowledge.\n"
        "- Cite sources using [Source N] notation after relevant statements.\n"
        "- If the context does not contain enough information to answer, say so clearly.\n"
        "- Be concise and direct. Use the same technical level as the source material.\n"
        "- When multiple sources support a point, cite all relevant ones."
    )

    user = (
        f"CONTEXT (retrieved from saved video transcripts):\n\n{context}\n\n"
        f"QUESTION: {query}\n\n"
        "Answer the question based only on the context above. Cite sources using [Source N] notation."
    )

    return await _call_llm(system, user)
