---
name: manual-tests
description: "Generate and import manual test cases into Xray (Jira) from a User Story. Fetches the US from JIRA, generates 5-8 test cases with GitHub Copilot (Claude Opus 4), runs a quality review, saves as JSON/CSV, imports into Xray, and links each test to the source User Story. All connection values are read from .env."
argument-hint: "Optional: USER_STORY_KEY to override the value in .env (e.g. CTC2S-400)"
---

# Manual Testing — Xray Test Generator

## References

| Asset | Path |
|-------|------|
| Xray test case template | [`assets/xray-template.md`](./assets/xray-template.md) |
| JSON output example | [`templates/manual_tests_20251212_112820.json`](./templates/manual_tests_20251212_112820.json) |
| CSV output example | [`templates/manual_tests_20251212_112820.csv`](./templates/manual_tests_20251212_112820.csv) |
| Import script | [`scripts/import_manual_tests.py`](./scripts/import_manual_tests.py) |
| Test case guide | [`references/guide_cas_de_test.docx`](./references/guide_cas_de_test.docx) |

### Required Python packages
```
requests
python-dotenv
```

### Required `.env` variables
```env
JIRA_URL=https://your-jira-instance.com   # example — read from .env at runtime
JIRA_TOKEN=your_bearer_token              # example — read from .env at runtime
PROJECT_KEY=YOUR_PROJECT                  # example — read from .env at runtime
USER_STORY_KEY=YOUR_PROJECT-000           # example — read from .env at runtime
```

---

## Pipeline

```
1. FETCH      → GET /rest/api/2/issue/{USER_STORY_KEY}
2. GENERATE   → GitHub Copilot (Claude Opus 4) → JSON array of 5–8 test cases
3. REVIEW     → Quality checks (see checklist below)
4. SAVE       → output/manual_tests_{timestamp}.json + .csv
5. IMPORT     → POST /rest/api/2/issue  (issuetype: Test, customfields: 11209 + 11213)
6. TRANSITION → POST /rest/api/2/issue/{key}/transitions  (ID: 61 → In Review)
7. LINK       → POST /rest/api/2/issueLink  (type: "Tests")
               inwardIssue = test key  ("tested by")
               outwardIssue = story key  (from .env or CSV Issue key column)
```

---

## ✅ Pre-Generation Checklist

Run these checks **before** calling the LLM.

- [ ] `.env` is present and all 4 variables are set (`JIRA_URL`, `JIRA_TOKEN`, `PROJECT_KEY`, `USER_STORY_KEY`)
- [ ] User Story exists in JIRA and is reachable (`GET /rest/api/2/issue/{USER_STORY_KEY}` returns 200)
- [ ] User Story has a non-empty `summary` and `description`
- [ ] Description contains identifiable acceptance criteria (look for "AC", "Criteria", "Given/When/Then", or bullet lists)
- [ ] GitHub Copilot is active in VS Code with model set to **Claude Opus 4**
- [ ] `output/` directory exists (create it if missing)

---

## Generation Prompt

Inject `{story_content}` = `summary` + `description` fetched from JIRA.

```
Generate comprehensive manual test cases for the following user story:

{story_content}

Generate 5-8 test cases covering:
- Happy path scenarios
- Edge cases
- Error handling
- Validation

Return ONLY a valid JSON array — no markdown, no explanation:
[
  {
    "name": "Test Case Name",
    "description": "What this test covers",
    "steps": [
      {"step": "Action 1"},
      {"step": "Action 2"},
      {"step": "Action 3"}
    ],
    "result": "Expected outcome"
  }
]
```

Use [`assets/xray-template.md`](./assets/xray-template.md) and [`templates/manual_tests_20251212_112820.json`](./templates/manual_tests_20251212_112820.json) as style references.

---

## ✅ Post-Generation Checklist

Run these checks **after** the LLM returns the test cases. Fix and regenerate if any item fails.

### Coverage
- [ ] Every acceptance criterion from the User Story maps to at least one test case
- [ ] At least one happy-path test
- [ ] At least one negative/error test
- [ ] Boundary values covered if the US has numeric or length constraints

### Duplicates
- [ ] No two test cases have the same objective
- [ ] No two test cases share >80% of their steps — merge or remove redundant ones

### Quality per test case
- [ ] Each test has **at least 3 steps**
- [ ] Each step is a **single, atomic user action** (not "fill in the form")
- [ ] Concrete test data is specified where needed (values, emails, amounts…)
- [ ] Expected result is **specific and verifiable** (not "the test passes")
- [ ] Preconditions are stated when the starting state is non-obvious

### Format
- [ ] Output is a valid JSON array matching the structure above
- [ ] Each `steps` array contains `{"step": "..."}` objects
- [ ] `result` is a single string (not an array)

---

## Import

Run the existing import script after the post-generation checklist passes. The script auto-detects format by file extension.

```bash
# JSON input (USER_STORY_KEY from .env)
python scripts/import_manual_tests.py output/manual_tests_YYYYMMDD.json

# CSV input (USER_STORY_KEY read from the "Issue key" column — overrides .env)
python scripts/import_manual_tests.py output/manual_tests_YYYYMMDD.csv
```

The script (`scripts/import_manual_tests.py`) will:
1. Read config from `.env` (`JIRA_URL`, `JIRA_TOKEN`, `PROJECT_KEY`, `USER_STORY_KEY`)
2. If CSV: override `USER_STORY_KEY` with the `Issue key` column value
3. Create each test in Xray (`customfield_11209` = Manual, `customfield_11213` = steps)
4. Transition each test to **In Review** (`POST /transitions`, ID: `61`)
5. Link each test to the User Story — `inwardIssue = test key`, `outwardIssue = story key`

### Xray custom fields

| Field ID | Name | Value |
|----------|------|-------|
| `customfield_11209` | Test Type | `{"value": "Manual"}` |
| `customfield_11213` | Manual Test Steps | `{"steps": [...]}` |

---

## Error Reference

| HTTP | Context | Meaning | Fix |
|------|---------|---------|-----|
| `401` | Any | Invalid token | Check `JIRA_TOKEN` in `.env` |
| `403` | Any | No permission | Check project rights in JIRA |
| `404` | Fetch / Import | Issue not found | Check `USER_STORY_KEY` in `.env` or CSV |
| `404` | Link | Invalid link type name | Use `"Tests"` — not `"tested by"` or `"Tested by"` |
| `429` | Any | Rate limit | Add exponential backoff in script |
| `500` | Import | Bad payload | Validate JSON structure against template |
