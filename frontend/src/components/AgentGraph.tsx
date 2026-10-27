/**
 * The studio as a diagram: every agent a node, every hand-off an edge. When
 * one agent passes work to another, a small light travels along the edge
 * between them - the conversation made visible.
 *
 * The CrewAI writers' room is drawn as a group inside the larger ADK
 * workflow; the ADK side reaches it over the A2A protocol, and the marker
 * under the group follows that exchange. Along the bottom, the studio's MCP
 * tools light up as agents call them.
 */

import { useCallback, useEffect, useState } from 'react'
import { agentLabel } from '../lib/format'
import { modelFor, type Packet, type Production } from '../lib/useLesson'

type Pos = { x: number; y: number }

const NODES: Record<string, Pos & { fw: 'adk' | 'crewai' }> = {
  intake: { x: 48, y: 214, fw: 'adk' },
  intake_reader: { x: 48, y: 96, fw: 'adk' },
  concept_analyst: { x: 184, y: 116, fw: 'crewai' },
  lesson_planner: { x: 184, y: 214, fw: 'crewai' },
  storyboard_director: { x: 184, y: 312, fw: 'crewai' },
  narrator: { x: 330, y: 150, fw: 'adk' },
  manim_coder: { x: 330, y: 278, fw: 'adk' },
  renderer: { x: 458, y: 214, fw: 'adk' },
  visual_critic: { x: 594, y: 132, fw: 'adk' },
  formula_checker: { x: 546, y: 286, fw: 'adk' },
  pedagogy_critic: { x: 642, y: 286, fw: 'adk' },
  refiner: { x: 458, y: 392, fw: 'adk' },
  editor: { x: 712, y: 214, fw: 'adk' },
}

// Virtual endpoints for messages addressed to groups or to the viewer.
const POINTS: Record<string, Pos> = {
  review_panel: { x: 594, y: 214 },
  viewer: { x: 790, y: 214 },
}

const EDGES: [string, string][] = [
  ['intake', 'intake_reader'],
  ['intake', 'concept_analyst'],
  ['intake_reader', 'concept_analyst'],
  ['concept_analyst', 'lesson_planner'],
  ['lesson_planner', 'storyboard_director'],
  ['storyboard_director', 'narrator'],
  ['storyboard_director', 'manim_coder'],
  ['narrator', 'renderer'],
  ['manim_coder', 'renderer'],
  ['renderer', 'visual_critic'],
  ['renderer', 'formula_checker'],
  ['formula_checker', 'pedagogy_critic'],
  ['renderer', 'refiner'],
  ['visual_critic', 'editor'],
  ['pedagogy_critic', 'editor'],
  ['visual_critic', 'refiner'],
  ['pedagogy_critic', 'refiner'],
  ['refiner', 'renderer'],
]

const TOOLS = ['read_document', 'lookup_concept', 'check_math', 'search_lessons']

function where(id: string): Pos | null {
  return NODES[id] ?? POINTS[id] ?? null
}

function curve(a: Pos, b: Pos, bend = 0.12): string {
  const mx = (a.x + b.x) / 2
  const my = (a.y + b.y) / 2
  const dx = b.x - a.x
  const dy = b.y - a.y
  const cx = mx - dy * bend
  const cy = my + dx * bend
  return `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`
}

function edgePath(from: string, to: string): string | null {
  const a = where(from)
  const b = where(to)
  if (!a || !b) return null
  // The correction loop bends the other way so it reads as a return path.
  const back = from === 'refiner' && to === 'renderer'
  return curve(a, b, back ? -0.35 : 0.1)
}

function Pulse({ packet, onDone }: { packet: Packet; onDone: (id: number) => void }) {
  useEffect(() => {
    const timer = window.setTimeout(() => onDone(packet.id), 1300)
    return () => window.clearTimeout(timer)
  }, [packet.id, onDone])
  const targets = packet.to === 'review_panel' ? ['visual_critic', 'formula_checker'] : [packet.to]
  const from = packet.from === 'review_panel' ? 'visual_critic' : packet.from
  return (
    <>
      {targets.map((to) => {
        const path = edgePath(from, to)
        if (!path) return null
        return (
          <g key={to} className="graph__pulse">
            <path d={path} className="graph__trace" />
            <circle r="4.2" className="graph__packet">
              <animateMotion dur="1.1s" path={path} fill="freeze" calcMode="spline" keyTimes="0;1" keySplines="0.4 0 0.2 1" />
            </circle>
          </g>
        )
      })}
    </>
  )
}

export function AgentGraph({ production, usesReader }: { production: Production; usesReader: boolean }) {
  const [done, setDone] = useState<Set<number>>(new Set())
  // Stable, so a pulse's timer is not restarted by every incoming event.
  const markDone = useCallback((id: number) => setDone((s) => new Set(s).add(id)), [])
  const live = production.packets.filter((p) => !done.has(p.id))

  return (
    <svg className="graph" viewBox="0 0 790 478" role="img" aria-label="The agents and their hand-offs">
      <text x="12" y="22" className="graph__caption">
        GOOGLE ADK WORKFLOW
      </text>

      <g className="graph__group">
        <rect x="116" y="72" width="136" height="286" rx="16" />
        <text x="184" y="62" textAnchor="middle" className="graph__caption graph__caption--crew">
          WRITERS&apos; ROOM · CREWAI
        </text>
        <g className={`graph__a2a is-${production.a2a ?? 'idle'}`} transform="translate(184 384)">
          <rect x="-58" y="-11" width="116" height="22" rx="11" />
          <text y="3.5" textAnchor="middle">
            {production.a2a === 'sent' ? 'A2A · STREAMING' : production.a2a === 'completed' ? 'A2A · DESIGN RECEIVED' : 'VIA A2A PROTOCOL'}
          </text>
        </g>
      </g>
      <g className="graph__group graph__group--panel">
        <rect x="500" y="90" width="186" height="252" rx="16" />
        <text x="593" y="358" textAnchor="middle" className="graph__caption">
          REVIEW PANEL
        </text>
      </g>

      {EDGES.map(([a, b]) => {
        const path = edgePath(a, b)
        if (!path) return null
        const dim = (a === 'intake_reader' || b === 'intake_reader') && !usesReader
        const loop = b === 'refiner' || a === 'refiner'
        return <path key={`${a}-${b}`} d={path} className={`graph__edge${dim ? ' is-dim' : ''}${loop ? ' is-loop' : ''}`} />
      })}

      {live.map((packet) => (
        <Pulse key={packet.id} packet={packet} onDone={markDone} />
      ))}

      <g className="graph__tools" transform="translate(0 452)">
        <text x="12" y="4" className="graph__caption">
          MCP TOOLS
        </text>
        {TOOLS.map((name, i) => {
          const used = production.tools[name] ?? 0
          const pulse = production.toolPulse?.name === name ? production.toolPulse.id : undefined
          return (
            <g key={`${name}-${pulse ?? 0}`} className={`graph__tool${used ? ' is-used' : ''}${pulse !== undefined ? ' is-pulse' : ''}`} transform={`translate(${104 + i * 132} 0)`}>
              <rect x="0" y="-11" width="122" height="22" rx="11" />
              <text x="61" y="3.5" textAnchor="middle">
                {name}
                {used ? ` · ${used}` : ''}
              </text>
            </g>
          )
        })}
      </g>

      {Object.entries(NODES).map(([id, node]) => {
        const state = production.agents[id]
        const dim = id === 'intake_reader' && !usesReader
        const model = state === 'active' ? modelFor(production, id) : undefined
        const labelBelow = !['intake_reader', 'refiner'].includes(id)
        return (
          <g key={id} className={`graph__node is-${state ?? 'idle'}${dim ? ' is-dim' : ''}`} transform={`translate(${node.x} ${node.y})`}>
            {state === 'active' ? <circle r="15" className="graph__halo" /> : null}
            <circle r="7" className="graph__dot" />
            <text y={labelBelow ? 27 : -18} textAnchor="middle" className="graph__label">
              {agentLabel(id).replace('Maths & Pedagogy Critic', 'Maths Critic')}
            </text>
            <text y={labelBelow ? 40 : -31} textAnchor="middle" className={`graph__fw graph__fw--${node.fw}`}>
              {model ? model.replace(/^replay:/, 'replay · ') : node.fw === 'crewai' ? 'CREWAI' : 'ADK'}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
