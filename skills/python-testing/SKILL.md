---
name: python-testing
description: >
  How to write tests in this repository — the Arrange-Act-Assert structure, what
  to cover (normal, edge, error, and integration cases), and the test-naming
  convention, with examples. Consult when adding or changing tests. To judge the
  quality of existing tests, use review-test instead.
version: 1
scope: authoring-standard
metadata:
  tags:
    - python
    - testing
    - pytest
    - arrange-act-assert
    - authoring-standard
---

# Python Testing

## Overview

How to author tests here. The goal is confidence that the system works — favor
tests that exercise real behavior over heavy mocking.

## When to Use

- Adding tests for new code, or extending tests for changed code.

Do **not** use this skill to *review* existing test quality (mock-heaviness,
brittleness, coverage gaps) — that is [review-test](../review-test/SKILL.md).

## Test structure — Arrange, Act, Assert

Every test follows the same three phases:

```python
def test_example_function_filters_correctly():
    """Test that values below threshold are removed."""
    # Arrange: set up test data and expected results
    input_data = [0.1, 0.5, 0.7, 1.0]
    threshold = 0.6
    expected_length = 2

    # Act: call the function
    result = example_function(input_data, threshold=threshold)

    # Assert: verify expectations
    assert len(result) == expected_length
    assert all(x >= threshold for x in result)
```

## Always test

1. **Normal cases** — typical expected inputs.
2. **Edge cases** — empty inputs, single items, maximum/boundary values.
3. **Error cases** — invalid inputs, type errors (assert the exception is raised).
4. **Integration** — how components work together, end to end.

## Naming convention

`test_<function_name>_<scenario>_<expected_outcome>`

Examples:

- `test_process_data_with_empty_list_raises_error`
- `test_process_data_with_valid_input_returns_filtered_list`
- `test_process_data_with_all_below_threshold_returns_empty`

Use pytest markers (`@pytest.mark.integration`, `@pytest.mark.slow`) for tests
that hit real services or are slow, so they can be selected or skipped.
