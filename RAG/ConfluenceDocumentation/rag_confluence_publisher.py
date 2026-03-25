"""Generate testing documentation from the workspace and publish it to Confluence.

Workflow:
  1. Scan the repository for relevant documentation and test assets.
  2. Build a prioritized RAG context from those files.
  3. Ask Claude to produce Confluence Storage Format XHTML.
  4. Create or update the Confluence page named "automatedTests".

Usage:
  python confluence/rag_confluence_publisher.py
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import pathlib
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
EXCLUDE_FILES = {"rag_confluence_publisher.py"}

MAX_RETRIES = 5
INITIAL_WAIT = 10
PAGE_TITLE = "automatedTests"
MAX_CONTEXT_CHARS = 150_000
DEFAULT_MODEL = "claude-opus-4-5"

SYSTEM_PROMPT = """\
You are an expert QA engineer writing a Confluence page for a software testing project.
Your output must be valid Confluence Storage Format XHTML - no Markdown, no plain text.
Only output the page body content (no <html> or <body> wrapper)."""

USER_PROMPT_TEMPLATE = """\
Based on the workspace documentation below, create a complete and professional \
Confluence testing documentation page in Confluence Storage Format (XHTML).

The page must cover:
1. Overview - purpose and scope of the testing framework
2. Test Types - Manual tests, Cucumber/BDD tests, Browser automation tests
3. Agent Skills - the available agent skills, their step-by-step workflows, \
and when to use each
4. Environment Setup - required .env variables and Python dependencies
5. How to Run Tests - command-line usage examples for each test type
6. Test Outputs - where results are stored and their format

Formatting rules:
- Use <h1>, <h2>, <h3>, <p>, <ul>, <li>, <strong>, <em> tags
- For code blocks use the Confluence Code macro:
  <ac:structured-macro ac:name="code">
    <ac:parameter ac:name="language">python</ac:parameter>
    <ac:plain-text-body><![CDATA[code here]]></ac:plain-text-body>
  </ac:structured-macro>
- For shell commands use language="bash"
- For info notes:
  <ac:structured-macro ac:name="info">
    <ac:rich-text-body><p>note text</p></ac:rich-text-body>
  </ac:structured-macro>
- For warning notes:
  <ac:structured-macro ac:name="warning">
    <ac:rich-text-body><p>warning text</p></ac:rich-text-body>
  </ac:structured-macro>

Output ONLY the XHTML body content. No prose explanation before or after.

WORKSPACE DOCUMENTATION:
{context}
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


def build_session(config: Config) -> requests.Session:
    """Build an authenticated requests session for Confluence."""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.confluence_token}",
    })
    return session


def request_with_retry(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """Run an HTTP request with exponential back-off on HTTP 429."""
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
    """Collect eligible workspace documents for the RAG context."""
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


def document_priority(document: Document) -> int:
    """Lower number means higher priority in the generated context."""
    path = document.normalized_path
    if path.endswith("skill.md"):
        return 0
    if path.endswith(".md"):
        return 1
    if path.endswith(".py") and ("test" in path or "import" in path or "fetch" in path):
        return 2
    if path.endswith(".py"):
        return 3
    return 4


def build_context(documents: list[Document], max_context_chars: int) -> str:
    """Build a bounded prompt context from prioritized documents."""
    parts: list[str] = []
    total = 0
    separator = "=" * 60

    for document in sorted(documents, key=document_priority):
        header = f"\n\n{separator}\nFILE: {document.relative_path}\n{separator}\n"
        chunk = header + document.content

        if total + len(chunk) > max_context_chars:
            remaining = max_context_chars - total - len(header) - 100
            if remaining <= 300:
                break
            chunk = header + document.content[:remaining] + "\n[... truncated ...]"

        parts.append(chunk)
        total += len(chunk)

    return "".join(parts)


def generate_test_documentation(config: Config, context: str) -> str:
    """Generate Confluence Storage Format XHTML with Claude."""
    print(f"  Using model: {config.claude_model}")
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    message = client.messages.create(
        model=config.claude_model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(context=context)}
        ],
    )
    return message.content[0].text


def find_existing_page(config: Config, session: requests.Session, title: str) -> dict | None:
    """Return the matching Confluence page when it already exists."""
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
    """Create a new Confluence page."""
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
    """Update an existing Confluence page."""
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
    """Create or update the target Confluence page and return its URL."""
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


def main() -> None:
    config = Config.from_env()

    print("\n" + "=" * 70)
    print("  RAG AGENT - Generate & Publish Testing Documentation")
    print("=" * 70)
    print(f"  Workspace : {config.workspace_root}")
    print(
        f"  Target    : {config.confluence_url}  "
        f"space={config.space_key}  page='{config.page_title}'"
    )

    config.validate()

    print("\n[1/4] Scanning workspace files...")
    documents = scan_workspace(config.workspace_root)
    print(f"  Found {len(documents)} file(s):")
    for document in documents:
        print(f"    - {document.relative_path}  ({document.size:,} chars)")

    if not documents:
        print("ERROR: No files found in workspace. Nothing to document.")
        sys.exit(1)

    print("\n[2/4] Building context...")
    context = build_context(documents, config.max_context_chars)
    print(f"  Context: {len(context):,} chars  (~{len(context)//4:,} tokens estimated)")

    print("\n[3/4] Generating documentation with Claude...")
    html_content = generate_test_documentation(config, context)
    print(f"  Generated {len(html_content):,} chars of Confluence storage XHTML")

    print("\n[4/4] Publishing to Confluence...")
    publish_documentation(config, html_content)

    print("\n" + "=" * 70)
    print("  COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
