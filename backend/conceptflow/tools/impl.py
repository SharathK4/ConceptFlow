"""The tools themselves, as plain functions (the MCP server wraps these).

* ``read_document``  - file parser: PDFs (pypdf), Markdown and text.
* ``lookup_concept`` - web knowledge lookup: Wikipedia's public REST API.
* ``check_math``     - calculator / computer algebra: SymPy.
* ``search_lessons`` - database lookup: the SQLite lesson library.

Each returns a Pydantic model from ``models.py``.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import get_settings
from .library import Library
from .models import ConceptLookup, DocumentDigest, LibrarySearch, MathCheck

# ----------------------------------------------------------- document parser

MAX_PAGES = 80
_STOP = set(
    """about above after again against also among another because before being below between both could does doing
    during each either every first from further given have having here however into itself just least many might more
    most much must need never often only other otherwise over same shall should since some such than that their them
    then there these they this those through thus under until upon very what when where whether which while whose with
    within without would your figure table section theorem lemma proof equation paper results result show shows
    shown using used also define defined following example abstract introduction conclusion""".split()
)
_SECTION = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+([A-Z][A-Za-z][^\n]{2,80})$")
_MD_HEADING = re.compile(r"^\s*#{1,3}\s+(.{3,90})$")


def _safe_path(path: str) -> Path:
    """Only files inside the studio's lessons folder may be read."""
    root = get_settings().lessons_dir.resolve()
    target = Path(path).resolve()
    if root not in target.parents or not target.is_file():
        raise ValueError("documents can only be read from the studio's upload folders")
    return target


def read_document(path: str, max_chars: int = 15000) -> DocumentDigest:
    target = _safe_path(path)
    suffix = target.suffix.lower()
    max_chars = max(1000, min(int(max_chars), 60000))
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        return DocumentDigest(name=target.name, kind="image", warnings=["an image has no text layer; the vision model reads it directly"])

    pages = 1
    meta_title = ""
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(target))
        pages = len(reader.pages)
        texts = []
        for page in reader.pages[:MAX_PAGES]:
            try:
                texts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - a broken page must not sink the whole file
                texts.append("")
        raw = "\n".join(texts)
        try:
            meta_title = (reader.metadata.title or "") if reader.metadata else ""
        except Exception:  # noqa: BLE001
            meta_title = ""
        kind = "pdf"
    else:
        raw = target.read_text(encoding="utf-8", errors="replace")
        kind = "text"

    raw = raw.replace("\r", "")
    text = re.sub(r"-\n(?=[a-z])", "", raw)  # re-join words hyphenated across lines
    lines = [ln.strip() for ln in text.split("\n")]
    clean = re.sub(r"[ \t]+", " ", "\n".join(ln for ln in lines if ln))

    warnings: list[str] = []
    if kind == "pdf" and len(clean) < 200:
        warnings.append("little or no extractable text - the PDF may be scanned; the vision model will read the pages")
    if pages > MAX_PAGES:
        warnings.append(f"only the first {MAX_PAGES} of {pages} pages were read")

    title = meta_title.strip() if len(meta_title.strip()) > 8 else ""
    if not title:
        heading = next((m.group(1).strip() for ln in lines[:15] if (m := _MD_HEADING.match(ln))), "")
        head = [ln for ln in lines[:12] if ln and len(ln) > 3]
        title = heading or (" ".join(head[:2])[:160] if head else target.stem)

    abstract = ""
    match = re.search(r"\babstract\b[\s.:\-]*(.+?)(?:\n\s*#{0,3}\s*(?:1\.?\s+)?introduction\b|\n\s*keywords\b|\n\s*index terms\b)", text, re.I | re.S)
    if match:
        abstract = re.sub(r"\s+", " ", match.group(1)).strip()[:1800]

    sections: list[str] = []
    for ln in lines:
        m = _SECTION.match(ln) or _MD_HEADING.match(ln)
        if not m:
            continue
        heading = " ".join(m.groups()).strip() if len(m.groups()) > 1 else m.group(1).strip()
        if heading not in sections and not heading.endswith("."):
            sections.append(heading)
        if len(sections) >= 30:
            break

    counts: dict[str, int] = {}
    for word in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", clean):
        w = word.lower()
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 1
    key_terms = [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:12]]

    return DocumentDigest(
        name=target.name,
        kind=kind,
        pages=pages,
        title=title,
        abstract=abstract,
        sections=sections,
        key_terms=key_terms,
        excerpt=clean[:max_chars],
        chars_total=len(clean),
        truncated=len(clean) > max_chars,
        warnings=warnings,
    )


# -------------------------------------------------------- knowledge lookup

_UA = {"User-Agent": "ConceptFlowAI/1.0 (educational multi-agent project; https://github.com)"}
_lookup_cache: dict[str, ConceptLookup] = {}
_cache_lock = threading.Lock()


def _cache_file() -> Path:
    return get_settings().cache_dir / "lookups.json"


def _load_cache() -> None:
    if _lookup_cache:
        return
    try:
        for key, value in json.loads(_cache_file().read_text(encoding="utf-8")).items():
            _lookup_cache[key] = ConceptLookup.model_validate(value)
    except (OSError, ValueError):
        pass


_QUERY_FILLER = set(
    """explain explaining explanation show visual visually visualise visualize intuition intuitive understand understanding
    lesson teach what why how does do the and for with from into that this its are was were can about simple simply
    meaning means mean actually really single instant geometric geometrically measure measures
    theta alpha beta gamma delta epsilon lambda sigma omega phi psi equal equals minus plus one two three zero half
    even though everywhere always never""".split()
)


def _query_words(text: str) -> list[str]:
    return list(dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _QUERY_FILLER))


def _content_words(text: str) -> list[str]:
    return [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in _query_words(text)]


_MATH_SENSE = re.compile(r"mathemat|statist|probabil|geometr|calculus|algebra|topolog|analysis|physics|number theory", re.I)


def _title_score(words: list[str], title: str) -> tuple[float, int]:
    """How well an article title names the query: words matched, less half a point per extra word.

    A disambiguating "(mathematics)" earns a small bonus - this is a maths studio.
    """
    if "(disambiguation)" in title or title.lower().startswith("list of"):
        return -1.0, len(words)
    main, _, sense = title.partition(" (")
    title_words = set(_content_words(main))
    overlap = sum(1 for w in set(words) if w in title_words)
    first = min((words.index(w) for w in title_words if w in words), default=len(words))
    score = overlap - 0.5 * len(title_words - set(words))
    if overlap and _MATH_SENSE.search(sense):
        score += 0.25
    return score, first


def _best_hit(query: str, hits: list[dict]) -> dict | None:
    """The article whose title names the concept, or None when no title does.

    Full-text search ranks "History of calculus" above "Derivative" for "the
    derivative as the slope of a curve"; a title match is what a reader means,
    and no grounding is better than grounding on the wrong article.
    """
    words = _content_words(query)
    scored = [(_title_score(words, hit["title"]), rank, hit) for rank, hit in enumerate(hits)]
    scored = [item for item in scored if item[0][0] > 0]
    if not scored:
        return None
    return min(scored, key=lambda item: (-item[0][0], item[0][1], item[1]))[2]


def lookup_concept(query: str, sentences: int = 3) -> ConceptLookup:
    query = re.sub(r"\s+", " ", query).strip()[:200]
    if not query:
        return ConceptLookup(ok=False, query=query, error="empty query")
    key = f"v2|{query.lower()}|{sentences}"
    with _cache_lock:
        _load_cache()
        if key in _lookup_cache:
            return _lookup_cache[key]
    # Wikipedia's full-text search ranks badly for a whole sentence, so look for
    # articles *titled* after the words that carry meaning - the subject usually
    # comes first - and fall back to full text; keep the best-titled article.
    words = _query_words(query)
    stems = _content_words(query)
    pairs = [f"intitle:{a} intitle:{b}" for a, b in zip(words[:4], words[1:5])]
    # (searches, how good the best title must be to stop there)
    stages = [(pairs, 2.0), ([f"intitle:{words[0]}"] if words else [], 1.0), ([" ".join(words[:3])] if words else [], 0.5)]
    try:
        with httpx.Client(headers=_UA, timeout=8.0) as client:
            hits: list[dict] = []
            best = None
            for searches, good_enough in stages:
                for text in searches:
                    search = client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={"action": "query", "list": "search", "srsearch": text, "format": "json", "srlimit": 8, "utf8": 1},
                    )
                    search.raise_for_status()
                    seen = {h["title"] for h in hits}
                    hits += [h for h in search.json().get("query", {}).get("search", []) if h["title"] not in seen]
                best = _best_hit(query, hits)
                if best is not None and _title_score(stems, best["title"])[0] >= good_enough:
                    break
            if best is None:
                return ConceptLookup(ok=False, query=query, error="no article is titled after this concept")
            title = best["title"]
            summary = client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
            summary.raise_for_status()
            data = summary.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return ConceptLookup(ok=False, query=query, error=f"lookup failed: {type(exc).__name__}")
    extract = data.get("extract", "")
    parts = re.split(r"(?<=[.!?])\s+", extract)
    result = ConceptLookup(
        ok=bool(extract),
        query=query,
        title=data.get("title", title),
        summary=" ".join(parts[: max(1, min(sentences, 8))]),
        url=data.get("content_urls", {}).get("desktop", {}).get("page", ""),
    )
    with _cache_lock:
        _lookup_cache[key] = result
        try:
            _cache_file().write_text(json.dumps({k: v.model_dump() for k, v in _lookup_cache.items()}, indent=1), encoding="utf-8")
        except OSError:
            pass
    return result


# ------------------------------------------------------- computer algebra

OPERATIONS = ("parse", "simplify", "evaluate", "differentiate", "integrate", "solve", "verify")
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="sympy")
# Letters that usually name functions: f(x) is an application, n(n+1) a product.
_FUNCTION_NAMES = {"f", "g", "h", "F", "G", "H", "p", "q", "r", "u", "v", "y", "phi", "psi"}
_parser = None
_parser_lock = threading.Lock()


def _latex_parser():
    """SymPy's lark LaTeX parser, taught to resolve ambiguous notation the way a mathematician reads it.

    LaTeX like ``\\sin(x)^2`` or ``n(n+1)`` has several readings; the grammar
    returns them all. Prefer readings that do not invent functions out of
    ordinary letters, then ones that apply named functions (f, g, ...), then
    the one whose function arguments are simplest - (sin x)^2, not sin(x^2).
    """
    global _parser
    if _parser is None:
        import sympy
        from sympy.core.function import AppliedUndef
        from sympy.parsing.latex.lark.latex_parser import LarkLaTeXParser
        from sympy.parsing.latex.lark.transformer import TransformToSymPyExpr

        def score(expr) -> tuple[int, int, int]:
            applied = [str(f.func).rstrip("'") for f in expr.atoms(AppliedUndef)]
            invented = sum(1 for name in applied if name not in _FUNCTION_NAMES)
            named = sum(1 for name in applied if name in _FUNCTION_NAMES)
            arguments = sum(sympy.count_ops(arg) for f in expr.atoms(sympy.Function) for arg in f.args)
            return invented, -named, arguments

        class Disambiguating(TransformToSymPyExpr):
            def _ambig(self, options):
                readings = [o for o in options if isinstance(o, sympy.Basic)]
                return min(readings, key=score) if readings else options[0]

        _parser = LarkLaTeXParser(transformer=Disambiguating)
    return _parser


def _parse(latex: str):
    """Parse LaTeX with SymPy's lark grammar - it builds a tree, never eval()s."""
    import warnings

    source = latex.strip().strip("$").replace(r"\left", "").replace(r"\right", "")
    source = re.sub(r"\\(?:,|;|!|quad|qquad)", " ", source)
    with warnings.catch_warnings(), _parser_lock:
        warnings.simplefilter("ignore")
        expr = _latex_parser().doparse(source)
    if type(expr).__name__ == "Tree":
        raise ValueError("the notation is ambiguous; write it unambiguously")
    return expr


def _run(latex: str, operation: str, variable: str) -> MathCheck:
    import sympy

    expr = _parse(latex)
    var = sympy.Symbol(variable or "x")
    check = MathCheck(ok=True, operation=operation, input=latex, parsed=str(expr))
    if isinstance(expr, (sympy.logic.boolalg.BooleanTrue, sympy.logic.boolalg.BooleanFalse)):
        # SymPy settled the equation while parsing it: both sides are (or are never) the same.
        check.is_identity = bool(expr)
        check.result = "identity holds" if check.is_identity else "the two sides are never equal"
        return check
    if isinstance(expr, sympy.Equality) or operation == "verify":
        if not isinstance(expr, sympy.Equality):
            raise ValueError("verify needs an equation with '='")
        difference = sympy.simplify((expr.lhs - expr.rhs).doit())
        check.is_identity = bool(difference == 0)
        check.result = "identity holds" if check.is_identity else f"sides differ by {difference}"
        check.result_latex = sympy.latex(difference)
        return check
    if operation in ("parse",):
        value = expr
    elif operation in ("simplify", "evaluate"):
        value = sympy.simplify(expr.doit())
        if operation == "evaluate" and not value.free_symbols:
            value = sympy.N(value, 10)
    elif operation == "differentiate":
        value = sympy.simplify(sympy.diff(expr, var))
    elif operation == "integrate":
        value = sympy.simplify(sympy.integrate(expr, var))
    elif operation == "solve":
        value = sympy.solve(expr, var)
    else:
        raise ValueError(f"unknown operation {operation!r}; use one of {', '.join(OPERATIONS)}")
    check.result = str(value)
    check.result_latex = sympy.latex(value)
    return check


def check_math(latex: str, operation: str = "simplify", variable: str = "x") -> MathCheck:
    operation = (operation or "simplify").lower()
    try:
        return _pool.submit(_run, latex, operation, variable).result(timeout=10)
    except concurrent.futures.TimeoutError:
        return MathCheck(ok=False, operation=operation, input=latex, error="timed out after 10s")
    except Exception as exc:  # noqa: BLE001 - parse errors are results, not crashes
        return MathCheck(ok=False, operation=operation, input=latex, error=f"{type(exc).__name__}: {str(exc)[:200]}")


# --------------------------------------------------------- lesson library

_library: Library | None = None


def library() -> Library:
    global _library
    if _library is None:
        settings = get_settings()
        _library = Library(settings.data_dir / "library.db")
        _library.rebuild(settings.lessons_dir)
    return _library


def search_lessons(query: str, limit: int = 5) -> LibrarySearch:
    return LibrarySearch(query=query, hits=library().search(query, limit))
