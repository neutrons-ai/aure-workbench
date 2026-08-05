---
name: review-design
description: >
  Instrument-agnostic software-design review standard for this repository.
  Use when reviewing code for architecture and layering, code duplication,
  hard-coded values, error handling, complexity and organic-growth smells,
  file size, and test coverage of critical paths. Applies after a feature or
  refactor, or whenever a design/architecture review is requested.
version: 1
scope: project-review-standard
edit_policy: >
  Single source of truth for the design-review standard in this repository.
  Edit it HERE, never in the dispatcher agents under .claude/agents/ or
  .github/agents/ (which only load and apply it). Downstream projects that use
  this template may tailor the categories to their own domain.
review:
  status: pending
  reviewer: null
  reviewed_on: null
  basis: []
  notes: null
metadata:
  tags:
    - design
    - architecture
    - duplication
    - hard-coded-values
    - correctness
    - error-handling
    - complexity
    - testing
    - review-standard
---

# Design Review Standard

## Overview

This is the shared software-design standard for the code in this repository. It
covers ordinary maintainability concerns — duplication, hard-coded values,
separation of concerns, complexity, and organic-growth smells — plus one lens
that matters more for scientific software than for typical applications:
**correctness risk** (where can this silently produce a plausible-but-wrong
result?).

The audience is often scientists who are newer to software engineering, so every
finding should explain *why* it matters and give a concrete, actionable fix — not
just name a rule.

## When to Use

Use this skill when:

- Reviewing code after a new feature (roughly 3+ functions) or a significant
  refactor.
- The user asks for a design, architecture, or maintainability review.

Do **not** use this skill for:

- Security posture (secrets, injection, unsafe deserialization) — that is
  [review-security](../review-security/SKILL.md).
- Test quality and coverage judgments — that is
  [review-test](../review-test/SKILL.md).

## Operating rules

- **Read-only.** Never modify code. Produce findings only.
- **Evidence-based.** Every finding cites `path:line`. Prefer a concrete example
  over a general assertion.
- **Prioritize by impact**, using the severity ladder below. Do not bury a
  correctness risk under style nits.
- Heuristics (file length, parameter counts, nesting depth) are **signals, not
  laws** — justify why a flagged instance actually makes correct change harder,
  ideally by naming an edit that would be risky today.
- **Consider backward compatibility** before recommending a change to a public
  API.

## Severity ladder

| Severity | Meaning |
|----------|---------|
| CRITICAL | Can silently produce a wrong result, or a design defect that blocks correct use of the code. |
| HIGH | Serious maintainability/correctness risk likely to cause defects or wasted effort. |
| MEDIUM | Real design debt worth scheduling. |
| LOW | Minor cleanup / style. |
| INFO | Observation or opportunity; not a defect. |

## Review categories

### 1. Code duplication
- **Exact duplicates**: identical or near-identical blocks in multiple places.
- **Structural duplicates**: the same logic with renamed variables or minor
  variation.
- **Concept duplicates**: multiple implementations of one concept that should
  share a helper, base class, or mixin.
- When you find duplication, suggest a shared utility function, an extracted base
  class/mixin, or composition over inheritance where appropriate.

### 2. Hard-coded values
- **Magic numbers**: unexplained numeric constants (e.g. `timeout=30`,
  `max_retries=3`).
- **String literals**: URLs, endpoints, file paths, error-message text.
- **Configuration values**: ports, hostnames, feature flags, credentials.
- **Thresholds and limits**: size limits, rate limits, buffer sizes.
- Recommend named constants, configuration files/objects, or environment
  variables with sensible defaults.

### 3. Architecture & separation of concerns
- **Single Responsibility**: each module/class has one reason to change.
- **Separation of concerns**: business logic, I/O, and presentation are
  separated.
- **Dependency management**: no circular dependencies; prefer dependency
  injection over hidden globals.
- **Interface design**: public APIs are clear, consistent, and minimal.
- Watch for leaky abstractions and a wrapper re-implementing logic it should
  delegate.

### 4. Correctness risk
- Unit conversions, coordinate/scale transforms, and numeric edge cases: correct,
  tested, and not silently mis-mapped?
- Unexplained magic numbers in calculations that should be sourced from named
  constants or configuration.
- **Silent wrong-answer paths**: places that can return a plausible but incorrect
  result without raising or labeling it. This is the highest-value thing to find
  in scientific code.

### 5. Error handling & failure modes
- Does the code **fail loud** when a required input is missing, or silently
  proceed with a default? Flag bare `except:` and swallowed exceptions.
- Is the error strategy consistent and typed, or ad-hoc? Are warnings actually
  surfaced to the user rather than lost?

### 6. File size & complexity
- **Python files should not exceed ~300 lines when avoidable** — treat this as a
  *signal* that a module may hold too many responsibilities, not an automatic
  defect.
- When a file exceeds the threshold and the length reflects real coupling,
  propose a split: name the new files, their responsibilities, which
  functions/classes move where, the required import changes, and a suggested
  order of steps.

### 7. Organic-growth smells
- **God classes/modules** with too many responsibilities.
- **Feature envy**: a method that uses more of another class than its own.
- **Shotgun surgery**: one change forces edits across many files.
- **Long parameter lists** (> 4–5), **deep nesting** (> 3 levels), and
  inconsistent naming conventions.

### 8. Testing of critical paths
- Are the code's most important paths (core logic, data transforms, public API)
  covered by tests? Flag critical paths with no coverage. (Depth of test-quality
  analysis belongs to [review-test](../review-test/SKILL.md).)

## Output format

Produce a single markdown report, most severe findings first.

```markdown
# Design Review — <date>

## Summary
<2–4 sentences: overall design health and the biggest risks.>
Severity counts: CRITICAL <n> · HIGH <n> · MEDIUM <n> · LOW <n> · INFO <n>

## Findings
<one block per finding, most severe first>

### [SEVERITY] <title>
- **Category:** <e.g. Code Duplication, Correctness Risk>
- **Location:** `path/to/file.py:LINE`
- **Problem:** what the issue is.
- **Impact:** why it matters (correctness / maintainability / readability).
- **Evidence:** short snippet or reference.
- **Recommendation:** concrete change. For a large refactor, use the plan
  template below.

## Positive findings
<design strengths worth preserving — be specific.>

## Out of scope / not applicable
<categories that did not apply, with a one-line reason each.>
```

For a significant refactor, expand the recommendation using this structure:

```markdown
## Refactoring Proposal: <brief description>
### Problem
### Impact
### Proposed changes
#### Phase 1: Preparation
- [ ] …
#### Phase 2: Core changes
- [ ] …
#### Phase 3: Cleanup
- [ ] …
### Files affected
- `path/to/file.py` — what changes
### Testing strategy
<how to verify the refactor preserves behavior.>
```

## Rationalizations

| Excuse | Rebuttal |
|--------|----------|
| "It's research/experimental code, design standards don't apply." | Research code produces results people publish and build on. Silent correctness defects are more costly here, not less. |
| "The file is long but it works." | Length is only a signal; the finding is whether the coupling makes correct change hard. Justify it with a concrete edit that would be risky today. |
| "Duplication is fine, it's only two places." | Two places is where a fix silently diverges. Note it now — extracting a helper is cheap while it is small. |
| "It never fails in practice." | A path that silently accepts bad input doesn't 'fail' — it succeeds with the wrong answer. That is the CRITICAL case to hunt. |

## Red Flags

- A path that can emit a result while a required input is missing or invalid,
  without labeling or warning.
- The same logic duplicated in several places where a change to one would need to
  be mirrored by hand.
- Hard-coded values that a user or deployment would realistically need to change.
- Style findings ranked above correctness findings.

## Verification

- [ ] Every finding cites `path:line` and states its correctness / maintainability
      impact.
- [ ] Silent wrong-answer paths (category 4) were actively hunted and either found
      or explicitly reported as none found.
- [ ] File-size / complexity flags are justified as signals, with a concrete risky
      edit — not raised on line count alone.
- [ ] Findings are ordered most-severe first, with severity counts in the summary.
- [ ] Recommendations are concrete and, for large refactors, phased.
- [ ] No files were modified during the review.
