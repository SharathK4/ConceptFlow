"""Static checks on generated Manim code before it is allowed to run.

The code comes from a language model and runs on the user's machine, so it
is parsed first and rejected if it reaches for anything a lesson has no use
for: imports beyond Manim/cfkit/maths, file or process access, dynamic
evaluation, or dunder attributes (the usual route out of any Python
"sandbox"). Renders additionally run in their own process with a timeout.

The same pass also checks the lesson's *shape*: one class per storyboard
scene, subclassing ConceptScene, and every beat used exactly once. Those
findings go to the fixer as plainly as a traceback would.
"""

from __future__ import annotations

import ast
import textwrap
import re
from dataclasses import dataclass, field

ALLOWED_MODULES = {"manim", "cfkit", "numpy", "math", "random", "itertools", "functools", "colorsys"}
FORBIDDEN_NAMES = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
    "os",
    "sys",
    "subprocess",
    "shutil",
    "pathlib",
    "socket",
    "importlib",
    "builtins",
    "ctypes",
}
FORBIDDEN_METHODS = {"add_sound", "save_image", "write_to_file"}


@dataclass
class Check:
    ok: bool
    problems: list[str] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    beats_used: dict[str, list[str]] = field(default_factory=dict)


def extract_code(text: str) -> str:
    """Pull the Python module out of a model reply (fenced or not)."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if blocks:
        return _tidy(max(blocks, key=len))
    # A reply cut off mid-module has an opening fence and no closing one.
    opened = re.match(r"\s*```(?:python|py)?\s*\n(.*)", text, re.S)
    if opened:
        return _tidy(opened.group(1))
    return _tidy(text)


def _tidy(block: str) -> str:
    """Undo indentation a model added to the whole module, then fix the import order.

    Models sometimes indent every line (or every line but the first) by a few
    spaces; stripping only the first line would leave a module that does not parse.
    """
    lines = block.strip("\n").splitlines()
    code = textwrap.dedent("\n".join(lines)).strip("\n")
    try:
        ast.parse(code)
        return _star_imports_in_order(code) + "\n"
    except SyntaxError:
        pass
    if len(lines) > 1:
        candidate = lines[0].strip() + "\n" + textwrap.dedent("\n".join(lines[1:])).strip("\n")
        try:
            ast.parse(candidate)
            return _star_imports_in_order(candidate) + "\n"
        except SyntaxError:
            pass
    # Notes in prose left after the module ("These scenes use ...") end it early.
    code_lines = code.splitlines()
    for cut in range(1, min(25, len(code_lines) - 1)):
        candidate = "\n".join(code_lines[:-cut]).rstrip()
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        if re.search(r"^class \w+\(", candidate, re.M):
            return _star_imports_in_order(candidate) + "\n"
    return code + "\n"


_STAR_IMPORT = re.compile(r"^from\s+(manim|cfkit)\s+import\s+\*\s*$")


def _star_imports_in_order(code: str) -> str:
    """``from manim import *`` must come before ``from cfkit import *``.

    Both export names such as ``Label``; whichever star import runs last wins,
    and Manim's ``Label`` is a shape label that rejects ``size=``. Put the two
    star imports first, in the order that lets the house style win.
    """
    lines = code.splitlines()
    if not any(_STAR_IMPORT.match(line) for line in lines):
        return code
    body = [line for line in lines if not _STAR_IMPORT.match(line)]
    future = [line for line in body if line.startswith("from __future__")]
    rest = [line for line in body if not line.startswith("from __future__")]
    return "\n".join([*future, "from manim import *", "from cfkit import *", *rest])


def check(code: str, scene_ids: list[str], beat_ids: dict[str, list[str]]) -> Check:
    problems: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return Check(False, [f"SyntaxError on line {exc.lineno}: {exc.msg} -> {(exc.text or '').strip()}"])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    problems.append(f"line {node.lineno}: import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                problems.append(f"line {node.lineno}: import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"line {node.lineno}: use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__") and node.attr != "__init__":
                problems.append(f"line {node.lineno}: dunder attribute '{node.attr}' is not allowed")
            elif node.attr in FORBIDDEN_METHODS:
                problems.append(f"line {node.lineno}: '{node.attr}' is not allowed (narration and output are handled by the studio)")

    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    expected = [scene_class(s) for s in scene_ids]
    found = []
    used: dict[str, list[str]] = {}
    for sid, name in zip(scene_ids, expected):
        node = classes.get(name)
        if node is None:
            problems.append(f"missing scene class '{name}' for storyboard scene {sid}")
            continue
        bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
        if "ConceptScene" not in bases:
            problems.append(f"class {name} must subclass ConceptScene")
        found.append(name)
        used[sid] = _beats_in(node)

    for sid in scene_ids:
        want = beat_ids.get(sid, [])
        got = used.get(sid, [])
        missing = [b for b in want if b not in got]
        extra = [b for b in got if b not in want]
        dupes = sorted({b for b in got if got.count(b) > 1})
        if missing and sid in used:
            problems.append(f"scene {sid} never plays beat(s) {', '.join(missing)} - every narration line needs a `with self.beat(...)` block")
        if extra:
            problems.append(f"scene {sid} uses unknown beat id(s) {', '.join(extra)}")
        if dupes:
            problems.append(f"scene {sid} plays beat(s) {', '.join(dupes)} more than once")

    return Check(not problems, problems, found, used)


def _beats_in(node: ast.ClassDef) -> list[str]:
    beats = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "beat" and sub.args:
            arg = sub.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                beats.append(arg.value)
    return beats


def scene_class(scene_id: str) -> str:
    return scene_id.upper()


def class_source(code: str, name: str) -> str:
    """Source of one scene class plus every module-level helper, for caching."""
    tree = ast.parse(code)
    lines = code.splitlines()
    shared, own = [], ""
    for node in tree.body:
        text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if isinstance(node, ast.ClassDef) and node.name == name:
            own = text
        elif isinstance(node, ast.ClassDef) and _is_scene(node):
            continue  # another scene: changing it cannot change this one
        else:
            shared.append(text)
    return "\n".join(shared) + "\n" + own


def _is_scene(node: ast.ClassDef) -> bool:
    return any(getattr(b, "id", getattr(b, "attr", "")) == "ConceptScene" for b in node.bases)
