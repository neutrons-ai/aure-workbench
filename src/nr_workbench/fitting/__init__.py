"""Running fits and capturing their output.

Nothing here is imported at CLI start-up: refl1d, bumps and matplotlib are all
expensive, so every import lives inside the function that needs it.
"""

from __future__ import annotations
