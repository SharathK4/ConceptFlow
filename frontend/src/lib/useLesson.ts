/**
 * Follow one lesson: its details, and the live stream of what every agent is
 * doing. The stream is folded into a `Production` - which agents are working,
 * who said what to whom, which frames have rendered, how the review went -
 * which is everything the stage needs to draw the studio at work.
 */

import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { DocumentDigest, LessonDetail, LessonStatus, Manifest, MathCheckRow, ReviewRecord, StudioEvent } from './types'

export interface FeedItem {
  seq: number
  t: number
  kind: 'message' | 'log' | 'scene' | 'review' | 'model' | 'progress' | 'tool' | 'a2a' | 'guardrail'
  from?: string
  to?: string
  text: string
  tone?: 'error' | 'good' | 'quiet'
}

export interface Frame {
  beat: string
  scene: string
  url: string
  round: number
}

export interface Packet {
  id: number
  from: string
  to: string
}

export interface Production {
  status: LessonStatus | 'connecting'
  mode?: string
  showcase?: string | null
  replayTitle?: string | null
  agents: Record<string, 'active' | 'done'>
  feed: FeedItem[]
  frames: Frame[]
  round: number
  scenes: Record<string, { status: string; error?: string | null }>
  reviews: ReviewRecord[]
  models: Record<string, string>
  packets: Packet[]
  lastT: number
  lastAt: number
  queuePosition?: number
  error?: string
  progress?: { agent: string; done: number; total: number; text: string }
  manifest?: Manifest
  sheets: string[]
  /** MCP tool calls so far, by tool; ``toolPulse`` is the latest live one (drives the graph). */
  tools: Record<string, number>
  toolPulse?: { name: string; id: number }
  /** The A2A exchange with the CrewAI writers' room: discovered -> sent -> completed. */
  a2a?: string
  traceId?: string
  document?: DocumentDigest
  mathChecks?: { round: number; checks: MathCheckRow[]; counts: Record<string, number> }
  headline?: { wall_seconds?: number; llm_calls?: number; tokens?: number; tool_calls?: number; paid_tier_usd?: number }
}

export const EMPTY: Production = {
  status: 'connecting',
  agents: {},
  feed: [],
  frames: [],
  round: 0,
  scenes: {},
  reviews: [],
  models: {},
  packets: [],
  lastT: 0,
  lastAt: 0,
  sheets: [],
  tools: {},
}

const ROLE_OF_AGENT: Record<string, string> = {
  concept_analyst: 'planner',
  lesson_planner: 'planner',
  storyboard_director: 'planner',
  manim_coder: 'coder',
  refiner: 'fixer',
  visual_critic: 'visual_critic',
  pedagogy_critic: 'pedagogy_critic',
  intake_reader: 'reader',
}

export function modelFor(production: Production, agent: string): string | undefined {
  const role = ROLE_OF_AGENT[agent]
  return role ? production.models[role] : undefined
}

function push(feed: FeedItem[], item: FeedItem): FeedItem[] {
  const next = feed.length > 400 ? feed.slice(-300) : feed.slice()
  next.push(item)
  return next
}

export function reduce(p: Production, e: StudioEvent, live: boolean): Production {
  const next: Production = { ...p, lastT: e.t ?? p.lastT, lastAt: Date.now() }
  const s = (key: string) => String(e[key] ?? '')

  switch (e.type) {
    case 'queued':
      next.status = 'queued'
      next.mode = s('mode')
      next.showcase = (e.showcase as string | null) ?? null
      next.queuePosition = Number(e.position ?? 0)
      break
    case 'started':
      next.status = 'running'
      next.mode = s('mode')
      next.replayTitle = (e.replay_title as string | null) ?? null
      next.traceId = s('trace_id') || undefined
      break
    case 'tool': {
      const name = s('tool')
      next.tools = { ...p.tools, [name]: (p.tools[name] ?? 0) + 1 }
      if (live) next.toolPulse = { name, id: e.seq }
      next.feed = push(p.feed, { seq: e.seq, t: e.t, kind: 'tool', from: 'mcp', text: `${name} answered in ${Number(e.seconds ?? 0).toFixed(2)} s`, tone: 'quiet' })
      break
    }
    case 'a2a': {
      const phase = s('phase')
      next.a2a = phase
      const text =
        phase === 'discovered'
          ? `Found \u201c${s('agent')}\u201d from its Agent Card`
          : phase === 'sent'
            ? 'Sent the lesson request as an A2A message (a JSON DataPart)'
            : `Received the lesson design in ${Number(e.seconds ?? 0).toFixed(1)} s, after ${String(e.updates ?? 0)} streamed task updates`
      next.feed = push(p.feed, { seq: e.seq, t: e.t, kind: 'a2a', from: 'writers_room', text })
      break
    }
    case 'guardrail': {
      const action = s('action')
      if (action !== 'allow') {
        const reasons = ((e.reasons as string[] | undefined) ?? []).join('; ')
        next.feed = push(p.feed, {
          seq: e.seq,
          t: e.t,
          kind: 'guardrail',
          from: 'guardrails',
          text: `${s('stage')}: ${action === 'sanitize' ? 'cleaned' : 'blocked'}${reasons ? ` \u2013 ${reasons}` : ''}`,
          tone: action === 'block' ? 'error' : undefined,
        })
      }
      break
    }
    case 'agent': {
      const agent = s('agent')
      next.agents = { ...p.agents, [agent]: e.status === 'start' ? 'active' : 'done' }
      if (agent === 'renderer' && e.status === 'start' && typeof e.round === 'number') next.round = e.round
      break
    }
    case 'message':
      next.feed = push(p.feed, { seq: e.seq, t: e.t, kind: 'message', from: s('from'), to: s('to'), text: s('text') })
      if (live) next.packets = [...p.packets.slice(-12), { id: e.seq, from: s('from'), to: s('to') }]
      break
    case 'log':
      next.feed = push(p.feed, { seq: e.seq, t: e.t, kind: 'log', from: s('agent'), text: s('text'), tone: 'quiet' })
      break
    case 'progress':
      next.progress = { agent: s('agent'), done: Number(e.done), total: Number(e.total), text: s('text') }
      break
    case 'model': {
      const status = s('status')
      if (status === 'ok') next.models = { ...p.models, [s('role')]: s('model') }
      if (status === 'daily' || status === 'waiting' || status === 'busy' || status === 'missing') {
        const why =
          status === 'daily'
            ? 'is out of free quota for today - moving down the ladder'
            : status === 'waiting'
              ? `asked us to wait ${Math.round(Number(e.seconds ?? 0))}s (per-minute limit)`
              : status === 'missing'
                ? 'is not available to this key'
                : 'is busy - trying the next model'
        next.feed = push(p.feed, { seq: e.seq, t: e.t, kind: 'model', from: s('role'), text: `${s('model')} ${why}`, tone: 'quiet' })
      }
      break
    }
    case 'scene': {
      const scene = s('scene')
      const status = s('status')
      next.scenes = { ...p.scenes, [scene]: { status, error: (e.error as string | null) ?? null } }
      const frames = (e.keyframes as { beat: string; url: string }[] | undefined) ?? []
      if (frames.length && (status === 'rendered' || status === 'cached')) {
        const kept = p.frames.filter((f) => !frames.some((n) => n.beat === f.beat))
        next.frames = [...kept, ...frames.map((f) => ({ ...f, scene, round: p.round }))].sort((a, b) =>
          a.beat.localeCompare(b.beat, undefined, { numeric: true }),
        )
      }
      if (status === 'failed') {
        next.feed = push(p.feed, { seq: e.seq, t: e.t, kind: 'scene', from: 'renderer', text: `Scene ${scene.toUpperCase()} failed - ${s('error')}`, tone: 'error' })
      }
      break
    }
    case 'artifact':
      if (e.name === 'contact') next.sheets = ((e.data as { sheets?: string[] })?.sheets ?? []) as string[]
      if (e.name === 'document') next.document = e.data as DocumentDigest
      if (e.name === 'math_checks') next.mathChecks = e.data as Production['mathChecks']
      break
    case 'review': {
      const record = e as unknown as ReviewRecord
      next.reviews = [...p.reviews.filter((r) => r.round !== record.round), record]
      next.feed = push(p.feed, {
        seq: e.seq,
        t: e.t,
        kind: 'review',
        from: 'review_panel',
        text: `Round ${record.round}: ${record.mean.toFixed(1)} / 10 - ${record.verdict === 'approve' ? 'approved' : 'revise'}`,
        tone: record.verdict === 'approve' ? 'good' : undefined,
      })
      break
    }
    case 'done':
      next.status = 'done'
      next.manifest = e.manifest as Manifest
      next.headline = (e.metrics as Production['headline']) ?? undefined
      next.agents = Object.fromEntries(Object.keys(p.agents).map((k) => [k, 'done' as const]))
      break
    case 'failed':
      next.status = 'failed'
      next.error = s('message')
      break
  }
  return next
}

export function useLesson(id: string | null) {
  const [production, setProduction] = useState<Production>(EMPTY)
  const [detail, setDetail] = useState<LessonDetail | null>(null)
  const openedAt = useRef(0)

  useEffect(() => {
    setProduction(EMPTY)
    setDetail(null)
    if (!id) return
    openedAt.current = Date.now()
    let closed = false
    api.lesson(id).then((d) => !closed && setDetail(d)).catch(() => undefined)

    const source = api.events(id)
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as StudioEvent
      // Events replayed on connect arrive in a burst; only animate live ones.
      const live = Date.now() - openedAt.current > 900
      setProduction((p) => reduce(p, event, live))
      if (event.type === 'done' || event.type === 'failed') {
        source.close()
        api.lesson(id).then((d) => !closed && setDetail(d)).catch(() => undefined)
      }
    }
    source.onerror = () => {
      // The server ends the stream once a lesson is finished; stop reconnecting then.
      setProduction((p) => {
        if (p.status === 'done' || p.status === 'failed') source.close()
        return p
      })
    }
    return () => {
      closed = true
      source.close()
    }
  }, [id])

  return { production, detail }
}

/** Seconds since the lesson started, ticking while it runs. */
export function useElapsed(production: Production): number {
  const [, tick] = useState(0)
  const running = production.status === 'running' || production.status === 'queued'
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  if (!running) return production.lastT
  return production.lastT + (Date.now() - production.lastAt) / 1000
}
