"""Auto-healing for flaky tests using RCA report and LLMProxy.

Usage examples:
  python LLMProxy/autoHealing/auto_heal_flaky.py --rca-report output/rca_report_latest.json
  python LLMProxy/autoHealing/auto_heal_flaky.py --rca-report output/rca_report_latest.json --apply --label-flaky-jira
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from LLMProxy.common import call_llm_json, jira_add_comment, jira_add_label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-heal flaky tests from RCA output")
    parser.add_argument("--rca-report", required=True, help="Path to RCA report JSON")
    parser.add_argument("--output", default="", help="Optional output JSON path")
    parser.add_argument("--apply", action="store_true", help="Overwrite flaky test files with healed versions")
    parser.add_argument("--label-flaky-jira", action="store_true", help="Add Flaky Tests label/comment to Jira issue when issue_key is present")
    parser.add_argument("--max-file-size-kb", type=int, default=256, help="Skip files larger than this size")
    return parser.parse_args()


def extract_json_from_markdown(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def request_healed_source(item: Dict[str, Any], source_code: str) -> Dict[str, Any]:
    system_prompt = (
        "You are a test auto-healing engine. "
        "Return only JSON with keys: healed_source, patch_summary, confidence, notes. "
        "Preserve test intent and apply minimal changes to reduce flaky behavior."
    )
    user_prompt = json.dumps(
        {
            "task": "Heal flaky automated test",
            "framework": item.get("framework"),
            "file": item.get("file"),
            "line": item.get("line"),
            "message": item.get("message"),
            "trace": item.get("trace"),
            "suggested_fix": item.get("suggested_fix"),
            "source_code": source_code,
        },
        ensure_ascii=False,
    )
    return call_llm_json(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.0)


def maybe_validate_python(file_path: Path, content: str) -> str:
    if file_path.suffix.lower() != ".py":
        return "skipped"

    import ast

    try:
        ast.parse(content)
        return "ok"
    except SyntaxError as exc:
        return f"invalid_python: {exc}"


def save_backup(path: Path) -> Path:
    stamp = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def apply_heal(item: Dict[str, Any], apply_changes: bool, max_file_size_kb: int) -> Dict[str, Any]:
    file_raw = str(item.get("file") or "").strip()
    if not file_raw:
        return {"status": "skipped", "reason": "missing file path"}

    file_path = Path(file_raw)
    if not file_path.exists():
        return {"status": "skipped", "reason": f"file not found: {file_path}"}

    if file_path.stat().st_size > max_file_size_kb * 1024:
        return {"status": "skipped", "reason": f"file too large (> {max_file_size_kb} KB)"}

    source = file_path.read_text(encoding="utf-8")

    llm = request_healed_source(item, source)
    healed_source = llm.get("healed_source")
    if not healed_source or not isinstance(healed_source, str):
        return {"status": "failed", "reason": "LLM did not return healed_source"}

    validation = maybe_validate_python(file_path, healed_source)
    if validation.startswith("invalid_python"):
        return {
            "status": "failed",
            "reason": validation,
            "patch_summary": llm.get("patch_summary", ""),
            "notes": llm.get("notes", ""),
        }

    result = {
        "status": "planned",
        "file": str(file_path),
        "patch_summary": llm.get("patch_summary", ""),
        "confidence": llm.get("confidence", 0.0),
        "notes": llm.get("notes", ""),
        "validation": validation,
    }

    if apply_changes:
        backup = save_backup(file_path)
        file_path.write_text(healed_source, encoding="utf-8")
        result["status"] = "applied"
        result["backup"] = str(backup)

    return result


def update_jira_for_flaky(item: Dict[str, Any], heal_result: Dict[str, Any]) -> Dict[str, Any]:
    issue_key = str(item.get("issue_key") or item.get("jira_issue") or "").strip()
    if not issue_key:
        return {"status": "skipped", "reason": "missing issue_key in RCA item"}

    comment = (
        "[test-healer] Flaky test detected\n"
        f"- Test: {item.get('name')}\n"
        f"- Cause: {item.get('root_cause')}\n"
        f"- Correction: {heal_result.get('patch_summary', 'N/A')}\n"
        f"- Status: {'Stabilized' if heal_result.get('status') in ('planned', 'applied') else 'Needs review'}"
    )

    jira_add_label(issue_key, "Flaky Tests")
    jira_add_comment(issue_key, comment)
    return {"status": "updated", "issue_key": issue_key}


def main() -> None:
    args = parse_args()
    rca_path = Path(args.rca_report)
    if not rca_path.exists():
        raise FileNotFoundError(f"RCA report not found: {rca_path}")

    payload = json.loads(rca_path.read_text(encoding="utf-8"))
    items = payload.get("items", [])

    flaky_items = [item for item in items if item.get("is_flaky")]

    results: List[Dict[str, Any]] = []
    for item in flaky_items:
        heal_result = apply_heal(item, apply_changes=args.apply, max_file_size_kb=args.max_file_size_kb)

        jira_result = None
        if args.label_flaky_jira:
            try:
                jira_result = update_jira_for_flaky(item, heal_result)
            except Exception as exc:
                jira_result = {"status": "failed", "reason": str(exc)}

        results.append(
            {
                "name": item.get("name"),
                "framework": item.get("framework"),
                "file": item.get("file"),
                "heal": heal_result,
                "jira": jira_result,
            }
        )

    summary = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "source_rca": str(rca_path),
        "flaky_total": len(flaky_items),
        "planned_or_applied": sum(1 for r in results if r["heal"].get("status") in {"planned", "applied"}),
        "applied": sum(1 for r in results if r["heal"].get("status") == "applied"),
        "failed": sum(1 for r in results if r["heal"].get("status") == "failed"),
        "results": results,
    }

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_path = Path("output") / f"flaky_healing_{stamp}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Auto-healing report: {out_path}")
    print(
        "Flaky items: {total} | Planned/Applied: {ok} | Applied: {applied} | Failed: {failed}".format(
            total=summary["flaky_total"],
            ok=summary["planned_or_applied"],
            applied=summary["applied"],
            failed=summary["failed"],
        )
    )


if __name__ == "__main__":
    main()
