# Minimum requirements: where each one is met

Each row says what the requirement asks for, how ConceptFlow AI meets it, where
the code is, and how to see it working. All test files are in `backend/tests/`.
Run them with `pytest backend/tests -q`, and add `--slow` for the end-to-end run.

| # | Requirement | Status |
|---|---|---|
| 1 | Context sharing and state updates | Done |
| 2 | Two or more external tools over MCP | Done: four tools |
| 3 | Structured output with Pydantic | Done |
| 4 | Agentic design patterns | Done |
| 5 | Monitoring and logging with callbacks | Done |
| 6 | A2A between an ADK agent and a CrewAI agent | Done |
| 7 | CrewAI and ADK only | Done |
| 8 | Guardrails and validation callbacks | Done |
| 9 | Observability: tracing, audit, provenance | Done (Langfuse once keys are added) |
| 10 | End-to-end evaluation, cost and time | Done: `docs/evaluation.md` |
| 11 | Cloud deployment | Ready: Cloud Run or Hugging Face Spaces. You run the deploy. |

---

## 1. Context sharing and state updates

Intermediate outputs travel between agents through **ADK session state**, and
inside the crew through **CrewAI task context**.

- **ADK state**: `intake` writes `document` and `related`. The reader writes
  `brief` through `output_key`. The writers' room client writes `analysis`,
  `plan`, `storyboard`, `grounding` and `title`. The narrator writes
  `narration`; the coder and refiner write `code_draft`; the renderer writes
  `render` and `round`; the formula checker writes `math_checks`. The critics
  write `visual_review` and `pedagogy_review`, the verdict writes `reviews`,
  and the editor writes `final`.
- **Reading state**: every downstream agent builds its prompt from it. Each
  `LlmAgent` has an `instruction=` callable that receives the `ReadonlyContext`.
  An example is `_pedagogy_instruction` in
  `backend/conceptflow/agents/production.py`.
- **CrewAI context**: `plan` has `context=[analyse]` and `storyboard` has
  `context=[analyse, plan]` in `agents/writers_room.py`. Each task receives the
  previous tasks' Pydantic outputs.
- **Evidence**: `test_pipeline_replay.py` (`--slow`) runs the whole graph. On
  the page, *Production log → The conversation* shows every hand-off.

## 2. Tool integration with MCP: four tools

`backend/conceptflow/tools/server.py` is a FastMCP server with four tools. Each
returns a Pydantic model, published as a structured-output schema.

| Tool | Kind | Used by |
|---|---|---|
| `read_document` | file parser (pypdf) | the intake node, on every upload, and the upload preview |
| `lookup_concept` | web knowledge (Wikipedia REST API) | grounding before the crew; the CrewAI analyst in live mode |
| `check_math` | calculator / computer algebra (SymPy) | the Formula Check node; the ADK maths critic and CrewAI analyst in live mode |
| `search_lessons` | database lookup (SQLite FTS5) | the intake node: "the library already has…" |

The tools are reached three ways, all over MCP:

- **Workflow steps** use an MCP client session (`tools/client.py`).
- **ADK** uses `McpToolset` over Streamable HTTP at `/mcp/` (`_critic_tools`).
- **CrewAI** uses its native `mcps=[MCPServerHTTP(...)]` (`writers_room_a2a.py`).

**Evidence**: `test_tools.py` and `test_server.py`. The latter tests a plain
MCP client, ADK's `McpToolset` and CrewAI's MCP client against the running
server. On the page, the MCP tools row under the agent graph lights up on each
call, and *Performance → Tools* counts the calls.

## 3. Structured output with Pydantic

- **Hand-offs between agents** are Pydantic models in `schemas.py`:
  `ConceptBrief`, `ConceptAnalysis`, `LessonPlan`, `Storyboard`,
  `VisualReview` and `PedagogyReview`. The same classes serve as CrewAI
  `output_pydantic`, ADK `output_schema` and Gemini response schemas.
- **Protocol payloads**: `LessonDesignRequest` and `LessonDesign` are the A2A
  `DataPart`s. The tool results are `DocumentDigest`, `ConceptLookup`,
  `MathCheck` and `LibrarySearch`.
- **Final output**: `FinalReport` in `report.py` is validated and saved as
  `report.json` (JSON) and `report.md` (Markdown with tables). It covers
  chapters, quiz, sources, agent steps, review rounds, formula checks,
  guardrails, performance, cost and provenance.
- **Evidence**: `test_monitoring.py::test_report_is_validated_and_rendered_as_markdown_tables`.
  On the page, *Production log → The structured report → Markdown / JSON*.
  Also `GET /api/lessons/{id}/report` and `?format=md`.

## 4. Agentic design patterns

| Pattern | Where |
|---|---|
| Graph orchestration with routing | ADK `Workflow`: `ctx.route` on intake (`upload`/`direct`), render (`rendered`/`broken`/`exhausted`) and verdict (`approve`/`revise`) |
| Parallel fan-out / fan-in | narrator ‖ coder → `production_join`; visual critic ‖ (formula check → maths critic) → `review_join` |
| Sequential role-based crew | CrewAI: Concept Analyst → Lesson Planner → Storyboard Director |
| Generator–critic reflection loop | coder → render → two critics → verdict → refiner → render, bounded by `MAX_REVISIONS` |
| Tool use / grounding (ReAct in live mode) | MCP tools before and inside agents |
| Delegation over a protocol | ADK delegates lesson design to the CrewAI crew over A2A |
| Guardrail-and-retry | CrewAI task guardrails send an agent back with feedback (`guardrail_max_retries=2`) |
| Graceful degradation | model ladders with quota-aware fallback; fallback scenes; Edge voice when the others are unavailable |

## 5. Task monitoring and logging with callbacks

- **ADK `MonitoringPlugin`** (`monitoring.py`) implements the run, event,
  agent, model, tool and error callbacks: `before/after_run`,
  `on_event` (routes), `before/after_agent`, `before/after_model` (latency,
  tokens, finish reason, tool calls), `on_model_error`, `before/after_tool`,
  `on_tool_error`, `on_agent_error` and `on_run_error`. It is installed on the
  `App` in `jobs.py`.
- **CrewAI event-bus listeners** cover task started, completed and failed;
  agent errors; and tool usage started, finished and failed. CrewAI **task
  callbacks** stream each finished task back over A2A.
- **LLM calls** in both frameworks are recorded with tokens and latency by the
  shared router.
- **Output files**: `data/lessons/<id>/monitor.jsonl` (structured, per lesson)
  and `data/logs/conceptflow.log` (rotating). Warnings and errors also reach
  the page's live feed.
- **Evidence**: `test_monitoring.py`. On the page, *Production log →
  Performance*. Also `GET /api/lessons/{id}/monitor`.

## 6. A2A: an ADK agent talks to a CrewAI agent

- **Server**: `agents/writers_room_a2a.py` publishes the CrewAI crew as an A2A
  agent with a2a-sdk. It serves an Agent Card at
  `/a2a/writers-room/.well-known/agent-card.json` (skill `design_lesson`,
  streaming) and JSON-RPC at `/a2a/writers-room/`. The `AgentExecutor` streams
  `TaskStatusUpdateEvent`s: which crew agent is working, their hand-off notes
  and the models used. The result is an artifact named `lesson_design`.
- **Client**: `agents/a2a_client.py` is the ADK workflow node `writers_room`.
  It resolves the Agent Card, sends one `Message` with a JSON `DataPart`, and
  relays the streamed updates to the page. It type-checks the artifact as
  `LessonDesign`, then writes the result into ADK state. The W3C trace context
  travels in the message metadata, so both sides appear in one trace.
- **Evidence**: `test_a2a.py` covers the card, the streamed task, the artifact
  and a failed task. On the page, the "VIA A2A PROTOCOL" marker under the
  CrewAI group shows "streaming" and then "design received". The A2A steps are
  in the conversation, and A2A spans are in *Trace*.

## 7. CrewAI and ADK only

No other agent framework is used. MCP and A2A are protocols, served and called
through their reference SDKs (`mcp`, `a2a-sdk`). `requirements.txt` lists the
dependencies.

## 8. Guardrails, security and validation callbacks

The guardrails are layered. All rules are in `guardrails.py`.

- **Input**: `screen_prompt` runs at `POST /api/lessons`. It refuses prompt
  injection (override, extraction, role hijack, spoofed roles, chat-template
  tokens, secret exfiltration, code injection), abusive or threatening
  language, and harmful requests. It normalises Unicode first, which defeats
  zero-width and full-width tricks. Personal data (emails, phone numbers,
  Luhn-valid card numbers, API keys) is redacted rather than refused.
- **Context**: `screen_document` removes instruction-like lines hidden in
  uploads. A flagged file reaches the model only as cleaned text, never as the
  raw file. The ADK `GuardrailPlugin.before_model_callback` screens every model
  input again.
- **Tool calls**: `GuardrailPlugin.before_tool_callback` validates arguments
  (TeX file commands, length limits, paths outside the upload folder).
  `after_tool_callback` type-checks every tool's return value against its
  Pydantic model (`tools/validate.py`). The workflow's own MCP calls are
  checked the same way, and the A2A artifact is validated at the protocol
  boundary.
- **Agents**: CrewAI task guardrails `guard_analysis`, `guard_plan` and
  `guard_storyboard` validate each crew output and make the agent retry with
  feedback.
- **Code**: the sandbox (`render/sandbox.py`) rejects generated Python that
  reaches outside Manim, and LaTeX file commands.
- **Output**: `screen_narration` checks the narration before anything is voiced.
- **Evidence**: `test_guardrails.py` (34 cases), `test_server.py` (the API
  refuses attacks), and the red-team section of `docs/evaluation.md`: 12 of 12
  attacks refused, 0 of 12 maths prompts wrongly blocked. On the page, the
  refusal reasons appear under the button, and *Performance → Guardrails* shows
  every check.

## 9. Observability: tracing, audit and provenance

- **Tracing**: OpenTelemetry (`observability.py`). Each lesson is one trace
  with a root span `lesson`, which carries Langfuse session and trace
  attributes. It contains ADK's own spans (agents, model calls, tools), the A2A
  client and server spans, the CrewAI crew span, MCP tool spans and the
  router's model-call spans, which carry `gen_ai.*` token usage.
- **Exporters**: every lesson's spans go to `trace.jsonl`. With
  `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set, they also go to
  **Langfuse** over OTLP/HTTP; any OTLP collector also works via
  `OTEL_EXPORTER_OTLP_ENDPOINT`.
- **Provenance**: `provenance.jsonl` has one record per artifact. Each record
  has a content hash, the producing agent, framework and model, the hashes of
  its inputs, and the trace and span ids. The chain is grounding → analysis →
  plan → storyboard → narration and code → reviews → video.
- **Evidence**: `test_pipeline_replay.py` checks that there is one trace id
  across both frameworks and that provenance is complete. On the page,
  *Production log → Trace* (waterfall) and *→ Provenance*.

## 10. End-to-end evaluation, cost and running time

`scripts/evaluate.py` sends lessons through the real API and follows the same
event stream the page uses. It reads each lesson's metrics and report, runs the
red-team set, and writes `docs/evaluation.md` and `docs/evaluation.json`. The
results include:

- wall time and time per stage;
- model calls, tokens and latency by role and by framework;
- tool calls and the A2A round trip;
- render time and revision rounds;
- cost per lesson: $0 on the free tier, plus a projection at paid-tier list
  prices for Gemini, voice and hosting;
- free-tier capacity, by simulating the model ladders against daily quotas;
- projected live running time.

Every lesson also gets its own `metrics.json`, shown in *Production log →
Performance*. The committed `docs/evaluation.md` was made in replay mode.
Running it with a Gemini key replaces the estimates with measurements.

## 11. Cloud deployment

There is one container: `Dockerfile`, with `constraints.txt` pinning the tested
versions. It serves the page, the API, the A2A agent and the MCP server on
`$PORT`. There are two step-by-step guides:

- `deploy/huggingface-spaces.md`: free, no credit card.
- `deploy/cloud-run.md`: Google Cloud Run, with the keys in Secret Manager.

`scripts/evaluate.py --url <deployed URL>` evaluates the deployment itself.
