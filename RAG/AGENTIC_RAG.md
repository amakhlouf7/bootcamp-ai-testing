# Agentic RAG Confluence Publisher

Script: `confluence/agentic_rag_confluence_publisher.py`

---

## What is Agentic RAG?

Standard RAG dumps all documents into a single prompt and calls the model once.  
**Agentic RAG** adds a planning step, per-section targeted retrieval, and a self-critique loop:

```
Scan → Chunk → Plan → Retrieve → Write → Assemble → Review → (Revise) → Publish
```

The model decides *what* to look for, retrieves only the relevant evidence, critiques its own draft, and only publishes when the review is satisfied — without any external database.

---

## Workflow (6 steps)

### Step 1 — Scan workspace
Recursively collect files matching `.md`, `.py`, `.json`, `.csv`.  
Skips: `.git`, `__pycache__`, `.venv`, `output/`, and the publisher scripts themselves.

### Step 2 — Build retrieval index (in memory)
Each file is split into overlapping text windows:

| Parameter | Default | Effect |
|---|---|---|
| `CHUNK_SIZE` | 1 500 chars | Size of each window |
| `CHUNK_OVERLAP` | 250 chars | Overlap between consecutive windows |

All chunks are stored as a plain `list[Chunk]` in process memory — no database required.

### Step 3 — Plan (Claude call #1)
The model receives only a file-name summary (not file contents) and returns a JSON array of 6–8 sections, each with:
- `name` — section title
- `goal` — what to explain
- `queries` — 2–4 retrieval strings to use in step 4

```json
{
  "sections": [
    { "name": "Overview", "goal": "...", "queries": ["README workflow", "..."] },
    ...
  ]
}
```

Separating planning from writing forces the model to declare its information needs before retrieval.

### Step 4 — Retrieve + Write (one Claude call per section)
For each planned section:
1. Each query is scored against all chunks (keyword overlap + phrase bonus + file priority).
2. Top-K unique chunks are selected as evidence.
3. Claude writes the section in Confluence Storage Format XHTML using only that evidence.

### Step 5 — Review + Revise (Claude call N+1, optional N+2)
The assembled page is sent to a **reviewer role** which returns:

```json
{
  "needs_revision": true,
  "missing_topics": [...],
  "unsupported_claims": [...],
  "revision_instructions": "..."
}
```

If `needs_revision` is `true`, the missing topics and unsupported claims are turned into new retrieval queries, fresh evidence is fetched, and a **revisor role** patches only the flagged parts.

### Step 6 — Publish
The final XHTML is sent to the Confluence REST API:
- Page exists → `PUT /rest/api/content/{id}` (version incremented)
- Page absent → `POST /rest/api/content`

HTTP 429 responses trigger exponential back-off (10 s, 20 s, 40 s … up to 120 s, max 5 retries).

---

## In-memory "database"

There is no external database. The retrieval index is rebuilt every run from the filesystem:

| Concept | Database equivalent | Implementation here |
|---|---|---|
| Document store | Table / collection | `list[Document]` in RAM |
| Index | Vector index | `list[Chunk]` in RAM |
| Similarity search | cosine / ANN query | Token overlap + phrase bonus (pure Python `set` operations) |
| Metadata filter | WHERE clause | `path_priority()` applied as a score signal |

### Scoring formula

Each chunk is ranked by a 3-tuple compared lexicographically:

```
score = (token_overlap, phrase_bonus, -path_priority)
```

| Signal | Description |
|---|---|
| `token_overlap` | Number of shared tokens between query and chunk |
| `phrase_bonus` | +1 if the full query string appears verbatim in the chunk |
| `-path_priority` | SKILL.md > other .md > test .py > other .py > JSON/CSV |

Chunks with zero token overlap are excluded entirely.

---

## Agent roles

| Role | Prompt constant | Purpose |
|---|---|---|
| Planner | `PLANNER_SYSTEM_PROMPT` | Decide page structure and retrieval queries |
| Writer | `WRITER_SYSTEM_PROMPT` | Draft one section from retrieved evidence |
| Reviewer | `REVIEWER_SYSTEM_PROMPT` | Critique draft for gaps and unsupported claims |
| Revisor | `REVISION_SYSTEM_PROMPT` | Patch only the flagged issues |

---

## Configuration

All values read from `.env`:

| Variable | Required | Description |
|---|---|---|
| `CONFLUENCE_URL` | ✅ | Base URL of the Confluence instance |
| `CONFLUENCE_TOKEN` | ✅ | Personal Access Token (Bearer) |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key |
| `PROJECT_KEY` | ✅ | Confluence space key (e.g. `CTC2S`) |
| `CONFLUENCE_SPACE_KEY` | optional | Overrides `PROJECT_KEY` for the space |
| `CLAUDE_MODEL` | optional | Overrides default model (`claude-opus-4-5`) |

---

## Usage

```bash
python confluence/agentic_rag_confluence_publisher.py
```

Expected output:

```
[1/6] Scanning workspace files...     → N file(s)
[2/6] Building retrieval chunks...    → N chunk(s)
[3/6] Planning documentation sections → N section(s)
[4/6] Writing sections with retrieval → one line per section
[5/6] Reviewing draft...              → needs_revision=True/False
[6/6] Publishing to Confluence...     → URL
```

---

## Comparison with the linear publisher

| Aspect | `rag_confluence_publisher.py` | `agentic_rag_confluence_publisher.py` |
|---|---|---|
| Context strategy | All files concatenated once | Chunked + per-section retrieval |
| Planning | None | Claude plans sections and queries |
| Writing | One monolithic call | One call per section |
| Self-critique | None | Reviewer + optional revision pass |
| Claude calls | 1 | 1 (plan) + N (write) + 1 (review) + 0–1 (revise) |
| Hallucination risk | Higher (large unfocused prompt) | Lower (small targeted evidence per section) |
