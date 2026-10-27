"""Build a showcase's recorded agent responses from its hand-written files.

A showcase folder holds analysis.json, plan.json, storyboard.json and
lesson.py (the approved module). This script writes the replies each role
"gave" on the recorded run, so replay mode walks the real workflow:

* coder_v1.md  - the Manim Coder's first draft, with one realistic mistake
                 (an API that no longer exists in Manim Community), so the
                 render fails and the correction cycle has something to do;
* fixer_v1.md  - the Refiner's reply: the corrected module plus CHANGES;
* the two critics' reviews of the corrected render (reviews/*.json).

    python scripts/build_showcase.py backend/conceptflow/showcase/derivative
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

folder = Path(sys.argv[1])
meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
lesson = (folder / "lesson.py").read_text(encoding="utf-8").rstrip() + "\n"

bug = meta.get("bug")
draft = lesson
if bug:  # a showcase may record a first draft with one realistic mistake
    if bug["fixed"] not in lesson:
        sys.exit(f"the fixed line was not found in lesson.py: {bug['fixed']!r}")
    draft = lesson.replace(bug["fixed"], bug["broken"], 1)

(folder / "coder_v1.md").write_text(
    "```python\n" + draft + "```\n\nNOTES:\n" + "\n".join(f"- {n}" for n in meta["coder_notes"]) + "\n",
    encoding="utf-8",
)
(folder / "fixer_v1.md").write_text(
    "```python\n" + lesson + "```\n\nCHANGES:\n" + "\n".join(f"- {c}" for c in meta["fixer_changes"]) + "\n",
    encoding="utf-8",
)
print("wrote coder_v1.md and fixer_v1.md")
