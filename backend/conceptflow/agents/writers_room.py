"""The Writers' Room: a CrewAI crew of three agents.

Three agents with distinct roles work in sequence, each receiving the
previous one's structured output as task context:

    Concept Analyst  ->  Lesson Planner  ->  Storyboard Director
    (what matters)       (in what order)     (what is seen and said)

CrewAI is the right tool for this part: collaborative, role-driven reasoning
where each specialist's point of view shapes the next. The crew is published
as an A2A agent (``writers_room_a2a.py``) and the ADK workflow delegates to it
over the A2A protocol (``a2a_client.py``) - two frameworks, one protocol.

Each task carries a CrewAI guardrail (guardrails.py) that validates its output
and sends the agent back with feedback when it falls short. In live mode the
Concept Analyst can also call the studio's MCP tools (Wikipedia lookup and the
SymPy checker) through CrewAI's native MCP client.
"""

from __future__ import annotations

from typing import Callable

from crewai import Agent, Crew, Process, Task

from ..guardrails import guard_analysis, guard_plan, guard_storyboard, logged
from ..llm.crew_llm import RoutedCrewLLM
from ..schemas import LENGTHS, ConceptAnalysis, ConceptBrief, LessonPlan, LessonRequest, Storyboard
from ..tools.models import ConceptLookup, DocumentDigest

AUDIENCES = {
    "school": "a curious high-school student (15-18) who is comfortable with algebra, functions and basic geometry",
    "undergraduate": "a first- or second-year university student in science or engineering",
    "graduate": "a graduate student or working professional who already uses the result and wants the deep intuition behind it",
}


def _llm(temperature: float) -> RoutedCrewLLM:
    return RoutedCrewLLM(model="conceptflow/planner", role="planner", temperature=temperature)


def _sources(brief: ConceptBrief | None, document: DocumentDigest | None, grounding: ConceptLookup | None) -> str:
    parts = []
    if brief is not None:
        parts.append(
            "The learner uploaded source material. Stay consistent with it.\n"
            f"Concept distilled from it: {brief.concept}\n"
            f"Context and notation from the source: {brief.context}\n"
            f"Symbols: {'; '.join(brief.notation) or 'none listed'}"
        )
    if document is not None and document.title:
        parts.append(f"Uploaded document: \"{document.title}\" ({document.pages} pages). Sections: {'; '.join(document.sections[:12]) or 'n/a'}.")
    if grounding is not None and grounding.ok:
        parts.append(
            f"Reference summary retrieved with the lookup tool ({grounding.source}: {grounding.title}, {grounding.url}):\n"
            f"{grounding.summary}\nUse it to check facts and terminology, not as text to copy; if it is about something else, ignore it."
        )
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def build_crew(
    request: LessonRequest,
    brief: ConceptBrief | None = None,
    document: DocumentDigest | None = None,
    grounding: ConceptLookup | None = None,
    mcps: list | None = None,
    on_task_done: Callable[[int, object], None] | None = None,
) -> tuple[Crew, list[Task]]:
    audience = AUDIENCES[request.audience]
    size = LENGTHS[request.length]
    scenes_lo, scenes_hi = size["scenes"]
    beats_lo, beats_hi = size["beats"]
    scene_count = f"{scenes_lo}" if scenes_lo == scenes_hi else f"{scenes_lo} or {scenes_hi}"
    source = _sources(brief, document, grounding)
    concept = request.prompt or (brief.concept if brief else "") or (document.title if document else "")

    analyst = Agent(
        role="Concept Analyst",
        goal="Find the mathematical structure of the concept and the single insight that makes it click.",
        backstory=(
            "You are a mathematician who has taught for twenty years and thinks in pictures. You care about "
            "what a learner must already know, what the central object is, and which picture makes an idea "
            "obvious rather than merely true. You are exact: every formula you write is correct, and when a "
            "checking tool is available you verify identities with it."
        ),
        llm=_llm(0.4),
        mcps=mcps or [],
        allow_delegation=False,
        max_iter=6,
        verbose=False,
    )
    planner = Agent(
        role="Lesson Planner",
        goal="Turn the analysis into a short lesson with a hook, one idea per section and a clean takeaway.",
        backstory=(
            "You are an instructional designer who has planned hundreds of explainer videos. You believe in "
            "motivation before formalism, concrete before abstract, and exactly one new idea at a time. You "
            "cut anything the viewer does not need."
        ),
        llm=_llm(0.5),
        allow_delegation=False,
        verbose=False,
    )
    director = Agent(
        role="Storyboard Director",
        goal="Write a shot-by-shot storyboard in the visual language of 3Blue1Brown: every line of narration paired with a precise picture that Manim can animate.",
        backstory=(
            "You direct mathematical animations. You know what Manim draws beautifully: axes and graphs, number "
            "lines, vectors and grids, geometric figures, LaTeX equations that transform into each other, points "
            "sliding along curves, areas filling, highlights. You write narration that is warm, concrete and "
            "brief, and visual directions a programmer can implement without guessing."
        ),
        llm=_llm(0.7),
        allow_delegation=False,
        verbose=False,
    )

    analyse = Task(
        name="analyse",
        description=(
            f"Analyse this concept for {audience}.\n\nRequest: {concept}{source}\n\n"
            "Identify the canonical topic and its domain; the single core insight the viewer must leave with; "
            "the prerequisites that are genuinely needed; three to five key ideas in teaching order; the central "
            "definition or result in LaTeX (empty if there is none); intuitions that make it visual; common "
            "misconceptions; and visual opportunities - things that can be shown moving on axes, a number line, "
            "a grid, a geometric figure or a diagram, not merely written down. If the request is a research "
            "paper, choose its main result or its key idea and explain that, at the requested level."
        ),
        expected_output="A precise, correct concept analysis.",
        agent=analyst,
        output_pydantic=ConceptAnalysis,
        guardrail=logged(guard_analysis, "analysis"),
        guardrail_max_retries=2,
    )
    plan = Task(
        name="plan",
        description=(
            f"Using the Concept Analyst's analysis, plan a lesson of {size['minutes']} minutes for {audience}. "
            "Open with a hook: a concrete question the viewer genuinely wants answered. Build the idea in "
            f"{scene_count} sections - each section becomes one animated scene, so each needs a goal, a short "
            "explanation, its key formula in LaTeX if it has one, and a visual idea. The last section lands the "
            "takeaway. Address the most important misconception somewhere. Finish with a quiz of three "
            "questions, each with exactly four choices, the index of the correct one, and a one-line reason."
        ),
        expected_output="A lesson plan with hook, objectives, sections, takeaway and quiz.",
        agent=planner,
        context=[analyse],
        output_pydantic=LessonPlan,
        guardrail=logged(guard_plan, "plan"),
        guardrail_max_retries=2,
    )
    storyboard = Task(
        name="storyboard",
        description=(
            f"Turn the Lesson Planner's plan into a storyboard of exactly {scene_count} scenes - one per section - "
            f"with {beats_lo} to {beats_hi} beats per scene. Scene ids are s1, s2, ...; beat ids are s1b1, s1b2, ... "
            "A beat is one line of narration plus the picture that goes with it.\n\n"
            "Narration: one or two spoken sentences, at most 32 words, conversational and concrete, in English. "
            "It will be read aloud, so never write LaTeX or symbols: say 'x squared', 'the limit as h goes to zero', "
            "'f of x'. Scene 1, beat 1 greets the viewer with the hook question.\n\n"
            "Visual: exactly what is on screen during that line and how it moves - which objects, where "
            "(left/right/centre), which colour role (PRIMARY blue for the main object, SECONDARY teal for its "
            "counterpart, HIGHLIGHT yellow for what is being pointed at, ACCENT terracotta for the result, MUTED "
            "grey for annotations), and the motion (draws, slides, transforms into, zooms to). Write any on-screen "
            "formula in LaTeX. Scene 1 opens with a title card. Everything must be drawable in 2D Manim: no 3D, "
            "no photographs, no real-world objects beyond simple shapes. Keep at most four pieces of text on "
            "screen, reuse and transform objects within a scene rather than replacing them, and give the eye one "
            "focal point at a time. State the colour story (which colour means what) and keep it consistent."
        ),
        expected_output="A complete storyboard with scenes and beats.",
        agent=director,
        context=[analyse, plan],
        output_pydantic=Storyboard,
        guardrail=logged(guard_storyboard, "storyboard"),
        guardrail_max_retries=2,
    )
    tasks = [analyse, plan, storyboard]
    if on_task_done is not None:
        for i, task in enumerate(tasks):
            task.callback = (lambda index: (lambda output: on_task_done(index, output)))(i)
    crew = Crew(agents=[analyst, planner, director], tasks=tasks, process=Process.sequential, verbose=False)
    return crew, tasks


# ------------------------------------------------------------ hand-off notes


def analysis_note(a: ConceptAnalysis) -> str:
    pre = ", ".join(a.prerequisites[:4]) or "none"
    return f"Core insight: {a.core_insight} Prerequisites: {pre}. {len(a.visual_opportunities)} things we can show moving."


def plan_note(p: LessonPlan) -> str:
    return f"\"{p.title}\" opens on: {p.hook} {len(p.sections)} sections, then a {len(p.quiz)}-question quiz."


def storyboard_note(s: Storyboard) -> str:
    beats = sum(len(sc.beats) for sc in s.scenes)
    return f"{len(s.scenes)} scenes, {beats} beats. Colour story: {s.color_story}"
