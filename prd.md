# 🧠 PRODUCT REQUIREMENTS DOCUMENT (FINAL)

## 🏷 Product Name (Working)

**MemTube / VideoBrain / RecallAI**

---

# 🎯 1. Product Vision

> "A personalized second brain for everything you watch on YouTube."

This is NOT:

* a summarizer
* a quiz generator

👉 This is a **learning system that remembers, connects, and evolves knowledge over time**

---

# 👥 2. Target Users

### Primary:

* Developers
* Students
* Self-learners

### Secondary:

* Content creators
* Researchers

---

# 🚀 3. Core Value Proposition

### 🔥 Unique Differentiators

1. **Memory Layer (User-owned knowledge)**
2. **Cross-video intelligence**
3. **Delta learning (what's NEW vs what you know)**
4. **Learning graph (knowledge connections)**

---

# 🧩 4. Core Features

---

## 4.1 Video Processing

* Input: YouTube URL ✅ DONE — URL validation + submission form live
* Output:

  * Summary (short + detailed)
  * Key insights
  * Concepts & entities
  * Relationships
  * Quiz (MCQ + flashcards)

---

## 4.2 Memory System (CORE USP)

User can:

* Save entire video OR specific insights
* Edit / delete memory
* Tag memory

---

### Memory Types:

| Level | Type              | Description                    |
| ----- | ----------------- | ------------------------------ |
| L1    | Chunk Memory      | Raw semantic chunks            |
| L2    | Concept Memory    | Extracted knowledge            |
| L3    | Compressed Memory | Summarized long-term knowledge |

---

## 4.3 Retrieval & Intelligence

* Ask questions across videos
* Personalized summaries
* Context-aware generation
* Learning recommendations

---

## 4.4 Personalization

System learns:

* Interests
* Weak areas
* Strong areas
* Engagement patterns

---

## 4.5 Learning Graph (Advanced Feature)

* Nodes = concepts
* Edges = relationships

Enables:

* "What should I learn next?"
* "You're missing prerequisite X"

---

## 4.6 Delta Learning (Advanced Feature)

When new video processed:

Instead of:
❌ repeating info

System outputs:

* ✅ New knowledge
* 🔁 Reinforced concepts
* ⚠️ Contradictions

---

# 🧠 5. System Architecture

---

## 🏗 High-Level Stack

### Frontend ✅ DONE (Phase 1)

* Next.js 16 (App Router) ✅
* Tailwind CSS v4 ✅
* Server Actions ✅

---

### Backend (STRICT SEPARATION)

#### FastAPI (Brain)

* AI pipelines
* Retrieval logic
* Memory processing

#### Supabase (Data Layer)

* Postgres (pgvector) ✅ DONE — vector extension enabled
* Auth ✅ DONE — Supabase Auth wired up (signup/login/logout)
* Storage

---

### Async System

* Redis (queue)
* Celery workers

---

### AI Layer

* LLM (OpenAI / Claude / switchable)
* Embeddings (OpenAI / open-source)

---

# 🔄 6. Full System Flow

---

## Step 1: Input ✅ DONE

User submits YouTube URL via dashboard form

→ creates `video` record with `status = 'pending'`

---

## Step 2: Async Pipeline

### Stage 1: Transcript

* YouTube API
* Whisper fallback

---

### Stage 2: Chunking (CRITICAL)

* Semantic chunking
* ~500–1000 tokens
* Add metadata

---

### Stage 3: Extraction

For each chunk:

* Summary
* Concepts
* Entities
* Relationships

---

### Stage 4: Canonical Mapping (NEW)

Map:

```text
"RAG" → "Retrieval Augmented Generation"
```

→ stored in canonical layer

---

### Stage 5: Embeddings

* chunk embeddings
* concept embeddings

---

### Stage 6: Quiz Generation

* MCQs
* Flashcards

---

### Stage 7: Store

→ Supabase

---

## Step 3: Memory Save

User clicks:

→ triggers memory pipeline

---

## Step 4: Future Retrieval

* detect topic
* fetch relevant memory
* inject into LLM

---

# 🗄 7. Database Design (Production-Level)

---

## 🔹 Core Tables

---

### videos ✅ DONE

```sql
id            UUID PRIMARY KEY
user_id       UUID (FK → auth.users)
youtube_id    TEXT
title         TEXT
thumbnail_url TEXT
duration_sec  INTEGER
status        TEXT ('pending' | 'processing' | 'completed' | 'failed')
error_message TEXT
created_at    TIMESTAMPTZ
updated_at    TIMESTAMPTZ
UNIQUE (user_id, youtube_id)
```

---

### chunks ✅ DONE (schema created; populated in Phase 2)

```sql
id         UUID PRIMARY KEY
video_id   UUID (FK → videos)
content    TEXT
start_time REAL
end_time   REAL
embedding  vector(1536)
version    INTEGER
created_at TIMESTAMPTZ
```

---

### concepts ✅ DONE (schema created; populated in Phase 2)

```sql
id          UUID PRIMARY KEY
video_id    UUID (FK → videos)
name        TEXT
description TEXT
confidence  REAL
created_at  TIMESTAMPTZ
```

---

### canonical_concepts (NEW 🔥)

```sql
id
normalized_name
aliases[]
embedding
```

---

### concept_mappings

```sql
concept_id
canonical_id
```

---

### relationships

```sql
id
source_id
target_id
type
confidence
```

---

### quizzes

```sql
id
video_id
type
question
options JSONB
answer
```

---

### memory_saves ✅ DONE (schema created; UI in Phase 3)

```sql
id         UUID PRIMARY KEY
user_id    UUID (FK → auth.users)
chunk_id   UUID (FK → chunks, nullable)
concept_id UUID (FK → concepts, nullable)
created_at TIMESTAMPTZ
CHECK (chunk_id IS NOT NULL OR concept_id IS NOT NULL)
```

---

### user_profiles ✅ DONE (auto-created on signup via DB trigger)

```sql
user_id       UUID PRIMARY KEY (FK → auth.users)
display_name  TEXT
interests     TEXT[]
weak_topics   TEXT[]
strong_topics TEXT[]
created_at    TIMESTAMPTZ
updated_at    TIMESTAMPTZ
```

---

### evaluations (NEW 🔥)

```sql
id
output_id
score
feedback
```

---

# 🔍 8. Retrieval System (FINAL DESIGN)

---

## Step 1: Query Understanding

* extract:

  * topic
  * intent

---

## Step 2: Hybrid Search

* vector similarity
* metadata filtering

---

## Step 3: Scoring (IMPORTANT)

```python
score =
  0.6 * similarity +
  0.2 * topic_match +
  0.1 * recency +
  0.1 * user_interest
```

---

## Step 4: Context Selection

* token-aware filtering
* remove redundancy

---

## Step 5: LLM Generation

---

# 🧠 9. Memory Compression System (NEW 🔥)

---

## Problem:

Memory grows infinitely

---

## Solution:

Periodic job:

* merge similar concepts
* summarize old chunks
* remove low-value data

---

## Example:

```python
if similarity(chunkA, chunkB) > 0.9:
    merge()
```

---

# 🧪 10. Evaluation System

---

## Metrics:

* summary quality
* retrieval accuracy
* quiz performance

---

## Feedback Loop:

* user feedback
* LLM self-evaluation

---

# 🛡 11. Guardrails

---

* schema validation (strict)
* confidence thresholds
* hallucination filtering

---

# 💰 12. Cost Optimization

---

* cache transcripts
* reuse embeddings
* batch API calls
* model tiering (cheap → expensive)

---

# ⚡ 13. Performance

---

* async pipeline
* WebSockets for updates
* vector indexing (HNSW)

---

# 🔐 14. Security

---

* Supabase Auth ✅ DONE
* RLS policies ✅ DONE — all 5 tables protected
* JWT validation ✅ DONE — middleware refreshes session on every request
* encrypted storage

---

# ⚠️ 15. Risks & Mitigation

---

| Risk              | Solution           |
| ----------------- | ------------------ |
| YouTube API fails | Whisper fallback   |
| High cost         | caching + batching |
| noisy retrieval   | hybrid search      |
| hallucinations    | validation layer   |

---

# 🚀 16. Deployment

---

* Frontend: Vercel
* Backend: Docker (FastAPI)
* Workers: Celery + Redis
* DB: Supabase ✅ DONE — project provisioned, schema live

---

# 🧠 17. What Makes This Product Special

---

Most tools:

* summarize videos ❌

Your system:

* remembers
* connects
* evolves knowledge

👉 This is closer to:

* Notion AI + Perplexity + Duolingo

---

# 🧠 Final Verdict

---

## ✅ This is now:

* Production-ready architecture
* Scalable
* Differentiated

---

## 🔥 Your Real Moat:

1. Memory system
2. Canonical knowledge layer
3. Hybrid retrieval
4. Learning graph
5. Delta learning

---

# 📦 Implementation Progress

---

## ✅ Phase 1 — Foundation (COMPLETED)

Items not originally in PRD, logged here for completeness:

### Infrastructure
- Installed `@supabase/supabase-js` + `@supabase/ssr`
- Created `.env.local` with Supabase URL and anon key
- `lib/supabase/client.ts` — browser-side Supabase client
- `lib/supabase/server.ts` — server-side Supabase client (cookie-aware for SSR)
- `middleware.ts` — session refresh on every request; redirects unauthenticated users away from `/dashboard`, logged-in users away from `/login` and `/signup`
- `lib/database.types.ts` — TypeScript types (`Video`, `UserProfile`, `VideoStatus`)

### Database Migrations (via Supabase MCP)
- Migration 001: enabled `vector` (pgvector) and `uuid-ossp` extensions
- Migration 002: created `videos`, `user_profiles`, `chunks`, `concepts`, `memory_saves` tables with triggers (`handle_updated_at`, `handle_new_user` auto-creates profile on signup)
- Migration 003: RLS policies on all 5 tables; `service_role` grants for future FastAPI worker

### Auth
- `app/actions/auth.ts` — `signUp`, `signIn`, `signOut` Server Actions
- `components/auth/LoginForm.tsx` — client-side login form with `useActionState`
- `components/auth/SignupForm.tsx` — client-side signup form with `useActionState`
- `app/login/page.tsx` — login page
- `app/signup/page.tsx` — signup page

### Video Submission
- `app/actions/videos.ts` — `submitVideo` Server Action (URL validation, YouTube ID extraction, DB insert, duplicate handling)
- `components/videos/SubmitVideoForm.tsx` — URL input form, shows success/error states
- `components/videos/VideoCard.tsx` — displays video with colour-coded status badge (Queued / Processing / Ready / Failed)
- `components/videos/VideoList.tsx` — renders list of VideoCards with empty state

### Pages
- `app/page.tsx` — landing page with CTA buttons (Get started / Sign in)
- `app/dashboard/layout.tsx` — auth-guarded nav shell with sign-out button
- `app/dashboard/page.tsx` — dashboard: server-fetches user's videos, renders form + list
- `app/layout.tsx` — updated metadata title and description

---

## 🔲 Phase 2 — Video Processing Pipeline (NEXT)

* FastAPI backend setup (Docker)
* YouTube transcript fetching (YouTube API + Whisper fallback)
* Semantic chunking pipeline
* Concept & entity extraction (LLM)
* Embeddings generation + storage in `chunks` table
* Video status updates (pending → processing → completed/failed)
* WebSocket or polling for real-time status on dashboard

---

## 🔲 Phase 3 — Memory & Retrieval

* Memory save UI (save chunk or concept to `memory_saves`)
* Hybrid retrieval system (vector similarity + metadata scoring)
* Ask questions across videos
* Canonical concept layer (`canonical_concepts`, `concept_mappings` tables)

---

## 🔲 Phase 4 — Advanced Features

* Quiz generation (MCQs + flashcards)
* Learning graph (`relationships` table + visualization)
* Delta learning (new vs reinforced vs contradictions)
* Personalization (user interest profiling)
* Memory compression (periodic merge job)
* Evaluation system (`evaluations` table)
