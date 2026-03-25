"""Generate testing documentation with an agentic RAG loop and publish it to Confluence.

Workflow:
  1. Scan repository files and split them into retrievable chunks.
  2. Ask Claude to plan the page sections and retrieval queries.
  3. Retrieve evidence per section and draft each section independently.
  4. Assemble the page, review it, optionally revise it, then publish it.

Usage:
  python confluence/agentic_rag_confluence_publisher.py
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import pathlib
import re
import sys
import time

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

INCLUDE_EXTENSIONS = {".md", ".py", ".json", ".csv"}
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".uv", "output", ".pytest_cache",
}
EXCLUDE_FILES = {
    "rag_confluence_publisher.py",
    "agentic_rag_confluence_publisher.py",
}

MAX_RETRIES = 5
INITIAL_WAIT = 10
PAGE_TITLE = "automatedTests-2"
MAX_CONTEXT_CHARS = 24_000
CHUNK_SIZE = 1_500
CHUNK_OVERLAP = 250
TOP_K_CHUNKS = 6
DEFAULT_MODEL = "claude-opus-4-5"

# ── Agent system prompts ──────────────────────────────────────────────────────
# Each prompt targets a distinct role in the agentic loop.
# Keeping them separate lets each Claude call be independently tuned.

# Step 3 — Planner: decides the page outline and formulates retrieval queries
PLANNER_SYSTEM_PROMPT = """\
You are a QA documentation planner.
Return valid JSON only.
Design a concise but complete documentation plan for a Confluence page describing a software testing workspace.
Each section must include a name, a goal, and 2 to 4 retrieval queries.
Prefer practical sections that can be supported by repository evidence.
"""

# Step 4 — Writer: drafts one section at a time from retrieved evidence
WRITER_SYSTEM_PROMPT = """\
You are an expert QA engineer writing part of a Confluence page.
Return only valid Confluence Storage Format XHTML for the requested section body.
Do not wrap the result in html or body tags.
Use evidence only from the provided excerpts.
"""

# Step 5a — Reviewer: critiques the assembled draft for gaps and unsupported claims
REVIEWER_SYSTEM_PROMPT = """\
You are reviewing a generated Confluence documentation page for factual support and completeness.
Return valid JSON only.
Be strict about unsupported claims and missing operational details.
"""

# Step 5b — Revisor: rewrites only the parts flagged by the reviewer
REVISION_SYSTEM_PROMPT = """\
You are revising a Confluence page draft.
Return only valid Confluence Storage Format XHTML.
Preserve correct parts, fix only the issues listed in the review, and use the supplied evidence.
"""


@dataclass(frozen=True)
class Config:
    workspace_root: pathlib.Path
    confluence_url: str
    confluence_token: str
    space_key: str
    anthropic_api_key: str
    claude_model: str
    page_title: str = PAGE_TITLE
    max_context_chars: int = MAX_CONTEXT_CHARS
    chunk_size: int = CHUNK_SIZE
    chunk_overlap: int = CHUNK_OVERLAP
    top_k_chunks: int = TOP_K_CHUNKS

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            workspace_root=pathlib.Path(__file__).parent.parent.resolve(),
            confluence_url=os.getenv("CONFLUENCE_URL", "").rstrip("/"),
            confluence_token=os.getenv("CONFLUENCE_TOKEN", ""),
            space_key=os.getenv("CONFLUENCE_SPACE_KEY") or os.getenv("PROJECT_KEY", "CTC2S"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            claude_model=os.getenv("CLAUDE_MODEL", DEFAULT_MODEL),
        )

    def validate(self) -> None:
        errors = []
        if not self.confluence_url:
            errors.append("CONFLUENCE_URL not set in .env")
        if not self.confluence_token:
            errors.append("CONFLUENCE_TOKEN not set in .env")
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY not set in .env")

        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)


@dataclass(frozen=True)
class Document:
    relative_path: str
    content: str

    @property
    def size(self) -> int:
        return len(self.content)

    @property
    def normalized_path(self) -> str:
        return self.relative_path.replace("\\", "/").lower()


@dataclass(frozen=True)
class Chunk:
    source_path: str
    chunk_id: str
    text: str

    @property
    def normalized_text(self) -> str:
        return self.text.lower()


@dataclass(frozen=True)
class SectionPlan:
    name: str
    goal: str
    queries: list[str]


@dataclass(frozen=True)
class ReviewResult:
    needs_revision: bool
    missing_topics: list[str]
    unsupported_claims: list[str]
    revision_instructions: str


def build_session(config: Config) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.confluence_token}",
    })
    return session


def request_with_retry(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.request(method, url, **kwargs)
        if response.status_code != 429:
            return response

        retry_after = response.headers.get("Retry-After")
        try:
            wait_time = min(int(retry_after), 120) if retry_after else INITIAL_WAIT * (2 ** (attempt - 1))
        except (ValueError, OverflowError):
            wait_time = INITIAL_WAIT * (2 ** (attempt - 1))

        print(f"  Rate limit (429) - attempt {attempt}/{MAX_RETRIES}, waiting {wait_time}s...")
        time.sleep(wait_time)

    return response


def should_include_file(file_path: pathlib.Path) -> bool:
    if file_path.is_dir():
        return False
    if any(part in EXCLUDE_DIRS for part in file_path.parts):
        return False
    if file_path.suffix.lower() not in INCLUDE_EXTENSIONS:
        return False
    if file_path.name in EXCLUDE_FILES:
        return False
    return True


def scan_workspace(workspace_root: pathlib.Path) -> list[Document]:
    documents: list[Document] = []

    for file_path in workspace_root.rglob("*"):
        if not should_include_file(file_path):
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"  Could not read {file_path}: {exc}")
            continue

        relative_path = str(file_path.relative_to(workspace_root))
        documents.append(Document(relative_path=relative_path, content=content))

    documents.sort(key=lambda doc: doc.relative_path)
    return documents


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    # Short documents fit in one chunk — skip the sliding window
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    # step < chunk_size produces the overlap between consecutive windows
    step = max(1, chunk_size - overlap)

    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step

    return chunks


def build_chunks(documents: list[Document], chunk_size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []

    for document in documents:
        for index, chunk_text_value in enumerate(chunk_text(document.content, chunk_size, overlap), start=1):
            chunks.append(
                Chunk(
                    source_path=document.relative_path,
                    chunk_id=f"{document.relative_path}#chunk-{index}",
                    text=chunk_text_value,
                )
            )

    return chunks


def tokenize(text: str) -> set[str]:
    # Preserve dots, slashes, and hyphens so file paths and env var names are
    # kept as single tokens (e.g. "CONFLUENCE_TOKEN", "get_ctc2s_pages.py")
    return set(re.findall(r"[a-zA-Z0-9_./-]+", text.lower()))


def path_priority(path: str) -> int:
    # Lower number = higher retrieval priority.
    # SKILL.md files are the most structured evidence source in this repo.
    normalized = path.replace("\\", "/").lower()
    if normalized.endswith("skill.md"):
        return 0  # Highest priority
    if normalized.endswith(".md"):
        return 1
    if normalized.endswith(".py") and ("test" in normalized or "import" in normalized or "fetch" in normalized):
        return 2  # Test/import scripts are directly relevant
    if normalized.endswith(".py"):
        return 3
    return 4  # JSON / CSV are lowest priority


def score_chunk(chunk: Chunk, query: str) -> tuple[int, int, int]:
    # Lexicographic 3-tuple — Python compares tuples element by element, so the
    # most important signal (token overlap) dominates; ties broken by phrase match,
    # then by file priority.
    #   [0] overlap       — shared tokens between query and chunk (BM25-like)
    #   [1] phrase_bonus  — 1 if the full query appears verbatim in the chunk
    #   [2] priority_bonus — negative path_priority so SKILL.md scores higher
    query_terms = tokenize(query)
    text_terms = tokenize(chunk.text)
    overlap = len(query_terms & text_terms)
    phrase_bonus = 1 if query.lower() in chunk.normalized_text else 0
    priority_bonus = -path_priority(chunk.source_path)
    return overlap, phrase_bonus, priority_bonus


def search_chunks(chunks: list[Chunk], query: str, top_k: int) -> list[Chunk]:
    # Sort descending by score then drop chunks with no token overlap at all
    # (guard value (0, 0, -10) ensures chunks with zero overlap are excluded)
    ranked = sorted(chunks, key=lambda chunk: score_chunk(chunk, query), reverse=True)
    return [chunk for chunk in ranked if score_chunk(chunk, query) > (0, 0, -10)][:top_k]


def combine_evidence(chunks: list[Chunk], max_chars: int) -> str:
    # Concatenate chunks into one evidence string for the Claude prompt.
    # Each block is prefixed with source metadata so the model can cite files.
    # Hard-stops at max_chars to stay within the per-call context budget.
    parts: list[str] = []
    total = 0
    separator = "=" * 60

    for chunk in chunks:
        block = (
            f"\n\n{separator}\nSOURCE: {chunk.source_path}\nCHUNK: {chunk.chunk_id}\n{separator}\n"
            f"{chunk.text}"
        )
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)

    return "".join(parts)


def call_claude_text(config: Config, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    message = client.messages.create(
        model=config.claude_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def parse_json_response(text: str) -> dict:
    # Claude occasionally wraps JSON in a ```json … ``` code fence — strip it before parsing
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def build_workspace_summary(documents: list[Document], max_files: int = 40) -> str:
    lines = []
    for document in documents[:max_files]:
        lines.append(f"- {document.relative_path} ({document.size} chars)")
    return "\n".join(lines)


def plan_document(config: Config, documents: list[Document]) -> list[SectionPlan]:
    # AGENT STEP — Planning
    # Only the file-name summary is sent, not file contents.
    # This keeps the planning prompt small and forces the model to decide
    # what evidence it needs before retrieval happens in the next step.
    summary = build_workspace_summary(documents)
    prompt = f"""Create a documentation plan for a Confluence page named {config.page_title}.

Workspace file summary:
{summary}

Return JSON with this shape:
{{
  "sections": [
    {{
      "name": "...",
      "goal": "...",
      "queries": ["...", "..."]
    }}
  ]
}}

Constraints:
- between 6 and 8 sections
- include setup, manual tests, cucumber tests, browser automation, outputs, and usage
- queries must help retrieve repository evidence
"""
    response = call_claude_text(config, PLANNER_SYSTEM_PROMPT, prompt, max_tokens=1200)
    payload = parse_json_response(response)

    sections = []
    for section in payload.get("sections", []):
        queries = [query.strip() for query in section.get("queries", []) if query.strip()]
        if not queries:
            continue
        sections.append(
            SectionPlan(
                name=section.get("name", "Untitled Section").strip(),
                goal=section.get("goal", "").strip(),
                queries=queries[:4],
            )
        )

    if not sections:
        return [
            SectionPlan("Overview", "Explain the repository testing workflow.", ["README testing workflow", "User Story Xray Confluence browser-use"]),
            SectionPlan("Environment Setup", "Describe installation and required environment variables.", ["pyproject dependencies", ".env.example Anthropic Confluence token"]),
            SectionPlan("Manual Tests", "Explain manual test generation and import flow.", ["manual tests Xray import", "generate-manual-tests SKILL"]),
            SectionPlan("Cucumber Tests", "Explain cucumber generation workflow.", ["cucumber tests skill", "generate-cucumber-tests SKILL"]),
            SectionPlan("Browser Automation", "Describe browser-use scripts.", ["BrowserUser login scripts", "browser-use Anthropic playwright"]),
            SectionPlan("Outputs and Usage", "Show commands and output locations.", ["output json csv confluence output", "python usage commands"]),
        ]

    return sections


def retrieve_for_section(config: Config, chunks: list[Chunk], section: SectionPlan) -> list[Chunk]:
    # AGENT STEP — Targeted retrieval
    # Issue one search per planned query and deduplicate by chunk_id so the same
    # excerpt is not sent twice to the writer even if multiple queries match it.
    selected: list[Chunk] = []
    seen_ids: set[str] = set()

    for query in section.queries:
        for chunk in search_chunks(chunks, query, config.top_k_chunks):
            if chunk.chunk_id in seen_ids:
                continue
            selected.append(chunk)
            seen_ids.add(chunk.chunk_id)

    return selected


def write_section(config: Config, section: SectionPlan, evidence: list[Chunk]) -> str:
    # AGENT STEP — Section drafting
    # Each section is written independently from its own retrieved evidence.
    # Keeping individual prompts small and focused reduces hallucination risk.
    evidence_text = combine_evidence(evidence, config.max_context_chars)
    prompt = f"""Write one Confluence section.

Section title: {section.name}
Section goal: {section.goal}

Instructions:
- Start with an <h2> heading using the section title
- Use only the evidence below
- If evidence is incomplete, stay conservative and avoid unsupported claims
- Include commands or file names only when the evidence supports them

Evidence:
{evidence_text}
"""
    return call_claude_text(config, WRITER_SYSTEM_PROMPT, prompt, max_tokens=2200).strip()


def assemble_document(section_bodies: list[str]) -> str:
    # Join independently written sections into one XHTML body.
    # A blank line between sections improves Confluence rendering.
    return "\n\n".join(section_bodies)


def review_document(config: Config, html_content: str, chunks: list[Chunk]) -> ReviewResult:
    # AGENT STEP — Self-critique
    # A dedicated reviewer role inspects the assembled draft against raw repository
    # evidence and flags missing topics and unsupported claims.
    # Separating review from writing prevents the writer from rationalising its own gaps.
    evidence = combine_evidence(chunks[:20], config.max_context_chars)
    prompt = f"""Review this Confluence XHTML draft.

Return JSON with this shape:
{{
  "needs_revision": true,
  "missing_topics": ["..."],
  "unsupported_claims": ["..."],
  "revision_instructions": "..."
}}

Draft:
{html_content}

Repository evidence sample:
{evidence}
"""
    response = call_claude_text(config, REVIEWER_SYSTEM_PROMPT, prompt, max_tokens=1200)
    payload = parse_json_response(response)
    return ReviewResult(
        needs_revision=bool(payload.get("needs_revision", False)),
        missing_topics=[item for item in payload.get("missing_topics", []) if item],
        unsupported_claims=[item for item in payload.get("unsupported_claims", []) if item],
        revision_instructions=payload.get("revision_instructions", "").strip(),
    )


def build_revision_queries(review: ReviewResult) -> list[str]:
    # Convert review findings into retrieval queries so targeted evidence
    # can be fetched for exactly the issues that were flagged.
    queries = []
    queries.extend(review.missing_topics)
    queries.extend(review.unsupported_claims)
    if review.revision_instructions:
        queries.append(review.revision_instructions)
    return [query for query in queries if query.strip()]


def revise_document(config: Config, html_content: str, review: ReviewResult, chunks: list[Chunk]) -> str:
    # AGENT STEP — Evidence-backed revision
    # Re-retrieve chunks targeted at the review findings, then ask the revisor
    # to patch only the flagged issues and preserve the rest of the draft.
    # Falls back to the first 10 chunks when no queries produce results.
    revision_chunks: list[Chunk] = []
    seen_ids: set[str] = set()

    for query in build_revision_queries(review):
        for chunk in search_chunks(chunks, query, config.top_k_chunks):
            if chunk.chunk_id in seen_ids:
                continue
            revision_chunks.append(chunk)
            seen_ids.add(chunk.chunk_id)

    evidence = combine_evidence(revision_chunks or chunks[:10], config.max_context_chars)
    prompt = f"""Revise this Confluence XHTML draft.

Review findings:
- missing_topics: {json.dumps(review.missing_topics, ensure_ascii=False)}
- unsupported_claims: {json.dumps(review.unsupported_claims, ensure_ascii=False)}
- revision_instructions: {review.revision_instructions}

Current draft:
{html_content}

Evidence for revision:
{evidence}
"""
    return call_claude_text(config, REVISION_SYSTEM_PROMPT, prompt, max_tokens=5000).strip()


def find_existing_page(config: Config, session: requests.Session, title: str) -> dict | None:
    url = f"{config.confluence_url}/rest/api/content"
    params = {
        "spaceKey": config.space_key,
        "title": title,
        "type": "page",
        "expand": "version",
    }
    response = request_with_retry(session, "GET", url, params=params, timeout=30)
    if response.status_code != 200:
        response.raise_for_status()

    results = response.json().get("results", [])
    return results[0] if results else None


def create_page(config: Config, session: requests.Session, title: str, html_content: str) -> dict:
    url = f"{config.confluence_url}/rest/api/content"
    payload = {
        "type": "page",
        "title": title,
        "space": {"key": config.space_key},
        "body": {
            "storage": {
                "value": html_content,
                "representation": "storage",
            }
        },
    }
    response = request_with_retry(session, "POST", url, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def update_page(
    config: Config,
    session: requests.Session,
    page_id: str,
    title: str,
    html_content: str,
    current_version: int,
) -> dict:
    url = f"{config.confluence_url}/rest/api/content/{page_id}"
    payload = {
        "type": "page",
        "title": title,
        "version": {"number": current_version + 1},
        "body": {
            "storage": {
                "value": html_content,
                "representation": "storage",
            }
        },
    }
    response = request_with_retry(session, "PUT", url, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def publish_documentation(config: Config, html_content: str) -> str:
    session = build_session(config)
    existing = find_existing_page(config, session, config.page_title)

    if existing:
        page_id = existing["id"]
        version = existing["version"]["number"]
        print(f"  Page '{config.page_title}' exists (id={page_id}, v{version}) - updating...")
        result = update_page(config, session, page_id, config.page_title, html_content, version)
        action = "Updated"
    else:
        print(f"  Page '{config.page_title}' not found - creating...")
        result = create_page(config, session, config.page_title, html_content)
        action = "Created"

    page_id = result.get("id")
    web_ui = result.get("_links", {}).get("webui", "")
    page_url = (
        f"{config.confluence_url}{web_ui}"
        if web_ui else f"{config.confluence_url}/pages/{page_id}"
    )

    print(f"\n✓ {action} Confluence page '{config.page_title}'")
    print(f"  ID  : {page_id}")
    print(f"  URL : {page_url}")
    return page_url


def run_agentic_rag(config: Config) -> str:
    print("\n[1/6] Scanning workspace files...")
    documents = scan_workspace(config.workspace_root)
    print(f"  Found {len(documents)} file(s)")
    if not documents:
        raise RuntimeError("No files found in workspace. Nothing to document.")

    print("\n[2/6] Building retrieval chunks...")
    chunks = build_chunks(documents, config.chunk_size, config.chunk_overlap)
    print(f"  Built {len(chunks)} chunk(s)")

    print("\n[3/6] Planning documentation sections...")
    sections = plan_document(config, documents)
    print(f"  Planned {len(sections)} section(s)")

    print("\n[4/6] Writing sections with retrieval...")
    section_bodies = []
    for index, section in enumerate(sections, start=1):
        print(f"  [{index}/{len(sections)}] {section.name}")
        evidence = retrieve_for_section(config, chunks, section)
        print(f"    Retrieved {len(evidence)} evidence chunk(s)")
        section_bodies.append(write_section(config, section, evidence))

    html_content = assemble_document(section_bodies)

    print("\n[5/6] Reviewing draft...")
    review = review_document(config, html_content, chunks)
    print(
        f"  needs_revision={review.needs_revision} "
        f"missing_topics={len(review.missing_topics)} unsupported_claims={len(review.unsupported_claims)}"
    )
    if review.needs_revision:
        print("  Revising draft from review feedback...")
        html_content = revise_document(config, html_content, review, chunks)

    print("\n[6/6] Publishing to Confluence...")
    publish_documentation(config, html_content)
    return html_content


def main() -> None:
    config = Config.from_env()
    config.validate()

    print("\n" + "=" * 70)
    print("  AGENTIC RAG - Generate & Publish Testing Documentation")
    print("=" * 70)
    print(f"  Workspace : {config.workspace_root}")
    print(
        f"  Target    : {config.confluence_url}  "
        f"space={config.space_key}  page='{config.page_title}'"
    )
    print(f"  Model     : {config.claude_model}")

    try:
        run_agentic_rag(config)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("  COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()