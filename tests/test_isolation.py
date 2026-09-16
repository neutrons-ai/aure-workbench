"""That the suite does not read the machine it happens to run on.

`nr_workbench.env.load_env` reads `~/.nrw` and `~/.aure`, and nearly every
command reaches it. Before the autouse fixture in `conftest.py`, every test
outside `test_env.py` read the developer's real files — so a green suite on one
laptop said nothing about another, or about CI. Observed, not theorised: a real
`~/.aure` carrying `LLM_MODEL` changed what `nrw check-llm` reported.

This file is deliberately *not* `test_env.py`, which has its own autouse
fixture stubbing the same paths. A guard placed there passes whether the
project-wide isolation exists or not, which makes it decoration. Here it fails
if `conftest.isolate_user_env` is removed or stops being autouse.
"""

from __future__ import annotations

import os
from pathlib import Path

from nr_workbench import env as env_module


def test_the_per_user_paths_are_not_the_real_ones() -> None:
    assert Path.home() / ".nrw" != env_module.USER_ENV_PATH
    assert Path.home() / ".aure" != env_module.AURE_ENV_PATH
    assert not env_module.USER_ENV_PATH.exists()
    assert not env_module.AURE_ENV_PATH.exists()


def test_the_load_once_latch_starts_clear() -> None:
    """It is module-level state: whichever test called `load_env` first would
    otherwise decide for every test after it, making outcomes order-dependent."""
    assert env_module._loaded is False


def test_the_settings_under_test_start_unset() -> None:
    """A developer's own `LLM_*` must not be what a test is measuring."""
    leaked = [name for name in env_module.KNOWN_VARS if name in os.environ]
    assert leaked == [], f"inherited from the environment: {leaked}"
