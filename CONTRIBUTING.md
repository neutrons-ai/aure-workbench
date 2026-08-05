# Contributing

Thanks for contributing! This project follows the **assess → plan → implement →
test → review** workflow described in
[.github/copilot-instructions.md](.github/copilot-instructions.md) (also imported
by [CLAUDE.md](CLAUDE.md), so Claude Code follows the same rules).

## Setup

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install        # enable the lint / format / secret-scan hooks
```

## Before you open a pull request

- `pre-commit run --all-files` — runs the exact checks CI runs (ruff lint + format, secret scan, ...).
- `pytest` — all tests must pass and stay above the coverage floor in `pyproject.toml`.
- Add or update tests for any behavior change. Favor real, integration-style tests
  over heavy mocking — see the standard in [skills/review-test/SKILL.md](skills/review-test/SKILL.md).
- Record non-obvious decisions, API quirks, or constraints in
  [docs/ground_truths.md](docs/ground_truths.md) so they persist across sessions.

## Reviews

For significant changes, run the reviewer subagents (design / security / test).
They apply the shared standards in [skills/](skills/); to change a standard, edit
its `SKILL.md`, not the dispatcher agent.

## Code style

Type hints and Google-style docstrings on public functions, specific exceptions
with clear messages, and comments that explain *why*. The
[copilot-instructions](.github/copilot-instructions.md) file has full examples.
