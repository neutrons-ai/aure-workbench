---
name: review-security
description: >
  Instrument-agnostic security review standard for this Python repository. Use
  when auditing code for exposed secrets, code injection and unsafe
  deserialization, command and path injection, untrusted-file parsing, web/API
  (OWASP) issues where a web surface exists, dependency/supply-chain risk, and
  information disclosure. Invoke before deployment, after changes to I/O or
  web/CLI surface area, or on request.
version: 1
scope: project-review-standard
edit_policy: >
  Single source of truth for the security-review standard in this repository.
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
  standards: [OWASP-Top-10, CWE]
  tags:
    - security
    - secrets
    - code-execution
    - deserialization
    - injection
    - path-traversal
    - supply-chain
    - owasp
    - review-standard
---

# Security Review Standard

## Overview

This is the shared security standard for the code in this repository. It targets
the realistic threat model of scientific Python software: code that reads
instrument data files, user-supplied configs and scripts, loads Python model
files, and may expose a CLI or a small web/API surface — often running on shared
machines. It is generally **not** a hardened internet-facing service.

Findings map to recognized standards — **CWE** identifiers, and the **OWASP Top
10** where a web or API surface actually exists. Do **not** invent web/LLM
findings for code that has no such surface; mark those categories **Not
Applicable** and say why. The audience may be new to secure coding, so every
finding needs a clear explanation and a concrete fix example.

## When to Use

Use this skill when:

- Auditing before a deployment, or after significant changes to I/O, web, or CLI
  surface area.
- The user asks for a security review.

Do **not** use this skill for:

- Architecture, duplication, and maintainability judgments — that is
  [review-design](../review-design/SKILL.md).
- Test quality — that is [review-test](../review-test/SKILL.md).

## Operating rules

- **Read-only.** Never modify code. Produce findings only.
- **Evidence-based.** Every finding cites `path:line` and a short snippet. No
  finding without a concrete location.
- **No false coverage.** If a category does not apply (e.g. no web surface, no
  network calls), record it under *Controls not applicable* with the reason.
  Silence is not the same as "checked and clear."
- **Confirm before flagging.** Trace each candidate to its input source; confirm a
  secret-pattern hit is a live secret, not a placeholder or example.
- **Severity is impact-based**, not vibe-based (see ladder below).
- **Adapt to the surfaces that actually exist** in this codebase — not every
  category applies to every project.

## Severity ladder

| Severity | Meaning |
|----------|---------|
| CRITICAL | Exploitable now with serious impact: live secret, RCE from data/config a normal user controls, credential exfiltration. |
| HIGH | Strong vulnerability requiring a plausible precondition (e.g. a crafted input file, a specific caller). |
| MEDIUM | Weakness exploitable only in narrow conditions or needing chaining. |
| LOW | Hardening gap / defense-in-depth; no direct exploit path. |
| INFO | Observation worth recording; not a vulnerability. |

## Review categories

Scan for each. Report what applies; mark the rest Not Applicable.

### 1. Secrets & credential exposure — CWE-798, CWE-259, CWE-532
- Hard-coded API keys, tokens, passwords, connection strings, private keys.
- Secrets in configs, YAML, `.env`, notebooks (including cell **outputs**), test
  fixtures, or example files.
- Secrets printed to logs, tracebacks, or CLI output.
- `.gitignore` coverage for `.env`, `*.pem`, `*.key`, credential files.
- Technique: grep high-signal patterns (`sk-`, `AKIA`, `-----BEGIN * PRIVATE
  KEY-----`, `password=`, `token=`, `Authorization:`) and scan git history if
  available. Confirm whether each hit is a live secret vs a placeholder.

```python
# BAD: hard-coded API key
client = OpenAI(api_key="sk-not-a-real-key-example")

# GOOD: from the environment
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
```

### 2. Arbitrary code execution & unsafe deserialization — CWE-94, CWE-95, CWE-502
- `eval` / `exec` / `compile` on anything that is not a literal constant —
  LLM-generated or user-supplied code must be sandboxed.
- `pickle`, `marshal`, `shelve`, `joblib.load`, `numpy.load(allow_pickle=True)`,
  `torch.load`, or `yaml.load` **without** `SafeLoader`, on any data a user or
  data file can influence.
- `importlib` / `__import__` / `runpy` loading user-supplied modules or scripts
  (common when a project executes user "model files").
- Hook/plugin/callback systems that execute registered callables — note the trust
  assumptions and failure behavior.

```python
# BAD: unsafe YAML loading (can execute arbitrary constructors)
config = yaml.load(file_content)
# GOOD
config = yaml.safe_load(file_content)

# BAD: unrestricted exec of generated code
exec(generated_code)
# BETTER: restricted namespace; BEST: run in a subprocess with timeout + limits
exec(generated_code, {"__builtins__": {}})
```

### 3. Command & subprocess injection — CWE-78
- `os.system`, `subprocess.*(..., shell=True)`, `Popen` with string-built
  commands, `os.popen`. Check whether any argument derives from user input,
  filenames, run numbers, or file contents. Prefer list-form args without
  `shell=True`.

### 4. Filesystem & path handling — CWE-22, CWE-377, CWE-59
- User/config-derived paths used in `open`/`Path` without validating against an
  allowed base directory (path traversal via `../`).
- Archive extraction (`zipfile`, `tarfile`) without member-path validation
  ("zip-slip"); decompression bombs.
- Insecure temp files (predictable names, `mktemp`), world-writable outputs,
  symlink following, TOCTOU between check and open.

```python
# BAD: direct use of a user-supplied path
with open(user_provided_path) as f:
    data = f.read()

# GOOD: resolve and confine to a base directory
base = Path("/allowed/directory").resolve()
target = (base / user_provided_path).resolve()
if not target.is_relative_to(base):
    raise ValueError("Path traversal detected")
```

### 5. Untrusted data-file parsing — CWE-611 (XXE), CWE-502, CWE-400
- Parsing of user/instrument files (HDF5, XML, JSON, custom formats): XML
  external-entity expansion; unbounded allocation driven by file headers;
  deserialization paths reachable from file contents (see category 2).

### 6. Web / API surface — OWASP Top 10 (ONLY if present)
- If and only if the repo exposes Flask/FastAPI/Django/etc.: authn/authz on
  state-changing endpoints, CSRF, XSS (`|safe`, `Markup()`), SSRF from
  user-supplied URLs, open redirects, debug mode enabled, hard-coded
  `SECRET_KEY`, missing upload type/size limits, missing `Content-Type`
  validation.
- If no such surface exists, mark **Not Applicable**.

### 7. Dependency & supply-chain — CWE-1104, CWE-494
- Unpinned or over-permissive version specs on security-relevant deps; presence
  and use of a lockfile.
- Runtime installs (`pip install` invoked by the code), non-standard package
  indices, downloads without integrity checks.
- Obviously known-vulnerable pinned versions (flag for follow-up scanning; do not
  fabricate CVE claims).

### 8. Information disclosure & resource exhaustion — CWE-209, CWE-532, CWE-400
- Verbose tracebacks/internal paths surfaced to users; secrets or sensitive data
  written to logs; debug flags on by default.
- File uploads without size limits; long-running jobs without timeouts or
  resource caps; unbounded loops/allocation driven by user input; missing rate
  limiting on endpoints.

## Output format

Produce a single markdown report, most severe findings first.

```markdown
# Security Review — <date>

## Summary
<2–4 sentences: overall risk level and the headline findings.>
Overall risk: CRITICAL | HIGH | MEDIUM | LOW
Severity counts: CRITICAL <n> · HIGH <n> · MEDIUM <n> · LOW <n> · INFO <n>

## Findings
<one block per finding, most severe first>

### [SEVERITY] <title>
- **Category:** <e.g. Secrets Leak> · **CWE:** CWE-XXX
- **Location:** `path/to/file.py:LINE`
- **Description:** what it is and why it matters.
- **Impact:** what an attacker or accident could achieve.
- **Evidence:** short snippet or the matched pattern.
- **Remediation:** concrete fix (show the corrected pattern).
- **Verification:** how to confirm the fix works.

## Positive findings
<security practices already done well — be honest and specific.>

## Controls not applicable
<category → why it doesn't apply to this codebase.>
```

## Rationalizations

| Excuse | Rebuttal |
|--------|----------|
| "It's internal / research code, security doesn't apply." | Shared machines have many users; a live secret or a `pickle`-load of a user file is a real multi-user compromise, not hypothetical. |
| "`yaml.load` / `pickle` is fine, we control the inputs." | Scientific code routinely loads user configs, data files, and model scripts. If any of those reach the deserializer, it is arbitrary code execution — verify the actual data path before dismissing. |
| "No findings in category X, so I'll leave it out." | Omission reads as 'not checked'. Record it under *Controls not applicable* with the reason. |
| "This looks like the web checklist, I'll flag CSRF/XSS." | Only if a web surface actually exists. Fabricated web findings are exactly the failure this standard exists to prevent. |

## Red Flags

- A grep for secret patterns returned hits that were not each confirmed as
  placeholder vs live.
- Any `eval`/`exec`/unguarded deserialization reachable from a file or config a
  user supplies, reported as less than HIGH without justification.
- Outbound network destinations that are not expected for this project.
- A report with zero *Controls not applicable* entries (means categories were
  skipped silently, not cleared).

## Verification

- [ ] Every finding cites `path:line` with a snippet and a CWE.
- [ ] Secret-pattern hits were each confirmed live vs placeholder.
- [ ] Deserialization / `eval` / `exec` / subprocess sites were traced to their
      input source.
- [ ] The web/API category is explicitly marked Not Applicable when no such
      surface exists.
- [ ] Every non-applicable category appears under *Controls not applicable* with a
      reason.
- [ ] Findings are ordered most-severe first, with severity counts in the summary.
- [ ] No files were modified during the review.
