---
name: python-code-standards
description: >
  Code-quality and documentation standards for writing Python in this
  repository — type hints, Google-style docstrings, specific exceptions, input
  validation, and comment style — with copy-ready function, module, and class
  templates. Consult when writing or refactoring Python source or docstrings.
version: 1
scope: authoring-standard
metadata:
  tags:
    - python
    - code-quality
    - docstrings
    - type-hints
    - error-handling
    - documentation
    - authoring-standard
---

# Python Code Standards

## Overview

The standards every piece of Python in this repository should follow, with
concrete templates. The audience is often scientists newer to software
engineering, so prefer clear, well-documented code over cleverness, and explain
*why* in comments. To review existing code against these standards, use
[review-design](../review-design/SKILL.md).

## When to Use

- Writing a new function, class, or module.
- Refactoring existing code, or adding/upgrading docstrings.

## Always include

1. **Type hints** — for function parameters and return values.
2. **Docstrings** — Google-style, on all public functions and classes.
3. **Error handling** — raise specific exceptions with clear, actionable messages.
4. **Input validation** — check assumptions about inputs at the boundary.
5. **Comments** — explain *why*, not *what*.

## Function template

```python
def example_function(
    data: list[float],
    threshold: float = 0.5,
    normalize: bool = True,
) -> list[float]:
    """
    Process data with filtering and optional normalization.

    This function filters values above a threshold and optionally
    normalizes them. Used in the preprocessing pipeline for
    sensor data cleanup.

    Args:
        data: Raw measurement values from sensor
        threshold: Minimum value to keep (default: 0.5)
        normalize: Whether to normalize to [0, 1] range (default: True)

    Returns:
        Processed data values

    Raises:
        ValueError: If data is empty or threshold is negative

    Example:
        >>> example_function([0.1, 0.7, 1.2], threshold=0.5)
        [0.58, 1.0]
    """
    # Validate inputs
    if not data:
        raise ValueError("Data cannot be empty")
    if threshold < 0:
        raise ValueError(f"Threshold must be non-negative, got {threshold}")

    # Filter data
    filtered = [x for x in data if x >= threshold]

    if not filtered:
        return []

    # Normalize if requested
    if normalize:
        max_val = max(filtered)
        return [x / max_val for x in filtered]

    return filtered
```

## Module docstring template

```python
"""
Module for data preprocessing operations.

This module provides functions for cleaning and normalizing
experimental data from the XYZ instrument. Typical workflow:

1. Load raw data with load_data()
2. Clean with remove_outliers()
3. Normalize with normalize_values()
4. Export with save_processed_data()

Example:
    >>> from package_name import preprocessing
    >>> data = preprocessing.load_data('experiment.csv')
    >>> clean = preprocessing.remove_outliers(data)
    >>> normalized = preprocessing.normalize_values(clean)
"""
```

## Class docstring template

```python
class DataProcessor:
    """
    Process and analyze experimental data.

    This class manages the complete data processing pipeline,
    from raw input to final analyzed output. It maintains
    state about processing parameters and history.

    Attributes:
        threshold: Minimum value for filtering
        normalize: Whether to apply normalization
        history: List of processing steps applied

    Example:
        >>> processor = DataProcessor(threshold=0.5)
        >>> processor.load('data.csv')
        >>> results = processor.process()
        >>> processor.save('output.csv')
    """
```
