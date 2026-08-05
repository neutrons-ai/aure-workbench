---
name: review-test
description: >
  Instrument-agnostic test-quality review standard for this repository. Use when
  assessing whether tests provide real confidence rather than just coverage —
  flagging mock-heavy unit tests, brittle implementation-detail tests, and
  missing integration coverage of critical paths. Invoke after adding or
  modifying tests, before merging significant test changes, or on request.
version: 1
scope: project-review-standard
edit_policy: >
  Single source of truth for the test-review standard in this repository. Edit it
  HERE, never in the dispatcher agents under .claude/agents/ or .github/agents/
  (which only load and apply it). Downstream projects that use this template may
  tailor the categories to their own domain.
review:
  status: pending
  reviewer: null
  reviewed_on: null
  basis: []
  notes: null
metadata:
  tags:
    - testing
    - integration-tests
    - mocking
    - test-quality
    - coverage
    - review-standard
---

# Test Review Standard

## Overview

This is the shared test-quality standard for the code in this repository. Its
premise: **the goal of a test suite is confidence that the system works, not a
high coverage number.** It prefers tests that exercise real components working
together and is skeptical of heavy mocking that reimplements the logic under test
and passes even when the code is broken.

The audience may be new to testing, so every finding should explain *why* a test
is low- or high-value and show a concrete replacement.

## When to Use

Use this skill when:

- Tests were added or modified, or a test-heavy change is about to merge.
- The user asks for a test-quality or coverage review.

Do **not** use this skill for:

- Architecture and maintainability of the code under test — that is
  [review-design](../review-design/SKILL.md).
- Vulnerabilities and unsafe patterns — that is
  [review-security](../review-security/SKILL.md).

## Operating rules

- **Read-only.** Never modify tests or code. Produce findings only.
- **Evidence-based.** Every finding cites `path:line` (or a test name) and says
  what confidence the test does or does not provide.
- **Judge value, not volume.** For every test ask: "If this passes, what does it
  prove?" A test that would pass while the feature is broken is worse than no
  test — it manufactures false confidence.
- Mocks are justified only for genuinely external or slow dependencies (network
  APIs, slow I/O). Flag mocks that stand in for the code being tested.

## Severity ladder

| Severity | Meaning |
|----------|---------|
| CRITICAL | A test that passes while the code it covers is broken (false confidence), or a critical path with no test at all. |
| HIGH | Mock-heavy test that reimplements the logic under test; a brittle test that fails on safe refactors. |
| MEDIUM | Redundant or low-value test; missing edge/error coverage on an important path. |
| LOW | Naming, clarity, or arrange/act/assert structure. |
| INFO | Observation or opportunity; not a defect. |

## Review categories

### 1. Integration over isolation
- **Prefer** tests that exercise real components working together (real
  filesystem, real in-process store, real parsing).
- **Flag** heavy mocking that creates brittle, implementation-dependent tests,
  especially mocks that simply mirror the production logic.

### 2. Value over coverage
- A good test catches real bugs and prevents regressions. A bad test passes when
  the code is broken and fails on safe refactors.
- Flag tests whose only assertion is that a mock was called, or that a value can
  be stored and read back (trivial getter/setter tests).

### 3. Real behavior over implementation details
- Test observable behavior, contracts, and outcomes — not private methods,
  internal state, or exact call sequences.
- Guideline: if the code can be refactored without changing behavior, the tests
  should still pass. A test that breaks under such a refactor is brittle.

### 4. Coverage of critical paths
- Identify core workflows / public APIs / error paths that are **not** covered
  end-to-end. Missing coverage on a path that matters is a finding even when the
  overall coverage percentage looks high.

### High-value test shapes (keep / add)
- End-to-end tests of a complete workflow from entry point to output.
- Contract/interface tests of public APIs, data formats, and error handling.
- Critical-path tests of the flows that matter most if they break.
- Regression tests that reproduce a real past bug with real components.

### Low-value test shapes (flag)
- Mock-heavy unit tests where mock setup dominates and mocks reimplement the
  logic under test.
- Getter/setter and trivial-property tests.
- Implementation-detail tests that assert private methods or exact call order.
- Redundant tests that overlap 100% with an existing integration test.

## Anti-patterns to flag (with fixes)

```python
# BAD: over-mocked — proves only that functions were called in order,
# not that the workflow produces a correct result
@patch("module.step_a")
@patch("module.step_b")
def test_workflow(mock_b, mock_a):
    mock_a.return_value = "a"
    mock_b.return_value = "b"
    workflow()
    mock_a.assert_called_once()
    mock_b.assert_called_with("a")

# GOOD: integration test with real components
def test_workflow_with_real_components():
    result = workflow(real_input_file)
    assert result.status == "success"
    assert verify_output_in_store()
```

```python
# BAD: testing the mock — passes even if the service is never called
@patch("package_name.ingest.service")
def test_generate_embeddings(mock_service):
    mock_service.embed.return_value = {"embeddings": [[0.1, 0.2]]}
    assert generate_embeddings(["text"]) == [[0.1, 0.2]]

# GOOD: real dependency, or skip when unavailable
@pytest.mark.integration
def test_generate_embeddings_real():
    if not service_available():
        pytest.skip("embedding service not available")
    result = generate_embeddings(["test text"])
    assert len(result) == 1 and len(result[0]) > 0
```

```python
# BAD: brittle call-count verification — breaks on safe refactors
def test_store_items(mock_session):
    store_items(items)
    assert mock_session.store.call_count == len(items)

# GOOD: assert the observable outcome
def test_store_items_integration():
    store_items(test_items)
    stored = retrieve_items_from_store()
    assert len(stored) == len(test_items)
    assert stored[0].text == test_items[0].text
```

## Output format

Produce a single markdown report, most severe findings first.

```markdown
# Test Review — <date>

## Summary
<2–4 sentences: overall suite health and the biggest gaps.>
Reviewed <n> tests. Severity counts: CRITICAL <n> · HIGH <n> · MEDIUM <n> · LOW <n> · INFO <n>

## Findings
<one block per finding, most severe first>

### [SEVERITY] <title>
- **Category:** <e.g. Mock Obsession, Missing Coverage>
- **Location:** `tests/test_x.py:LINE` (test name)
- **Problem:** what makes the test low-value or what path is uncovered.
- **What it actually verifies:** honest statement of the confidence it gives.
- **Recommendation:** the integration test or edge case to add/replace it with.

## High-value tests to keep
<tests that already provide real value — be specific.>

## Missing coverage
<critical workflows / error paths not tested end-to-end.>
```

## Rationalizations

| Excuse | Rebuttal |
|--------|----------|
| "Coverage is at 90%, so testing is good." | Coverage measures lines executed, not behavior verified. A mock-heavy suite can hit 90% and catch nothing. Judge what each test proves. |
| "Mocking makes the test fast and isolated." | Isolation from the code under test is the problem, not the goal. Mock external/slow services only; use real in-process components otherwise. |
| "It tests that we call the dependency correctly." | Asserting call order tests the implementation, not the outcome. It breaks on safe refactors and passes on real bugs. |
| "An integration test is too slow / needs a service." | Mark it `@pytest.mark.integration` and skip when the service is absent — do not replace it with a mock that proves nothing. |

## Red Flags

- A test that would still pass if the function it covers returned a wrong result.
- Mock setup makes up the majority of a test body.
- Assertions only check `mock.assert_called*`, never an observable outcome.
- A critical workflow whose only coverage is through mocked collaborators.

## Verification

- [ ] Every finding cites a test `path:line`/name and states the confidence the
      test provides.
- [ ] Each flagged low-value test has a concrete replacement or removal proposed.
- [ ] Critical paths lacking end-to-end coverage are listed under Missing
      coverage.
- [ ] Findings are ordered most-severe first, with severity counts in the summary.
- [ ] Mocks were judged case-by-case (external/slow → justified; standing in for
      code under test → flagged).
- [ ] No files were modified during the review.
