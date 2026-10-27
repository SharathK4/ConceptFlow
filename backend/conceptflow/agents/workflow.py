"""The studio floor: one Google ADK 2 ``Workflow`` graph for a whole lesson.

                          +--> intake_reader --+
    START -> intake ------+                    +--> writers_room ==A2A==> CrewAI crew
            (MCP: parse,  +--------------------+     (MCP: lookup)        (analyst -> planner -> director)
             library)                                     |
                                                          +--> narrator ------+
                                                          +--> manim_coder ---+--> production_join -> render
                                                                                                        |
             +-------------------------- "broken" (a scene failed) <------------------------------------+
             |                                                                                          |
             |        "rendered" --> visual_critic ---------------------------+                         |
             |                   --> verify_formulas --> pedagogy_critic -----+--> review_join -> verdict
             |                       (MCP: SymPy)                                                |      |
             v                                                                                   |      |
          refiner <------------------------------------ "revise" --------------------------------+      |
             |                                                                                   |      | "exhausted"
             +-----> render (again)                                                  "approve"   v      v
                                                                                              final_cut

* agent-to-agent: the writers' room is a CrewAI crew published as an A2A
  agent; the ADK node ``writers_room`` is its A2A client (``a2a_client.py``);
* tools: intake, grounding and formula checks call the MCP tool server;
* fan-out / join: narration and coding run in parallel, as do the two critics;
* conditional routes: nodes set ``ctx.route`` and the graph follows the match;
* the correction cycle refiner -> render -> critics -> verdict is a real cycle
  in the graph, bounded by MAX_REVISIONS.
"""

from __future__ import annotations

from google.adk.workflow import JoinNode, Workflow

from .a2a_client import WritersRoomClient
from .production import final_cut, intake, llm_agents, narrator, render, verdict, verify_formulas

NODES = [
    # id, label, framework, role
    ("intake", "Intake", "adk", "Parses uploads and checks the library (MCP)"),
    ("intake_reader", "Intake Reader", "adk", "Distils uploaded notes, papers and photos"),
    ("concept_analyst", "Concept Analyst", "crewai", "Finds the structure and the core insight"),
    ("lesson_planner", "Lesson Planner", "crewai", "Orders ideas into a lesson with a hook and quiz"),
    ("storyboard_director", "Storyboard Director", "crewai", "Pairs every line of narration with a picture"),
    ("narrator", "Narrator", "adk", "Translates and voices each line; sets the clock"),
    ("manim_coder", "Manim Coder", "adk", "Writes the animation in Manim"),
    ("renderer", "Renderer", "adk", "Renders scenes in parallel; checks layout"),
    ("visual_critic", "Visual Critic", "adk", "Looks at a keyframe of every beat"),
    ("formula_checker", "Formula Check", "adk", "Verifies every formula with SymPy (MCP)"),
    ("pedagogy_critic", "Maths & Pedagogy Critic", "adk", "Checks correctness, order and pacing"),
    ("refiner", "Refiner", "adk", "Fixes errors and review notes"),
    ("editor", "Editor", "adk", "Final render, voice, captions, chapters"),
]


def build_workflow() -> Workflow:
    agents = llm_agents()
    writers_room = WritersRoomClient(
        name="writers_room",
        description="A2A client of the CrewAI writers' room: analyst, planner, storyboard director.",
    )
    production_join = JoinNode(name="production_join")
    review_join = JoinNode(name="review_join")

    return Workflow(
        name="conceptflow",
        description="From a concept to a narrated, reviewed Manim lesson.",
        edges=[
            ("START", intake),
            (intake, {"upload": agents["reader"], "direct": writers_room}),
            (agents["reader"], writers_room),
            (writers_room, (narrator, agents["coder"]), production_join, render),
            (
                render,
                {
                    "rendered": (agents["visual_critic"], verify_formulas),
                    "broken": agents["fixer"],
                    "exhausted": final_cut,
                },
            ),
            (verify_formulas, agents["pedagogy_critic"]),
            ((agents["visual_critic"], agents["pedagogy_critic"]), review_join, verdict),
            (verdict, {"revise": agents["fixer"], "approve": final_cut}),
            (agents["fixer"], render),
        ],
    )
