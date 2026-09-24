"""The Sanitize screen names its workflow states with core/workflow.py's words.

``ui/src/lib/workflowState.ts`` derives the state the strip on the Sanitize
screen shows. The names are duplicated there because the UI has no Python at
runtime, and a duplicated vocabulary drifts. This reads the TypeScript union and
fails if it names a state the state machine does not define, or drops one it
does, so "BLOCKED" on the screen and in a plan's JSON stay the same word.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.workflow import WorkflowState

SOURCE = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "workflowState.ts"


def test_the_ui_state_names_are_exactly_the_state_machine_names() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    union = re.search(r"export type WorkflowStateName =(.*?)\n\n", text, re.S)
    assert union, "WorkflowStateName union not found"
    names = set(re.findall(r"'([A-Z_]+)'", union.group(1)))
    assert names == {state.value for state in WorkflowState}
