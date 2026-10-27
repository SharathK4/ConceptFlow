/**
 * Three views of how a lesson was made, for the production log:
 *
 * - Performance: where the time went, every model call and tool call, the
 *   guardrails that ran, and what the lesson cost (free tier and paid tier).
 * - Trace: the lesson's OpenTelemetry spans as a waterfall - ADK agents, the
 *   A2A exchange, the CrewAI crew, MCP tools and model calls in one trace.
 * - Provenance: every artifact the agents handed on, who made it, with which
 *   model, and the hashes of the artifacts it was made from.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { agentLabel, count, seconds, usd } from '../lib/format'
import type { Metrics, ProvenanceRecord, Span, TraceData } from '../lib/types'

function useFetched<T>(load: () => Promise<T>, key: string): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let current = true
    setData(null)
    setError(null)
    load()
      .then((value) => current && setData(value))
      .catch((exc) => current && setError(exc instanceof Error ? exc.message : String(exc)))
    return () => {
      current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return { data, error }
}

// ------------------------------------------------------------- performance

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <span className="tile__label">{label}</span>
      <span className="tile__value num">{value}</span>
      {note ? <span className="tile__note">{note}</span> : null}
    </div>
  )
}

export function PerformanceView({ id, running }: { id: string; running: boolean }) {
  const { data: m, error } = useFetched<Metrics>(() => api.metrics(id), `${id}-${running}`)
  if (error) return <p className="log__empty">{error}</p>
  if (!m) return <p className="log__empty">Measuring…</p>
  const longest = Math.max(1, ...m.stages.map((s) => s.seconds))
  const tools = Object.entries(m.tools.by_tool)
  const roles = Object.entries(m.llm.by_role)
  const guards = Object.entries(m.guardrails.by_stage)
  return (
    <div className="perf">
      {running ? <p className="perf__live">Still in production - measured so far.</p> : null}
      <div className="perf__tiles">
        <Tile label="Wall time" value={seconds(m.wall_seconds)} note={m.realtime_factor ? `${m.realtime_factor.toFixed(2)}× the video's length` : undefined} />
        <Tile label="Model calls" value={count(m.llm.calls)} note={`${count(m.llm.tokens_in + m.llm.tokens_out)} tokens${m.llm.estimated ? ', estimated' : ''}`} />
        <Tile label="MCP tool calls" value={count(m.tools.calls)} note={tools.map(([name]) => name).join(' · ') || undefined} />
        <Tile label="A2A round trip" value={seconds(m.a2a.seconds)} note={m.a2a.stream_updates ? `${m.a2a.stream_updates} streamed task updates` : undefined} />
        <Tile label="Rendering" value={seconds(m.render.render_seconds)} note={`${m.render.scene_renders} scene renders · ${m.render.rounds} round${m.render.rounds === 1 ? '' : 's'}`} />
        <Tile label="Cost" value={usd(m.cost.free_tier_usd)} note={`on the free tier · ${usd(m.cost.paid_tier_total_usd)} at paid-tier prices`} />
      </div>

      <section className="perf__block">
        <h3 className="perf__title">Where the time went</h3>
        <ol className="bars">
          {m.stages.map((stage) => (
            <li key={stage.agent} className="bars__row">
              <span className="bars__label">
                {agentLabel(stage.agent)}
                {stage.runs > 1 ? <span className="bars__runs num"> ×{stage.runs}</span> : null}
              </span>
              <span className={`bars__fw is-${stage.framework ?? 'adk'}`}>{stage.framework === 'crewai' ? 'CrewAI' : 'ADK'}</span>
              <span className="bars__track">
                <span className={`bars__fill is-${stage.framework ?? 'adk'}`} style={{ width: `${(100 * stage.seconds) / longest}%` }} />
              </span>
              <span className="bars__value num">{seconds(stage.seconds)}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="perf__block">
        <h3 className="perf__title">Model calls</h3>
        <table className="ledger">
          <thead>
            <tr>
              <th>Role</th>
              <th>Calls</th>
              <th>Tokens in</th>
              <th>Tokens out</th>
              <th>Time</th>
              <th>Paid tier</th>
            </tr>
          </thead>
          <tbody>
            {roles.map(([role, row]) => (
              <tr key={role}>
                <td>{role.replace(/_/g, ' ')}</td>
                <td className="num">{row.calls}</td>
                <td className="num">{count(row.tokens_in)}</td>
                <td className="num">{count(row.tokens_out)}</td>
                <td className="num">{seconds(row.seconds)}</td>
                <td className="num">{usd(row.usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="perf__note">
          By framework:{' '}
          {Object.entries(m.llm.by_framework)
            .map(([fw, row]) => `${fw} ${row.calls} call${row.calls === 1 ? '' : 's'}`)
            .join(' · ')}
          {m.llm.failed_calls ? ` · ${m.llm.failed_calls} call(s) retried on another model` : ''}
        </p>
      </section>

      <div className="perf__pair">
        <section className="perf__block">
          <h3 className="perf__title">Tools (MCP)</h3>
          <table className="ledger">
            <thead>
              <tr>
                <th>Tool</th>
                <th>Calls</th>
                <th>Called by</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {tools.map(([name, row]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td className="num">{row.calls}</td>
                  <td>{row.callers.join(', ')}</td>
                  <td className="num">{seconds(row.seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="perf__block">
          <h3 className="perf__title">Guardrails</h3>
          <table className="ledger">
            <thead>
              <tr>
                <th>Check</th>
                <th>Ran</th>
                <th>Intervened</th>
              </tr>
            </thead>
            <tbody>
              {guards.map(([stage, row]) => (
                <tr key={stage}>
                  <td>{stage}</td>
                  <td className="num">{row.checks}</td>
                  <td className={`num${row.interventions ? ' is-flag' : ''}`}>{row.interventions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <p className="perf__note">
        Prices as of {m.cost.prices_as_of}. {m.cost.assumptions.join('. ')}.
      </p>
    </div>
  )
}

// ------------------------------------------------------------------- trace

const SCOPE_LABEL: Record<string, string> = {
  conceptflow: 'studio',
  'gcp.vertex.agent': 'ADK',
  'a2a-python-sdk': 'A2A',
}

function scopeClass(scope: string): string {
  if (scope === 'gcp.vertex.agent') return 'is-adk'
  if (scope.startsWith('a2a')) return 'is-a2a'
  if (scope === 'conceptflow') return 'is-studio'
  return 'is-other'
}

function spanLabel(span: Span): string {
  const a = span.attributes
  const agent = a['gen_ai.agent.name'] ?? a['conceptflow.role'] ?? a['mcp.tool'] ?? a['a2a.agent']
  return agent && !span.name.includes(String(agent)) ? `${span.name} · ${String(agent)}` : span.name
}

export function TraceView({ id }: { id: string }) {
  const { data, error } = useFetched<TraceData>(() => api.trace(id), id)
  const rows = useMemo(() => {
    const spans = (data?.spans ?? []).filter((s) => s.start && s.end)
    if (!spans.length) return { list: [] as (Span & { depth: number })[], t0: 0, total: 1 }
    const byId = new Map(spans.map((s) => [s.span_id, s]))
    const depth = (s: Span): number => {
      let d = 0
      let parent = s.parent_id ? byId.get(s.parent_id) : undefined
      while (parent && d < 12) {
        d += 1
        parent = parent.parent_id ? byId.get(parent.parent_id) : undefined
      }
      return d
    }
    const t0 = Math.min(...spans.map((s) => s.start as number))
    const t1 = Math.max(...spans.map((s) => s.end as number))
    const list = spans
      .map((s) => ({ ...s, depth: depth(s) }))
      .sort((a, b) => (a.start as number) - (b.start as number))
    return { list, t0, total: Math.max(0.001, t1 - t0) }
  }, [data])

  if (error) return <p className="log__empty">{error}</p>
  if (!data) return <p className="log__empty">Loading the trace…</p>
  if (!rows.list.length) return <p className="log__empty">No spans were recorded for this lesson (lessons made before tracing was added have none).</p>
  const shown = rows.list.slice(0, 260)
  const scopes = Object.entries(
    rows.list.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s.scope]: (acc[s.scope] ?? 0) + 1 }), {}),
  )
  return (
    <div className="trace">
      <p className="trace__head">
        <span className="mono">trace {data.trace_id ?? rows.list[0].trace_id}</span>
        <span>{rows.list.length} spans</span>
        {scopes.map(([scope, n]) => (
          <span key={scope} className={`trace__scope ${scopeClass(scope)}`}>
            {SCOPE_LABEL[scope] ?? scope} {n}
          </span>
        ))}
        <span className="trace__export">
          exported to {data.exporters.map((e) => (e === 'lesson-files' ? 'trace.jsonl' : e === 'langfuse' ? 'Langfuse' : e)).join(' + ')}
          {data.exporters.includes('langfuse') ? '' : ' · add LANGFUSE keys to send it to Langfuse'}
        </span>
      </p>
      <ol className="waterfall">
        {shown.map((span) => {
          const start = ((span.start as number) - rows.t0) / rows.total
          const width = ((span.end as number) - (span.start as number)) / rows.total
          return (
            <li key={span.span_id} className={`waterfall__row${span.status === 'ERROR' ? ' is-error' : ''}`}>
              <span className="waterfall__name" style={{ paddingLeft: `${span.depth * 12}px` }} title={span.name}>
                {spanLabel(span)}
              </span>
              <span className="waterfall__track">
                <span className={`waterfall__bar ${scopeClass(span.scope)}`} style={{ left: `${start * 100}%`, width: `${Math.max(0.35, width * 100)}%` }} />
              </span>
              <span className="waterfall__dur num">{seconds((span.end as number) - (span.start as number))}</span>
            </li>
          )
        })}
      </ol>
      {rows.list.length > shown.length ? <p className="perf__note">Showing the first {shown.length} of {rows.list.length} spans; all of them are in trace.jsonl.</p> : null}
    </div>
  )
}

// -------------------------------------------------------------- provenance

export function ProvenanceView({ id }: { id: string }) {
  const { data, error } = useFetched<ProvenanceRecord[]>(() => api.provenance(id), id)
  const [focus, setFocus] = useState<string | null>(null)
  if (error) return <p className="log__empty">{error}</p>
  if (!data) return <p className="log__empty">Loading the provenance record…</p>
  if (!data.length) return <p className="log__empty">No provenance was recorded for this lesson (lessons made before it was added have none).</p>
  const nameOf = new Map(data.map((r) => [r.sha, r.artifact]))
  return (
    <div className="prov">
      <p className="perf__note">
        Each artifact carries a content hash and the hashes of what it was made from, so any output can be traced back to its inputs. Hover a hash to
        follow it.
      </p>
      <ol className="prov__list">
        {data.map((r) => (
          <li key={`${r.artifact}-${r.sha}`} className={`prov__row${focus === r.sha ? ' is-focus' : ''}`} onMouseEnter={() => setFocus(r.sha)} onMouseLeave={() => setFocus(null)}>
            <div className="prov__what">
              <span className="prov__artifact">{r.artifact.replace(/_/g, ' ')}</span>
              <span className="prov__by">
                {agentLabel(r.producer)} · {r.framework}
                {r.model ? ` · ${r.model}` : ''}
              </span>
              {r.note ? <span className="prov__note">{r.note}</span> : null}
            </div>
            <span className="prov__sha mono">{r.sha}</span>
            <div className="prov__inputs">
              {Object.entries(r.inputs).length ? (
                Object.entries(r.inputs).map(([name, sha]) => (
                  <span
                    key={name}
                    className={`prov__input${focus === sha ? ' is-focus' : ''}`}
                    onMouseEnter={() => setFocus(sha)}
                    title={nameOf.has(sha) ? `made from ${nameOf.get(sha)}` : 'an input from outside the record'}
                  >
                    {name} <span className="mono">{sha.slice(0, 8)}</span>
                  </span>
                ))
              ) : (
                <span className="prov__input is-source">source</span>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
