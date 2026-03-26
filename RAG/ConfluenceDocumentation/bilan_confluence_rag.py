"""Bilan Confluence Test — Générateur RAG avec Vector DB (ChromaDB).

Architecture :
  1. JQL Jira  → Récupération des tests ET des bugs du projet/sprint.
  2. KPI Calc  → Calcul des KPIs : couverture, taux de succès, densité de bugs, sévérité.
  3. ChromaDB  → Stockage persistant des snapshots KPI + recherche de tendances historiques.
  4. RAG Claude → Génération de l'analyse narrative (insights, tendances, recommandations).
  5. Confluence → Publication de la page sous CoachTestC2S (parent configurable).

Variables .env requises :
  JIRA_URL, JIRA_TOKEN, CONFLUENCE_URL, CONFLUENCE_TOKEN,
  PROJECT_KEY, SPRINT_NAME, ANTHROPIC_API_KEY,
  CONFLUENCE_PARENT_PAGE_ID (ID de la page CoachTestC2S),
  CONFLUENCE_SPACE_KEY (ex: CTC2S),
  CHROMA_PERSIST_DIR (optionnel, défaut: ./chroma_kpi_db)

JQL personnalisables (optionnels, des valeurs par défaut sont générées) :
  JQL_ALL_TESTS, JQL_PASSED, JQL_FAILED, JQL_BLOCKED, JQL_NOT_EXECUTED,
  JQL_BUGS_OPEN, JQL_BUGS_CRITICAL, JQL_BUGS_RESOLVED, JQL_TESTS_AUTOMATED

Usage :
  python RAG/ConfluenceDocumentation/bilan_confluence_rag.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import anthropic
import chromadb
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Constantes ─────────────────────────────────────────────────────────────────

MAX_RETRIES = 5
INITIAL_WAIT = 10
DEFAULT_MODEL = "claude-opus-4-5"
CHROMA_COLLECTION = "kpi_bilan_test"
MAX_HISTORY_CONTEXT = 5          # nombre de snapshots historiques remontés par RAG
MAX_ISSUES_TABLE = 50            # lignes max dans les tableaux de détail

PASS_STATUSES  = {"pass", "passed", "réussi", "success"}
FAIL_STATUSES  = {"fail", "failed", "échoué", "failure"}
BLOCKED_STATUSES = {"blocked", "bloqué", "in progress"}

# ── Prompts Claude ──────────────────────────────────────────────────────────────

ANALYST_SYSTEM_PROMPT = """\
Tu es un expert QA Lead spécialisé dans l'analyse des résultats de tests logiciels.
Tu analyses les KPIs de tests et de bugs d'un projet pour rédiger un bilan clair,
factuel et actionnable destiné à une page Confluence.
Réponds uniquement en français. Sois précis, concis et orienté données.
N'invente aucun chiffre — utilise uniquement les données fournies.
"""

ANALYST_USER_TEMPLATE = """\
## KPIs Actuels ({sprint_name} — {date})

### Tests
- Total tests        : {total}
- Passés             : {passed} ({success_rate}%)
- Échoués            : {failed}
- Bloqués            : {blocked}
- Non exécutés       : {not_executed}
- Taux d'exécution   : {execution_rate}%
- Tests automatisés  : {automated} ({automation_rate}%)

### Couverture
- Tests planifiés    : {planned_tests}
- Tests exécutés     : {executed_tests}
- Taux de couverture : {coverage_rate}%

### Bugs
- Bugs ouverts       : {bugs_open}
- Bugs critiques     : {bugs_critical}
- Bugs résolus       : {bugs_resolved}
- Densité de bugs    : {bug_density} bugs/100 tests
- Taux de résolution : {resolution_rate}%

## Historique des {history_count} derniers bilans
{history_summary}

## Instructions
1. Rédige une analyse narrative de 3 à 5 paragraphes couvrant :
   - Résumé exécutif des KPIs actuels (forces et points d'attention)
   - Tendances observées par rapport à l'historique (progression/régression)
   - Analyse de la qualité des bugs (densité, criticité, résolution)
   - Risques identifiés et recommandations concrètes
2. Formate chaque paragraphe avec un titre Confluence h3 en HTML (ex: <h3>...</h3>).
3. Utilise des bullet points HTML (<ul><li>...</li></ul>) pour les recommandations.
4. Ne dépasse pas 800 mots.
"""

# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class Config:
    jira_url: str
    jira_token: str
    confluence_url: str
    confluence_token: str
    anthropic_api_key: str
    space_key: str
    project_key: str
    sprint_name: str
    parent_page_id: Optional[str]
    chroma_persist_dir: str
    claude_model: str
    page_title: str
    # JQL
    jql_all_tests: str
    jql_passed: str
    jql_failed: str
    jql_blocked: str
    jql_not_executed: str
    jql_bugs_open: str
    jql_bugs_critical: str
    jql_bugs_resolved: str
    jql_tests_automated: str

    @classmethod
    def from_env(cls) -> "Config":
        pk = os.getenv("PROJECT_KEY", "")
        sprint = os.getenv("SPRINT_NAME", "Sprint Courant")
        page_title = os.getenv("BILAN_PAGE_TITLE", f"Bilan Test — {sprint}")

        # Requêtes JQL par défaut — surchargeable via .env
        def_all    = f'project = "{pk}" AND issuetype = Test ORDER BY created DESC'
        def_passed = f'project = "{pk}" AND issuetype = Test AND status in ("Pass","Passed") ORDER BY created DESC'
        def_failed = f'project = "{pk}" AND issuetype = Test AND status in ("Fail","Failed") ORDER BY created DESC'
        def_blocked= f'project = "{pk}" AND issuetype = Test AND status = "Blocked" ORDER BY created DESC'
        def_notexec= f'project = "{pk}" AND issuetype = Test AND status in ("To Do","Open") ORDER BY created DESC'
        def_bugs   = f'project = "{pk}" AND issuetype = Bug AND status != Done ORDER BY priority DESC'
        def_crit   = f'project = "{pk}" AND issuetype = Bug AND priority in ("Critical","Blocker") AND status != Done'
        def_resolv = f'project = "{pk}" AND issuetype = Bug AND status = Done AND updated >= -30d'
        def_auto   = f'project = "{pk}" AND issuetype = Test AND labels = "automated" ORDER BY created DESC'

        return cls(
            jira_url           = os.getenv("JIRA_URL", "").rstrip("/"),
            jira_token         = os.getenv("JIRA_TOKEN", ""),
            confluence_url     = os.getenv("CONFLUENCE_URL", "").rstrip("/"),
            confluence_token   = os.getenv("CONFLUENCE_TOKEN", ""),
            anthropic_api_key  = os.getenv("ANTHROPIC_API_KEY", ""),
            space_key          = os.getenv("CONFLUENCE_SPACE_KEY") or pk,
            project_key        = pk,
            sprint_name        = sprint,
            parent_page_id     = os.getenv("CONFLUENCE_PARENT_PAGE_ID") or None,
            chroma_persist_dir = os.getenv("CHROMA_PERSIST_DIR", "./chroma_kpi_db"),
            claude_model       = os.getenv("CLAUDE_MODEL", DEFAULT_MODEL),
            page_title         = page_title,
            jql_all_tests      = os.getenv("JQL_ALL_TESTS",      def_all),
            jql_passed         = os.getenv("JQL_PASSED",         def_passed),
            jql_failed         = os.getenv("JQL_FAILED",         def_failed),
            jql_blocked        = os.getenv("JQL_BLOCKED",        def_blocked),
            jql_not_executed   = os.getenv("JQL_NOT_EXECUTED",   def_notexec),
            jql_bugs_open      = os.getenv("JQL_BUGS_OPEN",      def_bugs),
            jql_bugs_critical  = os.getenv("JQL_BUGS_CRITICAL",  def_crit),
            jql_bugs_resolved  = os.getenv("JQL_BUGS_RESOLVED",  def_resolv),
            jql_tests_automated= os.getenv("JQL_TESTS_AUTOMATED",def_auto),
        )

    def validate(self) -> None:
        required = {
            "JIRA_URL": self.jira_url,
            "JIRA_TOKEN": self.jira_token,
            "CONFLUENCE_URL": self.confluence_url,
            "CONFLUENCE_TOKEN": self.confluence_token,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "PROJECT_KEY": self.project_key,
        }
        errors = [f"{k} manquant dans .env" for k, v in required.items() if not v]
        if errors:
            for e in errors:
                print(f"  ❌ {e}")
            sys.exit(1)


@dataclass
class KpiSnapshot:
    """Snapshot complet des KPIs — persisté dans ChromaDB."""
    sprint_name: str
    date_iso: str
    total_tests: int
    passed: int
    failed: int
    blocked: int
    not_executed: int
    automated: int
    planned_tests: int
    bugs_open: int
    bugs_critical: int
    bugs_resolved: int
    # champs calculés
    success_rate: float = 0.0
    execution_rate: float = 0.0
    automation_rate: float = 0.0
    coverage_rate: float = 0.0
    bug_density: float = 0.0
    resolution_rate: float = 0.0

    def __post_init__(self) -> None:
        executed = self.passed + self.failed + self.blocked
        self.success_rate   = _pct(self.passed,      self.total_tests)
        self.execution_rate = _pct(executed,          self.total_tests)
        self.automation_rate= _pct(self.automated,    self.total_tests)
        self.coverage_rate  = _pct(executed,          self.planned_tests) if self.planned_tests else self.execution_rate
        total_bugs = self.bugs_open + self.bugs_resolved
        self.bug_density    = round(self.bugs_open / self.total_tests * 100, 1) if self.total_tests else 0.0
        self.resolution_rate= _pct(self.bugs_resolved, total_bugs) if total_bugs else 0.0

    def to_text(self) -> str:
        """Représentation textuelle pour l'embedding ChromaDB."""
        return (
            f"Sprint: {self.sprint_name} | Date: {self.date_iso} | "
            f"Total={self.total_tests} Passés={self.passed} Échoués={self.failed} "
            f"Bloqués={self.blocked} NonExec={self.not_executed} Automatisés={self.automated} "
            f"TauxSuccès={self.success_rate}% TauxExéc={self.execution_rate}% "
            f"Couverture={self.coverage_rate}% Automatisation={self.automation_rate}% "
            f"BugsOuverts={self.bugs_open} BugsCritiques={self.bugs_critical} "
            f"BugsRésolus={self.bugs_resolved} DensitéBugs={self.bug_density} "
            f"TauxRésolution={self.resolution_rate}%"
        )

    def unique_id(self) -> str:
        return hashlib.md5(f"{self.sprint_name}_{self.date_iso}".encode()).hexdigest()


@dataclass
class IssueDetail:
    key: str
    summary: str
    status: str
    priority: str
    assignee: str
    labels: list[str] = field(default_factory=list)


# ── Utilitaires ────────────────────────────────────────────────────────────────

def _pct(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _color_rate(rate: float) -> str:
    if rate >= 80:
        return "#00875A"
    if rate >= 50:
        return "#FF991F"
    return "#DE350B"


def _trend_arrow(current: float, previous: float) -> str:
    """Retourne une flèche HTML colorée selon la tendance."""
    if previous == 0:
        return ""
    diff = current - previous
    if diff > 2:
        return ' <span style="color:#00875A;">▲ +{:.1f}%</span>'.format(diff)
    if diff < -2:
        return ' <span style="color:#DE350B;">▼ {:.1f}%</span>'.format(diff)
    return ' <span style="color:#6B778C;">→ {:.1f}%</span>'.format(diff)


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _jira_session(config: Config) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {config.jira_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    return s


def _confluence_session(config: Config) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {config.confluence_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    return s


def _request_with_retry(
    session: requests.Session, method: str, url: str, **kwargs
) -> requests.Response:
    for attempt in range(1, MAX_RETRIES + 1):
        resp = session.request(method, url, **kwargs)
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = min(int(retry_after), 120) if retry_after else INITIAL_WAIT * (2 ** (attempt - 1))
        except (ValueError, OverflowError):
            wait = INITIAL_WAIT * (2 ** (attempt - 1))
        print(f"    ⏳ Rate-limit 429 — tentative {attempt}/{MAX_RETRIES}, attente {wait}s…")
        time.sleep(wait)
    return resp  # type: ignore[return-value]


# ── 1. Collecte Jira via JQL ───────────────────────────────────────────────────

def _run_jql(
    session: requests.Session,
    jira_url: str,
    jql: str,
    max_results: int = 500,
    fields: str = "summary,status,assignee,priority,issuetype,labels",
) -> list[dict]:
    url = f"{jira_url}/rest/api/2/search"
    params = {"jql": jql, "maxResults": max_results, "fields": fields}
    resp = _request_with_retry(session, "GET", url, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"    ⚠️  JQL échoué ({resp.status_code}) : {jql[:80]}…")
        return []
    return resp.json().get("issues", [])


def _parse_issue(raw: dict) -> IssueDetail:
    f = raw.get("fields", {})
    return IssueDetail(
        key      = raw.get("key", ""),
        summary  = (f.get("summary") or "")[:120],
        status   = (f.get("status") or {}).get("name", ""),
        priority = (f.get("priority") or {}).get("name", ""),
        assignee = (f.get("assignee") or {}).get("displayName", "Non assigné"),
        labels   = f.get("labels") or [],
    )


@dataclass
class JiraData:
    """Toutes les données brutes issues de Jira."""
    all_tests: list[IssueDetail]
    passed: list[IssueDetail]
    failed: list[IssueDetail]
    blocked: list[IssueDetail]
    not_executed: list[IssueDetail]
    automated: list[IssueDetail]
    bugs_open: list[IssueDetail]
    bugs_critical: list[IssueDetail]
    bugs_resolved: list[IssueDetail]


def collect_jira_data(config: Config) -> JiraData:
    """Exécute tous les JQL et collecte les données Jira."""
    session = _jira_session(config)

    queries = [
        ("Tous les tests",       config.jql_all_tests),
        ("Passés",               config.jql_passed),
        ("Échoués",              config.jql_failed),
        ("Bloqués",              config.jql_blocked),
        ("Non exécutés",        config.jql_not_executed),
        ("Automatisés",          config.jql_tests_automated),
        ("Bugs ouverts",         config.jql_bugs_open),
        ("Bugs critiques",       config.jql_bugs_critical),
        ("Bugs résolus",         config.jql_bugs_resolved),
    ]

    results: list[list[IssueDetail]] = []
    for label, jql in queries:
        print(f"  🔍 [{label}] {jql[:70]}…")
        raw_issues = _run_jql(session, config.jira_url, jql)
        parsed = [_parse_issue(r) for r in raw_issues]
        results.append(parsed)
        print(f"     → {len(parsed)} issue(s)")

    return JiraData(*results)


# ── 2. Calcul des KPIs ─────────────────────────────────────────────────────────

def compute_kpis(config: Config, data: JiraData) -> KpiSnapshot:
    """Calcule le snapshot KPI complet depuis les données Jira."""
    # planned_tests = total des tests (tous statuts confondus)
    planned_tests = len(data.all_tests) or (
        len(data.passed) + len(data.failed) + len(data.blocked) + len(data.not_executed)
    )
    return KpiSnapshot(
        sprint_name   = config.sprint_name,
        date_iso      = datetime.now().strftime("%Y-%m-%d"),
        total_tests   = len(data.all_tests),
        passed        = len(data.passed),
        failed        = len(data.failed),
        blocked       = len(data.blocked),
        not_executed  = len(data.not_executed),
        automated     = len(data.automated),
        planned_tests = planned_tests,
        bugs_open     = len(data.bugs_open),
        bugs_critical = len(data.bugs_critical),
        bugs_resolved = len(data.bugs_resolved),
    )


# ── 3. Vector DB (ChromaDB) ────────────────────────────────────────────────────

def get_chroma_collection(config: Config) -> chromadb.Collection:
    """Initialise (ou ouvre) la collection ChromaDB persistée."""
    client = chromadb.PersistentClient(path=config.chroma_persist_dir)
    return client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def store_snapshot(collection: chromadb.Collection, snapshot: KpiSnapshot) -> None:
    """Persiste le snapshot KPI dans ChromaDB (upsert par ID unique)."""
    doc_id = snapshot.unique_id()
    metadata = {k: v for k, v in asdict(snapshot).items() if isinstance(v, (int, float, str))}
    collection.upsert(
        ids=[doc_id],
        documents=[snapshot.to_text()],
        metadatas=[metadata],
    )
    print(f"  💾 Snapshot stocké dans ChromaDB (id={doc_id[:8]}…)")


def retrieve_history(collection: chromadb.Collection, snapshot: KpiSnapshot) -> list[dict]:
    """Récupère les N snapshots les plus similaires depuis ChromaDB (RAG retrieval)."""
    count = collection.count()
    if count == 0:
        return []
    n = min(MAX_HISTORY_CONTEXT, count)
    results = collection.query(
        query_texts=[snapshot.to_text()],
        n_results=n,
        include=["metadatas", "documents"],
    )
    history: list[dict] = []
    for i, meta in enumerate(results["metadatas"][0]):
        history.append({
            "doc": results["documents"][0][i],
            "meta": meta,
        })
    # Trier par date (plus récent en premier, excluant le snapshot actuel)
    current_id = snapshot.unique_id()
    history = [h for h in history if h["meta"].get("sprint_name") != snapshot.sprint_name
               or h["meta"].get("date_iso") != snapshot.date_iso]
    history.sort(key=lambda h: h["meta"].get("date_iso", ""), reverse=True)
    return history[:MAX_HISTORY_CONTEXT]


def _format_history_for_prompt(history: list[dict]) -> str:
    if not history:
        return "Aucun historique disponible (premier bilan)."
    lines = []
    for h in history:
        m = h["meta"]
        lines.append(
            f"- {m.get('sprint_name','?')} ({m.get('date_iso','?')}) : "
            f"Succès={m.get('success_rate','?')}% Exéc={m.get('execution_rate','?')}% "
            f"Couverture={m.get('coverage_rate','?')}% "
            f"BugsOuverts={m.get('bugs_open','?')} Densité={m.get('bug_density','?')}"
        )
    return "\n".join(lines)


# ── 4. RAG — Génération narrative avec Claude ──────────────────────────────────

def generate_rag_analysis(
    config: Config, snapshot: KpiSnapshot, history: list[dict]
) -> str:
    """Appelle Claude avec contexte RAG pour générer l'analyse narrative."""
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    history_summary = _format_history_for_prompt(history)

    user_prompt = ANALYST_USER_TEMPLATE.format(
        sprint_name    = snapshot.sprint_name,
        date           = snapshot.date_iso,
        total          = snapshot.total_tests,
        passed         = snapshot.passed,
        failed         = snapshot.failed,
        blocked        = snapshot.blocked,
        not_executed   = snapshot.not_executed,
        automated      = snapshot.automated,
        success_rate   = snapshot.success_rate,
        execution_rate = snapshot.execution_rate,
        automation_rate= snapshot.automation_rate,
        planned_tests  = snapshot.planned_tests,
        executed_tests = snapshot.passed + snapshot.failed + snapshot.blocked,
        coverage_rate  = snapshot.coverage_rate,
        bugs_open      = snapshot.bugs_open,
        bugs_critical  = snapshot.bugs_critical,
        bugs_resolved  = snapshot.bugs_resolved,
        bug_density    = snapshot.bug_density,
        resolution_rate= snapshot.resolution_rate,
        history_count  = len(history),
        history_summary= history_summary,
    )

    print(f"  🤖 Appel Claude ({config.claude_model}) pour analyse RAG…")
    message = client.messages.create(
        model=config.claude_model,
        max_tokens=1500,
        system=ANALYST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


# ── 5. Construction du contenu Confluence (Storage Format XHTML) ───────────────

def _kpi_card(icon: str, count: int | float, label: str, color: str, suffix: str = "") -> str:
    return f"""<td style="text-align:center;padding:14px;border:1px solid #DFE1E6;min-width:110px;">
  <p style="font-size:26px;font-weight:bold;color:{color};margin:0;">{icon} {count}{suffix}</p>
  <p style="font-size:11px;color:#6B778C;margin:4px 0 0 0;text-transform:uppercase;">{label}</p>
</td>"""


def _build_test_kpi_table(snap: KpiSnapshot, prev: Optional[dict]) -> str:
    def trend(key: str, current: float) -> str:
        return _trend_arrow(current, float(prev.get(key, current))) if prev else ""

    rows = [
        ("🔢", snap.total_tests,      "Total Tests",       "#0052CC", ""),
        ("✅", snap.passed,           "Passés",            "#00875A", ""),
        ("❌", snap.failed,           "Échoués",           "#DE350B", ""),
        ("⛔", snap.blocked,          "Bloqués",           "#FF991F", ""),
        ("⬜", snap.not_executed,     "Non Exécutés",      "#6B778C", ""),
        ("🤖", snap.automated,        "Automatisés",       "#6554C0", ""),
    ]
    cells = "".join(_kpi_card(*r) for r in rows)

    rate_rows = [
        ("Taux de succès",    snap.success_rate,    trend("success_rate",    snap.success_rate)),
        ("Taux d'exécution",  snap.execution_rate,  trend("execution_rate",  snap.execution_rate)),
        ("Couverture",        snap.coverage_rate,   trend("coverage_rate",   snap.coverage_rate)),
        ("Automatisation",    snap.automation_rate, trend("automation_rate", snap.automation_rate)),
    ]
    rate_html = ""
    for label, rate, t in rate_rows:
        c = _color_rate(rate)
        rate_html += f"""<tr>
  <td style="padding:7px;border:1px solid #DFE1E6;">{label}</td>
  <td style="padding:7px;border:1px solid #DFE1E6;text-align:center;font-weight:bold;color:{c};">{rate}%{t}</td>
</tr>"""

    return f"""<h2>🧪 KPIs Tests</h2>
<table style="width:100%;border-collapse:collapse;margin-bottom:12px;"><tbody>
<tr>{cells}</tr></tbody></table>
<table style="width:55%;border-collapse:collapse;margin-bottom:20px;">
  <thead><tr style="background:#F4F5F7;">
    <th style="padding:7px;border:1px solid #DFE1E6;text-align:left;">Indicateur</th>
    <th style="padding:7px;border:1px solid #DFE1E6;text-align:center;">Valeur</th>
  </tr></thead>
  <tbody>{rate_html}</tbody>
</table>"""


def _build_bug_kpi_table(snap: KpiSnapshot, prev: Optional[dict]) -> str:
    def trend(key: str, current: float) -> str:
        return _trend_arrow(current, float(prev.get(key, current))) if prev else ""

    bug_cards = [
        ("🐛", snap.bugs_open,     "Bugs Ouverts",    "#DE350B", ""),
        ("🔥", snap.bugs_critical, "Critiques/Bloquants", "#FF5630", ""),
        ("✅", snap.bugs_resolved, "Résolus (30j)",   "#00875A", ""),
        ("📉", snap.bug_density,   "Densité / 100 tests", "#FF991F", ""),
    ]
    cells = "".join(_kpi_card(*r) for r in bug_cards)

    res_rate = snap.resolution_rate
    res_color = _color_rate(res_rate)

    return f"""<h2>🐛 KPIs Bugs</h2>
<table style="width:100%;border-collapse:collapse;margin-bottom:12px;"><tbody>
<tr>{cells}</tr></tbody></table>
<table style="width:55%;border-collapse:collapse;margin-bottom:20px;">
  <thead><tr style="background:#F4F5F7;">
    <th style="padding:7px;border:1px solid #DFE1E6;text-align:left;">Indicateur</th>
    <th style="padding:7px;border:1px solid #DFE1E6;text-align:center;">Valeur</th>
  </tr></thead>
  <tbody>
    <tr>
      <td style="padding:7px;border:1px solid #DFE1E6;">Taux de résolution</td>
      <td style="padding:7px;border:1px solid #DFE1E6;text-align:center;font-weight:bold;color:{res_color};">{res_rate}%{trend("resolution_rate",res_rate)}</td>
    </tr>
    <tr>
      <td style="padding:7px;border:1px solid #DFE1E6;">Densité bugs / 100 tests</td>
      <td style="padding:7px;border:1px solid #DFE1E6;text-align:center;font-weight:bold;">{snap.bug_density}{trend("bug_density",snap.bug_density)}</td>
    </tr>
  </tbody>
</table>"""


def _build_issues_table(issues: list[IssueDetail], title: str, jira_url: str) -> str:
    if not issues:
        return f"<p><em>Aucun ticket pour : {title}</em></p>"
    rows = ""
    for iss in issues[:MAX_ISSUES_TABLE]:
        priority_color = {
            "Blocker": "#DE350B", "Critical": "#FF5630",
            "Major": "#FF991F",   "Minor": "#00875A",
        }.get(iss.priority, "#6B778C")
        rows += f"""<tr>
  <td style="padding:6px;border:1px solid #DFE1E6;">
    <a href="{jira_url}/browse/{iss.key}">{iss.key}</a>
  </td>
  <td style="padding:6px;border:1px solid #DFE1E6;">{iss.summary}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;">{iss.status}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;color:{priority_color};font-weight:bold;">{iss.priority}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;">{iss.assignee}</td>
</tr>"""
    return f"""<h3>{title}</h3>
<table style="width:100%;border-collapse:collapse;font-size:12px;">
  <thead><tr style="background:#F4F5F7;">
    <th style="padding:6px;border:1px solid #DFE1E6;">Clé</th>
    <th style="padding:6px;border:1px solid #DFE1E6;">Résumé</th>
    <th style="padding:6px;border:1px solid #DFE1E6;">Statut</th>
    <th style="padding:6px;border:1px solid #DFE1E6;">Priorité</th>
    <th style="padding:6px;border:1px solid #DFE1E6;">Assigné</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>"""


def _build_history_table(history: list[dict]) -> str:
    if not history:
        return "<p><em>Aucun historique disponible — premier bilan enregistré.</em></p>"
    rows = ""
    for h in history:
        m = h["meta"]
        sr = float(m.get("success_rate", 0))
        rows += f"""<tr>
  <td style="padding:6px;border:1px solid #DFE1E6;">{m.get("sprint_name","?")}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;">{m.get("date_iso","?")}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;text-align:center;">{m.get("total_tests","?")}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;text-align:center;color:{_color_rate(sr)};font-weight:bold;">{sr}%</td>
  <td style="padding:6px;border:1px solid #DFE1E6;text-align:center;">{m.get("coverage_rate","?")}%</td>
  <td style="padding:6px;border:1px solid #DFE1E6;text-align:center;">{m.get("bugs_open","?")}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;text-align:center;">{m.get("bug_density","?")}</td>
</tr>"""
    return f"""<h2>📅 Historique des Bilans (Vector DB)</h2>
<table style="width:100%;border-collapse:collapse;font-size:12px;">
  <thead><tr style="background:#F4F5F7;">
    <th style="padding:6px;border:1px solid #DFE1E6;">Sprint</th>
    <th style="padding:6px;border:1px solid #DFE1E6;">Date</th>
    <th style="padding:6px;border:1px solid #DFE1E6;text-align:center;">Total Tests</th>
    <th style="padding:6px;border:1px solid #DFE1E6;text-align:center;">Taux Succès</th>
    <th style="padding:6px;border:1px solid #DFE1E6;text-align:center;">Couverture</th>
    <th style="padding:6px;border:1px solid #DFE1E6;text-align:center;">Bugs Ouverts</th>
    <th style="padding:6px;border:1px solid #DFE1E6;text-align:center;">Densité Bugs</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>"""


def _build_jql_reference(config: Config, snap: KpiSnapshot) -> str:
    rows = [
        ("Tous les tests",    config.jql_all_tests,       snap.total_tests),
        ("Passés",            config.jql_passed,          snap.passed),
        ("Échoués",           config.jql_failed,          snap.failed),
        ("Bloqués",           config.jql_blocked,         snap.blocked),
        ("Non exécutés",      config.jql_not_executed,    snap.not_executed),
        ("Automatisés",       config.jql_tests_automated, snap.automated),
        ("Bugs ouverts",      config.jql_bugs_open,       snap.bugs_open),
        ("Bugs critiques",    config.jql_bugs_critical,   snap.bugs_critical),
        ("Bugs résolus",      config.jql_bugs_resolved,   snap.bugs_resolved),
    ]
    html_rows = ""
    for label, jql, count in rows:
        html_rows += f"""<tr>
  <td style="padding:6px;border:1px solid #DFE1E6;">{label}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;font-family:monospace;font-size:11px;">{jql}</td>
  <td style="padding:6px;border:1px solid #DFE1E6;text-align:center;font-weight:bold;">{count}</td>
</tr>"""
    return f"""<h2>📋 Référence JQL</h2>
<table style="width:100%;border-collapse:collapse;font-size:11px;">
  <thead><tr style="background:#F4F5F7;">
    <th style="padding:6px;border:1px solid #DFE1E6;">Indicateur</th>
    <th style="padding:6px;border:1px solid #DFE1E6;">Requête JQL</th>
    <th style="padding:6px;border:1px solid #DFE1E6;text-align:center;">Résultat</th>
  </tr></thead>
  <tbody>{html_rows}</tbody>
</table>"""


def build_confluence_page(
    config: Config,
    snap: KpiSnapshot,
    data: JiraData,
    history: list[dict],
    ai_analysis: str,
) -> str:
    """Assemble la page Confluence complète en Storage Format XHTML."""
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")
    prev = history[0]["meta"] if history else None

    test_kpi_html    = _build_test_kpi_table(snap, prev)
    bug_kpi_html     = _build_bug_kpi_table(snap, prev)
    failed_html      = _build_issues_table(data.failed, "Tests Échoués ❌", config.jira_url)
    blocked_html     = _build_issues_table(data.blocked, "Tests Bloqués ⛔", config.jira_url)
    crit_bugs_html   = _build_issues_table(data.bugs_critical, "Bugs Critiques / Bloquants 🔥", config.jira_url)
    history_html     = _build_history_table(history)
    jql_ref_html     = _build_jql_reference(config, snap)

    return f"""<h1>📊 Bilan Test — {config.sprint_name}</h1>
<p style="color:#6B778C;font-size:12px;">
  Généré automatiquement le <strong>{now}</strong> · Projet : <strong>{config.project_key}</strong>
  · Espace : <strong>{config.space_key}</strong>
  · Modèle IA : <em>{config.claude_model}</em>
</p>
<hr/>

{test_kpi_html}
{bug_kpi_html}

<h2>🤖 Analyse IA (RAG — {len(history)} bilans historiques)</h2>
<div style="background:#F4F5F7;border-left:4px solid #0052CC;padding:12px 16px;margin-bottom:20px;">
{ai_analysis}
</div>

<h2>🔍 Détail des Anomalies</h2>
{failed_html}
{blocked_html}
{crit_bugs_html}

{history_html}
{jql_ref_html}
"""


# ── 6. Publication Confluence ──────────────────────────────────────────────────

def _get_existing_page(session: requests.Session, config: Config) -> Optional[dict]:
    url = f"{config.confluence_url}/rest/api/content"
    params = {"title": config.page_title, "spaceKey": config.space_key, "expand": "version"}
    resp = _request_with_retry(session, "GET", url, params=params, timeout=30)
    if resp.status_code != 200:
        return None
    results = resp.json().get("results", [])
    return results[0] if results else None


def _create_page(session: requests.Session, config: Config, content: str) -> dict:
    url = f"{config.confluence_url}/rest/api/content"
    body: dict = {
        "type": "page",
        "title": config.page_title,
        "space": {"key": config.space_key},
        "body": {"storage": {"value": content, "representation": "storage"}},
    }
    if config.parent_page_id:
        body["ancestors"] = [{"id": config.parent_page_id}]
    resp = _request_with_retry(session, "POST", url, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _update_page(session: requests.Session, config: Config, page: dict, content: str) -> dict:
    page_id = page["id"]
    version = page["version"]["number"]
    url = f"{config.confluence_url}/rest/api/content/{page_id}"
    body = {
        "id": page_id,
        "type": "page",
        "title": config.page_title,
        "space": {"key": config.space_key},
        "version": {"number": version + 1},
        "body": {"storage": {"value": content, "representation": "storage"}},
    }
    if config.parent_page_id:
        body["ancestors"] = [{"id": config.parent_page_id}]
    resp = _request_with_retry(session, "PUT", url, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def publish_to_confluence(config: Config, content: str) -> str:
    session = _confluence_session(config)
    existing = _get_existing_page(session, config)
    if existing:
        print(f"  📝 Mise à jour de la page existante (ID: {existing['id']})…")
        result = _update_page(session, config, existing, content)
        action = "mise à jour"
    else:
        print("  ✨ Création d'une nouvelle page…")
        result = _create_page(session, config, content)
        action = "créée"

    page_id = result.get("id", "")
    page_url = f"{config.confluence_url}/pages/viewpage.action?pageId={page_id}"
    print(f"  ✅ Page {action} : {page_url}")
    return page_url


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 65)
    print("📊  Bilan Confluence Test — Générateur RAG + Vector DB")
    print("=" * 65)

    # ── Étape 1 : Configuration
    print("\n[1/6] Chargement de la configuration…")
    config = Config.from_env()
    config.validate()
    print(f"  ✅ Projet     : {config.project_key}")
    print(f"  ✅ Sprint     : {config.sprint_name}")
    print(f"  ✅ Jira URL   : {config.jira_url}")
    print(f"  ✅ Confluence : {config.confluence_url}")
    print(f"  ✅ Espace     : {config.space_key}")
    print(f"  ✅ Modèle IA  : {config.claude_model}")
    print(f"  ✅ ChromaDB   : {config.chroma_persist_dir}")

    # ── Étape 2 : Collecte Jira
    print("\n[2/6] Collecte des données Jira (JQL)…")
    jira_data = collect_jira_data(config)

    # ── Étape 3 : Calcul KPIs
    print("\n[3/6] Calcul des KPIs…")
    snapshot = compute_kpis(config, jira_data)
    print(f"  ✅ Tests       : {snapshot.total_tests} total · {snapshot.passed} passés · {snapshot.failed} échoués")
    print(f"  ✅ Taux succès : {snapshot.success_rate}% · Couverture : {snapshot.coverage_rate}%")
    print(f"  ✅ Automatisés : {snapshot.automated} ({snapshot.automation_rate}%)")
    print(f"  ✅ Bugs        : {snapshot.bugs_open} ouverts · {snapshot.bugs_critical} critiques · {snapshot.bugs_resolved} résolus")
    print(f"  ✅ Densité bugs: {snapshot.bug_density} / 100 tests · Résolution : {snapshot.resolution_rate}%")

    # ── Étape 4 : ChromaDB — Récupération historique + stockage
    print("\n[4/6] Vector DB (ChromaDB) — Recherche historique…")
    collection = get_chroma_collection(config)
    history = retrieve_history(collection, snapshot)
    print(f"  📚 {len(history)} snapshot(s) historique(s) récupéré(s)")
    store_snapshot(collection, snapshot)

    # ── Étape 5 : RAG — Analyse Claude
    print("\n[5/6] Génération de l'analyse IA (RAG)…")
    ai_analysis = generate_rag_analysis(config, snapshot, history)
    print(f"  ✅ Analyse générée ({len(ai_analysis)} caractères)")

    # ── Étape 6 : Publication Confluence
    print("\n[6/6] Publication sur Confluence…")
    page_content = build_confluence_page(config, snapshot, jira_data, history, ai_analysis)
    page_url = publish_to_confluence(config, page_content)

    print("\n" + "=" * 65)
    print(f"✅  Bilan RAG publié avec succès !")
    print(f"🔗  {page_url}")
    print("=" * 65)


if __name__ == "__main__":
    main()
