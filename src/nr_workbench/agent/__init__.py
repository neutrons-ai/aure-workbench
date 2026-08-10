"""Running the harness unattended: the limits, not the judgement.

nr-workbench driven by a coding harness already outperforms a hand-written
decision loop, and the evidence says why: of the seventeen findings a real week
of analysis produced, one was reachable by arithmetic alone and eleven needed
judgement. So nothing here decides anything. This package supplies what has to
exist *around* a harness for it to run without a person watching --- limits it
cannot talk its way past, and a record of what it did.

The one rule that shapes the whole package: **an instruction is not a
mechanism.** Telling a model not to publish is a request; a hook that refuses
the call is a limit. Both layers are here, and they are independent on purpose.
"""

from __future__ import annotations

__all__ = ["guard"]
