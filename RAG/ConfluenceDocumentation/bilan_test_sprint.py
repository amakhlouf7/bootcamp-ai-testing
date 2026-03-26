"""Génère une page Confluence "Bilan Test Sprint" avec des KPIs extraits via JQL Jira.

Workflow :
  1. Lire les variables JQL depuis le fichier .env.
  2. Interroger l'API Jira avec chaque JQL pour récupérer les tickets.
  3. Calculer les KPIs : total, réussis, échoués, bloqués, non exécutés, taux de succès.
  4. Construire le contenu Confluence en Storage Format (XHTML).
  5. Créer ou mettre à jour la page Confluence.

Usage :
  python RAG/ConfluenceDocumentation/bilan_test_sprint.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

PAGE_TITLE = os.getenv("SPRINT_REPORT_PAGE_TITLE", "Bilan Test Sprint")
MAX_RETRIES = 5
INITIAL_WAIT = 10

# Statuts Jira considérés comme "réussi", "échoué", "bloqué", "non exécuté"
PASS_STATUSES = {"pass", "passed", "réussi", "success"}
FAIL_STATUSES = {"fail", "failed", "échoué", "failure"}
BLOCKED_STATUSES = {"blocked", "bloqué", "in progress"}
NOT_EXECUTED_STATUSES = {"to do", "open", "non exécuté", "not executed", "new"}


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class Config:
    jira_url: str
    jira_token: str
    confluence_url: str
    confluence_token: str
    space_key: str
    project_key: str
    sprint_name: str
    jql_all_tests: str
    jql_passed: str
    jql_failed: str
    jql_blocked: str
    jql_not_executed: str
    parent_page_id: Optional[str]
    page_title: str = PAGE_TITLE

    @classmethod
    def from_env(cls) -> "Config":
        project_key = os.getenv("PROJECT_KEY", "")
        sprint_name = os.getenv("SPRINT_NAME", "Sprint Courant")

        # JQL par défaut basé sur le PROJECT_KEY — peut être surchargé dans .env
        default_all = f'project = "{project_key}" AND issuetype = Test ORDER BY created DESC'
        default_passed = f'project = "{project_key}" AND issuetype = Test AND status in ("Pass","Passed") ORDER BY created DESC'
        default_failed = f'project = "{project_key}" AND issuetype = Test AND status in ("Fail","Failed") ORDER BY created DESC'
        default_blocked = f'project = "{project_key}" AND issuetype = Test AND status = "Blocked" ORDER BY created DESC'
        default_not_exec = f'project = "{project_key}" AND issuetype = Test AND status in ("To Do","Open") ORDER BY created DESC'

        return cls(
            jira_url=os.getenv("JIRA_URL", "").rstrip("/"),
            jira_token=os.getenv("JIRA_TOKEN", ""),
            confluence_url=os.getenv("CONFLUENCE_URL", "").rstrip("/"),
            confluence_token=os.getenv("CONFLUENCE_TOKEN", ""),
            space_key=os.getenv("CONFLUENCE_SPACE_KEY") or project_key,
            project_key=project_key,
            sprint_name=sprint_name,
            jql_all_tests=os.getenv("JQL_ALL_TESTS", default_all),
            jql_passed=os.getenv("JQL_PASSED", default_passed),
            jql_failed=os.getenv("JQL_FAILED", default_failed),
            jql_blocked=os.getenv("JQL_BLOCKED", default_blocked),
            jql_not_executed=os.getenv("JQL_NOT_EXECUTED", default_not_exec),
            parent_page_id=os.getenv("CONFLUENCE_PARENT_PAGE_ID") or None,
        )

    def validate(self) -> None:
        errors = []
        if not self.jira_url:
            errors.append("JIRA_URL manquant dans .env")
        if not self.jira_token:
            errors.append("JIRA_TOKEN manquant dans .env")
        if not self.confluence_url:
            errors.append("CONFLUENCE_URL manquant dans .env")
        if not self.confluence_token:
            errors.append("CONFLUENCE_TOKEN manquant dans .env")
        if not self.project_key:
            errors.append("PROJECT_KEY manquant dans .env")
        if errors:
            for e in errors:
                print(f"❌ ERREUR : {e}")
            sys.exit(1)


@dataclass
class KpiResult:
    label: str
    jql: str
    count: int = 0
    issues: list[dict] = field(default_factory=list)


@dataclass
class SprintKpis:
    total: KpiResult
    passed: KpiResult
    failed: KpiResult
    blocked: KpiResult
    not_executed: KpiResult

    @property
    def success_rate(self) -> float:
        if self.total.count == 0:
            return 0.0
        return round(self.passed.count / self.total.count * 100, 1)

    @property
    def execution_rate(self) -> float:
        executed = self.passed.count + self.failed.count + self.blocked.count
        if self.total.count == 0:
            return 0.0
        return round(executed / self.total.count * 100, 1)


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _jira_session(config: Config) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {config.jira_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    return session


def _confluence_session(config: Config) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {config.confluence_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    return session


def _request_with_retry(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    for attempt in range(1, MAX_RETRIES + 1):
        resp = session.request(method, url, **kwargs)
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = min(int(retry_after), 120) if retry_after else INITIAL_WAIT * (2 ** (attempt - 1))
        except (ValueError, OverflowError):
            wait = INITIAL_WAIT * (2 ** (attempt - 1))
        print(f"  ⏳ Rate limit (429) - tentative {attempt}/{MAX_RETRIES}, attente {wait}s…")
        time.sleep(wait)
    return resp  # type: ignore[return-value]


# ── Jira JQL queries ───────────────────────────────────────────────────────────

def _run_jql(session: requests.Session, jira_url: str, jql: str, max_results: int = 200) -> list[dict]:
    """Exécute un JQL et retourne la liste des issues."""
    url = f"{jira_url}/rest/api/2/search"
    params = {
        "jql": jql,
        "maxResults": max_results,
        "fields": "summary,status,assignee,priority,issuetype,labels",
    }
    resp = _request_with_retry(session, "GET", url, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"  ⚠️  JQL échoué ({resp.status_code}) : {jql[:80]}…")
        print(f"     Réponse : {resp.text[:200]}")
        return []
    data = resp.json()
    return data.get("issues", [])


def fetch_all_kpis(config: Config) -> SprintKpis:
    """Récupère les KPIs pour chaque JQL défini dans la configuration."""
    session = _jira_session(config)

    definitions = [
        ("Total Tests",     config.jql_all_tests),
        ("Passés ✅",        config.jql_passed),
        ("Échoués ❌",        config.jql_failed),
        ("Bloqués ⛔",       config.jql_blocked),
        ("Non exécutés ⬜", config.jql_not_executed),
    ]

    results: list[KpiResult] = []
    for label, jql in definitions:
        print(f"  🔍 JQL [{label}] : {jql[:80]}…")
        issues = _run_jql(session, config.jira_url, jql)
        results.append(KpiResult(label=label, jql=jql, count=len(issues), issues=issues))
        print(f"      → {len(issues)} issue(s) trouvée(s)")

    return SprintKpis(
        total=results[0],
        passed=results[1],
        failed=results[2],
        blocked=results[3],
        not_executed=results[4],
    )


# ── HTML / Confluence Storage Format ──────────────────────────────────────────

def _color_rate(rate: float) -> str:
    """Retourne une couleur Confluence selon le taux."""
    if rate >= 80:
        return "#00875A"   # vert
    if rate >= 50:
        return "#FF991F"   # orange
    return "#DE350B"       # rouge


def _status_badge(status: str) -> str:
    lower = status.lower()
    if any(s in lower for s in PASS_STATUSES):
        color, text = "#00875A", status
    elif any(s in lower for s in FAIL_STATUSES):
        color, text = "#DE350B", status
    elif any(s in lower for s in BLOCKED_STATUSES):
        color, text = "#FF991F", status
    else:
        color, text = "#6B778C", status
    return f'<ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Neutral</ac:parameter><ac:parameter ac:name="title">{text}</ac:parameter></ac:structured-macro>'


def _build_kpi_table(kpis: SprintKpis) -> str:
    rows = [
        ("Total Tests",      kpis.total.count,        "#0052CC", "🔢"),
        ("Passés",           kpis.passed.count,       "#00875A", "✅"),
        ("Échoués",          kpis.failed.count,       "#DE350B", "❌"),
        ("Bloqués",          kpis.blocked.count,      "#FF991F", "⛔"),
        ("Non exécutés",     kpis.not_executed.count, "#6B778C", "⬜"),
    ]
    cells = ""
    for label, count, color, icon in rows:
        cells += f"""
        <td style="text-align:center; padding:12px; border:1px solid #DFE1E6;">
          <p style="font-size:28px; font-weight:bold; color:{color}; margin:0;">{icon} {count}</p>
          <p style="font-size:12px; color:#6B778C; margin:4px 0 0 0;">{label}</p>
        </td>"""

    return f"""<table style="width:100%; border-collapse:collapse; margin-bottom:16px;">
  <tbody><tr>{cells}
  </tr></tbody>
</table>"""


def _build_rate_table(kpis: SprintKpis) -> str:
    success_color = _color_rate(kpis.success_rate)
    exec_color = _color_rate(kpis.execution_rate)
    return f"""<table style="width:60%; border-collapse:collapse; margin-bottom:16px;">
  <thead>
    <tr style="background:#F4F5F7;">
      <th style="padding:8px; border:1px solid #DFE1E6; text-align:left;">Indicateur</th>
      <th style="padding:8px; border:1px solid #DFE1E6; text-align:center;">Valeur</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="padding:8px; border:1px solid #DFE1E6;">Taux de succès</td>
      <td style="padding:8px; border:1px solid #DFE1E6; text-align:center; font-weight:bold; color:{success_color};">{kpis.success_rate}%</td>
    </tr>
    <tr>
      <td style="padding:8px; border:1px solid #DFE1E6;">Taux d'exécution</td>
      <td style="padding:8px; border:1px solid #DFE1E6; text-align:center; font-weight:bold; color:{exec_color};">{kpis.execution_rate}%</td>
    </tr>
  </tbody>
</table>"""


def _build_issues_table(issues: list[dict], title: str) -> str:
    if not issues:
        return f"<p><em>Aucun ticket pour : {title}</em></p>"

    rows = ""
    for issue in issues[:50]:  # limiter l'affichage à 50 lignes
        key = issue.get("key", "")
        summary = issue.get("fields", {}).get("summary", "")[:100]
        status = issue.get("fields", {}).get("status", {}).get("name", "")
        priority = issue.get("fields", {}).get("priority", {}).get("name", "") if issue.get("fields", {}).get("priority") else ""
        assignee_obj = issue.get("fields", {}).get("assignee") or {}
        assignee = assignee_obj.get("displayName", "Non assigné")
        rows += f"""
    <tr>
      <td style="padding:6px; border:1px solid #DFE1E6;"><a href="#">{key}</a></td>
      <td style="padding:6px; border:1px solid #DFE1E6;">{summary}</td>
      <td style="padding:6px; border:1px solid #DFE1E6;">{status}</td>
      <td style="padding:6px; border:1px solid #DFE1E6;">{priority}</td>
      <td style="padding:6px; border:1px solid #DFE1E6;">{assignee}</td>
    </tr>"""

    return f"""<h3>{title}</h3>
<table style="width:100%; border-collapse:collapse; font-size:13px;">
  <thead>
    <tr style="background:#F4F5F7;">
      <th style="padding:6px; border:1px solid #DFE1E6;">Clé</th>
      <th style="padding:6px; border:1px solid #DFE1E6;">Résumé</th>
      <th style="padding:6px; border:1px solid #DFE1E6;">Statut</th>
      <th style="padding:6px; border:1px solid #DFE1E6;">Priorité</th>
      <th style="padding:6px; border:1px solid #DFE1E6;">Assigné</th>
    </tr>
  </thead>
  <tbody>{rows}
  </tbody>
</table>"""


def _build_page_content(config: Config, kpis: SprintKpis) -> str:
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")
    kpi_table = _build_kpi_table(kpis)
    rate_table = _build_rate_table(kpis)
    failed_table = _build_issues_table(kpis.failed.issues, "Tests Échoués ❌")
    blocked_table = _build_issues_table(kpis.blocked.issues, "Tests Bloqués ⛔")

    return f"""<h1>📊 Bilan Test Sprint — {config.sprint_name}</h1>
<p style="color:#6B778C; font-size:12px;">Généré automatiquement le {now} · Projet : <strong>{config.project_key}</strong></p>
<hr/>

<h2>🔢 KPIs Globaux</h2>
{kpi_table}

<h2>📈 Taux</h2>
{rate_table}

<h2>🔍 Détail par Statut</h2>
{failed_table}
{blocked_table}

<h2>📋 JQL Utilisés</h2>
<table style="width:100%; border-collapse:collapse; font-size:12px;">
  <thead>
    <tr style="background:#F4F5F7;">
      <th style="padding:6px; border:1px solid #DFE1E6;">Indicateur</th>
      <th style="padding:6px; border:1px solid #DFE1E6;">JQL</th>
      <th style="padding:6px; border:1px solid #DFE1E6;">Résultat</th>
    </tr>
  </thead>
  <tbody>
    <tr><td style="padding:6px; border:1px solid #DFE1E6;">Total</td><td style="padding:6px; border:1px solid #DFE1E6; font-family:monospace;">{config.jql_all_tests}</td><td style="padding:6px; border:1px solid #DFE1E6;">{kpis.total.count}</td></tr>
    <tr><td style="padding:6px; border:1px solid #DFE1E6;">Passés</td><td style="padding:6px; border:1px solid #DFE1E6; font-family:monospace;">{config.jql_passed}</td><td style="padding:6px; border:1px solid #DFE1E6;">{kpis.passed.count}</td></tr>
    <tr><td style="padding:6px; border:1px solid #DFE1E6;">Échoués</td><td style="padding:6px; border:1px solid #DFE1E6; font-family:monospace;">{config.jql_failed}</td><td style="padding:6px; border:1px solid #DFE1E6;">{kpis.failed.count}</td></tr>
    <tr><td style="padding:6px; border:1px solid #DFE1E6;">Bloqués</td><td style="padding:6px; border:1px solid #DFE1E6; font-family:monospace;">{config.jql_blocked}</td><td style="padding:6px; border:1px solid #DFE1E6;">{kpis.blocked.count}</td></tr>
    <tr><td style="padding:6px; border:1px solid #DFE1E6;">Non exécutés</td><td style="padding:6px; border:1px solid #DFE1E6; font-family:monospace;">{config.jql_not_executed}</td><td style="padding:6px; border:1px solid #DFE1E6;">{kpis.not_executed.count}</td></tr>
  </tbody>
</table>
"""


# ── Confluence API ─────────────────────────────────────────────────────────────

def _get_existing_page(session: requests.Session, config: Config) -> Optional[dict]:
    """Recherche une page existante par titre dans l'espace donné."""
    url = f"{config.confluence_url}/rest/api/content"
    params = {"title": config.page_title, "spaceKey": config.space_key, "expand": "version,body.storage"}
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
    current_version = page["version"]["number"]
    url = f"{config.confluence_url}/rest/api/content/{page_id}"
    body = {
        "id": page_id,
        "type": "page",
        "title": config.page_title,
        "space": {"key": config.space_key},
        "version": {"number": current_version + 1},
        "body": {"storage": {"value": content, "representation": "storage"}},
    }
    resp = _request_with_retry(session, "PUT", url, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def publish_page(config: Config, content: str) -> str:
    """Crée ou met à jour la page Confluence. Retourne l'URL de la page."""
    session = _confluence_session(config)
    existing = _get_existing_page(session, config)

    if existing:
        print(f"  📝 Mise à jour de la page existante (ID: {existing['id']})…")
        result = _update_page(session, config, existing, content)
        action = "mise à jour"
    else:
        print(f"  ✨ Création d'une nouvelle page…")
        result = _create_page(session, config, content)
        action = "créée"

    page_id = result.get("id", "")
    page_url = f"{config.confluence_url}/pages/viewpage.action?pageId={page_id}"
    print(f"  ✅ Page {action} : {page_url}")
    return page_url


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("📊  Bilan Test Sprint — Générateur Confluence")
    print("=" * 60)

    print("\n[1/3] Chargement de la configuration…")
    config = Config.from_env()
    config.validate()
    print(f"  ✅ Projet     : {config.project_key}")
    print(f"  ✅ Sprint     : {config.sprint_name}")
    print(f"  ✅ Jira URL   : {config.jira_url}")
    print(f"  ✅ Confluence : {config.confluence_url}")
    print(f"  ✅ Espace     : {config.space_key}")
    print(f"  ✅ Titre page : {config.page_title}")

    print("\n[2/3] Récupération des KPIs via JQL…")
    kpis = fetch_all_kpis(config)
    print(f"\n  📊 Résumé :")
    print(f"     Total          : {kpis.total.count}")
    print(f"     Passés         : {kpis.passed.count}")
    print(f"     Échoués        : {kpis.failed.count}")
    print(f"     Bloqués        : {kpis.blocked.count}")
    print(f"     Non exécutés   : {kpis.not_executed.count}")
    print(f"     Taux de succès : {kpis.success_rate}%")
    print(f"     Taux d'exéc.   : {kpis.execution_rate}%")

    print("\n[3/3] Publication sur Confluence…")
    content = _build_page_content(config, kpis)
    page_url = publish_page(config, content)

    print("\n" + "=" * 60)
    print(f"✅  Bilan généré avec succès : {page_url}")
    print("=" * 60)


if __name__ == "__main__":
    main()
