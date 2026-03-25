"""RCA analyzer for failed tests using LLMProxy.

Usage examples:
  python LLMProxy/RCA/rca_analyzer.py --report output/junit.xml --format junit --create-jira-bugs
  python LLMProxy/RCA/rca_analyzer.py --report output/failures.json --format json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

from LLMProxy.common import call_llm_json, jira_create_bug


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze failed tests and generate RCA report")
    parser.add_argument("--report", required=True, help="Path to test report (JUnit XML or JSON)")
    parser.add_argument("--format", choices=["auto", "junit", "json"], default="auto")
    parser.add_argument("--output", default="", help="Optional output JSON path")
    parser.add_argument("--create-jira-bugs", action="store_true", help="Create Jira bugs for real defects")
    return parser.parse_args()


def detect_format(path: Path, explicit: str) -> str:
    if explicit != "auto":
        return explicit
    if path.suffix.lower() in {".xml", ".junit"}:
        return "junit"
    if path.suffix.lower() in {".json", ".jsonl"}:
        return "json"
    raise ValueError("Cannot detect report format, use --format junit|json")


def parse_junit(report_path: Path) -> List[Dict[str, Any]]:
    root = ET.parse(report_path).getroot()
    failures: List[Dict[str, Any]] = []

    for testcase in root.iter("testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        if failure is None and error is None:
            continue

        node = failure if failure is not None else error
        message = (node.attrib.get("message") or "").strip() if node is not None else ""
        trace = (node.text or "").strip() if node is not None else ""

        failures.append(
            {
                "name": testcase.attrib.get("name", "unknown_test"),
                "classname": testcase.attrib.get("classname", ""),
                "file": testcase.attrib.get("file", ""),
                "line": testcase.attrib.get("line", ""),
                "message": message,
                "trace": trace,
            }
        )

    return failures


def parse_json_report(report_path: Path) -> List[Dict[str, Any]]:
    with open(report_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        if "failures" in payload and isinstance(payload["failures"], list):
            candidates = payload["failures"]
        elif "tests" in payload and isinstance(payload["tests"], list):
            candidates = [item for item in payload["tests"] if item.get("status") == "failed"]
        else:
            raise ValueError("Unsupported JSON report schema")
    else:
        raise ValueError("Unsupported JSON report content")

    failures: List[Dict[str, Any]] = []
    for item in candidates:
        failures.append(
            {
                "name": item.get("name") or item.get("test") or "unknown_test",
                "classname": item.get("classname") or item.get("suite") or "",
                "file": item.get("file") or item.get("path") or "",
                "line": item.get("line") or "",
                "message": item.get("message") or item.get("error") or "",
                "trace": item.get("trace") or item.get("stack") or "",
            }
        )

    return failures


def infer_framework(test_file: str, classname: str) -> str:
    joined = f"{test_file} {classname}".lower()
    if ".robot" in joined:
        return "robot"
    if any(token in joined for token in ["cypress", ".cy.", ".spec.ts", ".spec.js"]):
        return "cypress"
    return "pytest"


def heuristic_classification(message: str, trace: str) -> Dict[str, Any]:
    blob = f"{message}\n{trace}".lower()
    flaky_signals = [
        "timeout",
        "timed out",
        "stale element",
        "detached",
        "network",
        "econnreset",
        "rate limit",
        "retry",
        "intermittent",
    ]
    is_flaky = any(signal in blob for signal in flaky_signals)

    if is_flaky:
        return {
            "category": "Flaky",
            "root_cause": "Likely unstable timing/network/async behavior",
            "confidence": 0.55,
            "priority": "Minor",
            "suggested_fix": "Replace fixed waits, synchronize on explicit conditions or intercepts, and isolate test data.",
        }

    return {
        "category": "Regression",
        "root_cause": "Likely real functional defect or assertion mismatch",
        "confidence": 0.5,
        "priority": "Major",
        "suggested_fix": "Validate app behavior against expected output and patch root functionality.",
    }


def llm_classification(failure: Dict[str, Any], framework: str) -> Dict[str, Any]:
    system_prompt = (
        "You are an expert QA RCA engine. "
        "Return only valid JSON with keys: category, root_cause, confidence, priority, suggested_fix. "
        "category must be one of: Flaky, Regression, TestCode, Environment, Data."
    )
    user_prompt = json.dumps(
        {
            "framework": framework,
            "failure": failure,
            "task": "Classify this failed test and provide RCA.",
        },
        ensure_ascii=False,
    )
    return call_llm_json(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.1)


def create_bug_for_defect(item: Dict[str, Any]) -> Dict[str, Any]:
    summary = f"[test-healer] {item['name']} - {item['category']}"
    description = (
        f"*Framework*: {item['framework']}\n"
        f"*Test*: {item['name']}\n"
        f"*File*: {item['file']}:{item['line']}\n"
        f"*Category*: {item['category']}\n"
        f"*Root cause*: {item['root_cause']}\n"
        f"*Message*: {item['message']}\n"
        f"*Trace*: {item['trace'][:5000]}\n"
        f"*Suggested fix*: {item['suggested_fix']}\n"
    )
    labels = ["test-healer", "regression", item["framework"]]

    jira_response = jira_create_bug(
        summary=summary,
        description=description,
        labels=labels,
        priority=item.get("priority") or "Major",
    )
    return jira_response


def clean_priority(value: str) -> str:
    normalized = (value or "").strip().lower()
    mapping = {
        "blocker": "Highest",
        "critical": "High",
        "major": "Medium",
        "minor": "Low",
        "trivial": "Lowest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }
    return mapping.get(normalized, "Medium")


def parse_line(line_raw: Any, trace: str) -> str:
    if line_raw:
        return str(line_raw)
    match = re.search(r":(\d+)(?::\d+)?", trace)
    return match.group(1) if match else ""


def main() -> None:
    args = parse_args()
    report_path = Path(args.report)
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")

    fmt = detect_format(report_path, args.format)
    failures = parse_junit(report_path) if fmt == "junit" else parse_json_report(report_path)

    report_items: List[Dict[str, Any]] = []
    for failure in failures:
        framework = infer_framework(failure.get("file", ""), failure.get("classname", ""))

        try:
            llm = llm_classification(failure, framework)
        except Exception:
            llm = heuristic_classification(failure.get("message", ""), failure.get("trace", ""))

        category = llm.get("category", "Regression")
        item = {
            "name": failure.get("name", "unknown_test"),
            "framework": framework,
            "file": failure.get("file", ""),
            "line": parse_line(failure.get("line"), failure.get("trace", "")),
            "message": failure.get("message", ""),
            "trace": failure.get("trace", ""),
            "category": category,
            "root_cause": llm.get("root_cause", "N/A"),
            "confidence": llm.get("confidence", 0.5),
            "priority": clean_priority(str(llm.get("priority", "Major"))),
            "suggested_fix": llm.get("suggested_fix", "N/A"),
            "is_flaky": str(category).lower() == "flaky",
            "jira_bug": None,
        }

        if args.create_jira_bugs and not item["is_flaky"]:
            try:
                jira = create_bug_for_defect(item)
                item["jira_bug"] = {
                    "key": jira.get("key"),
                    "id": jira.get("id"),
                    "self": jira.get("self"),
                }
            except Exception as exc:
                item["jira_bug"] = {"error": str(exc)}

        report_items.append(item)

    output = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "source_report": str(report_path),
        "total_failures": len(report_items),
        "flaky_count": sum(1 for item in report_items if item["is_flaky"]),
        "defect_count": sum(1 for item in report_items if not item["is_flaky"]),
        "items": report_items,
    }

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_path = Path("output") / f"rca_report_{stamp}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)

    print(f"RCA report created: {out_path}")
    print(f"Failures: {output['total_failures']} | Flaky: {output['flaky_count']} | Defects: {output['defect_count']}")


if __name__ == "__main__":
    main()
