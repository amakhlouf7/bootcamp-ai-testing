---
name: cucumber-tests
description: "Generate and import Cucumber/Gherkin BDD test scenarios into Xray (Jira) from a User Story. Fetches the US from JIRA, generates Given/When/Then scenarios with GitHub Copilot (Claude Opus 4), runs a quality review, saves as a .feature file, and imports into Xray via the Raven API. The @USER_STORY_KEY tag in the feature file links all scenarios to the source User Story automatically."
argument-hint: "Optional: USER_STORY_KEY to use as the feature tag (e.g. CTC2S-400). If omitted, set it in the .feature file manually."
---

# Cucumber Tests — Xray BDD Test Generator

## References

| Asset | Path |
|-------|------|
| Feature file example | [`references/cucumber_tests_20251212_115846.feature`](./references/cucumber_tests_20251212_115846.feature) |
| Import script | [`scripts/import_cucumber_tests.py`](./scripts/import_cucumber_tests.py) |

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
USER_STORY_KEY=YOUR_PROJECT-000           # used as @tag in the feature file
```

> `USER_STORY_KEY` is not read by the import script — it is embedded as a `@tag` in the `.feature` file header. Xray uses this tag to link all scenarios to the User Story automatically on import.

---

## Pipeline

```
1. FETCH   → GET /rest/api/2/issue/{USER_STORY_KEY}
2. GENERATE → GitHub Copilot (Claude Opus 4) → .feature file (Gherkin)
3. REVIEW  → Quality checks (see checklist below)
4. SAVE    → output/cucumber_tests_{timestamp}.feature
5. IMPORT  → POST /rest/raven/2.0/import/feature?projectKey={PROJECT_KEY}
             multipart file upload — @{USER_STORY_KEY} tag auto-links all scenarios
```

> No separate LINK or TRANSITION step — Xray handles both automatically via the `@tag` on import.

---

## ✅ Pre-Generation Checklist

Run these checks **before** calling the LLM.

- [ ] `.env` is present with `JIRA_URL`, `JIRA_TOKEN`, `PROJECT_KEY`, `USER_STORY_KEY`
- [ ] User Story exists in JIRA and is reachable (`GET /rest/api/2/issue/{USER_STORY_KEY}` returns 200)
- [ ] User Story has a non-empty `summary` and `description`
- [ ] Description contains identifiable acceptance criteria
- [ ] GitHub Copilot is active in VS Code with model set to **Claude Opus 4**
- [ ] `output/` directory exists (create it if missing)

---

## Generation Prompt

Inject `{story_content}` = `summary` + `description` fetched from JIRA.
Inject `{USER_STORY_KEY}` for the feature tag.

```
Generate Cucumber/Gherkin BDD test scenarios for the following user story:

{story_content}

Requirements:
- Tag the feature with @{USER_STORY_KEY} to link it to the User Story in Xray
- Generate 5-8 scenarios covering: happy path, negative cases, edge cases, validation
- Use declarative steps (describe WHAT, not HOW)
- Each scenario must have Given / When / Then steps
- Add the expected result as a comment on the last Then step: # Expected Result: ...
- Use Scenario Outline + Examples table for data-driven cases

Return ONLY valid Gherkin — no markdown fences, no explanation:

# language: en
@{USER_STORY_KEY}
Feature: <Feature name from the User Story>
  <One-line description>

  Scenario: <Scenario name>
    Given <precondition>
    When <action>
    Then <expected outcome>
      # Expected Result: <precise verifiable result>
```

Use [`references/cucumber_tests_20251212_115846.feature`](./references/cucumber_tests_20251212_115846.feature) as a style reference.

---

## ✅ Post-Generation Checklist

Run these checks **after** the LLM returns the `.feature` file. Fix and regenerate if any item fails.

### Coverage
- [ ] Every acceptance criterion maps to at least one scenario
- [ ] At least one happy-path scenario
- [ ] At least one negative / error scenario
- [ ] Data-driven cases use `Scenario Outline` + `Examples` table

### Quality per scenario
- [ ] Steps are **declarative** — no UI implementation details ("click button X")
- [ ] `Given` sets context, `When` performs the action, `Then` asserts the outcome
- [ ] Each `Then` step has a `# Expected Result:` comment with a precise, verifiable result
- [ ] No duplicate scenarios (same objective tested twice)
- [ ] Scenario names are unique and descriptive

### Format
- [ ] File starts with `# language: en`
- [ ] Feature is tagged `@{USER_STORY_KEY}` on the line before `Feature:`
- [ ] Valid Gherkin syntax — no markdown, no fences
- [ ] File saved as `.feature`

---

## Import

Run the import script after the post-generation checklist passes.

```bash
python scripts/import_cucumber_tests.py output/cucumber_tests_{timestamp}.feature
```

The script (`scripts/import_cucumber_tests.py`) will:
1. Read `JIRA_URL`, `JIRA_TOKEN`, `PROJECT_KEY` from `.env`
2. Upload the `.feature` file via multipart `POST /rest/raven/2.0/import/feature`
3. Return the list of created Xray test keys
4. The `@{USER_STORY_KEY}` tag in the file links all scenarios to the User Story automatically

---

## Error Reference

| HTTP | Context | Meaning | Fix |
|------|---------|---------|-----|
| `401` | Any | Invalid token | Check `JIRA_TOKEN` in `.env` |
| `403` | Any | No permission | Check project rights in JIRA |
| `404` | Import | Endpoint not found | Confirm Xray Raven plugin is installed (`/rest/raven/2.0`) |
| `400` | Import | Invalid Gherkin syntax | Validate `.feature` file — check for missing steps or malformed tags |
| `422` | Import | Project key not found | Check `PROJECT_KEY` in `.env` |
| `429` | Any | Rate limit | Add retry with backoff |
| `500` | Import | Bad file upload | Check multipart encoding and file path |
