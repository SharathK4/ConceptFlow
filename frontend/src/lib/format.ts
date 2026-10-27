export function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function index(n: number): string {
  return String(n).padStart(2, '0')
}

export function ago(epochSeconds: number): string {
  const diff = Date.now() / 1000 - epochSeconds
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** Agent ids from the backend, as people should read them. */
export const AGENT_LABEL: Record<string, string> = {
  intake: 'Intake',
  intake_reader: 'Intake Reader',
  concept_analyst: 'Concept Analyst',
  lesson_planner: 'Lesson Planner',
  storyboard_director: 'Storyboard Director',
  narrator: 'Narrator',
  manim_coder: 'Manim Coder',
  renderer: 'Renderer',
  visual_critic: 'Visual Critic',
  pedagogy_critic: 'Maths & Pedagogy Critic',
  review_panel: 'Review Panel',
  refiner: 'Refiner',
  editor: 'Editor',
  formula_checker: 'Formula Check',
  writers_room: 'Writers\u2019 Room \u00b7 A2A',
  mcp: 'MCP tool',
  guardrails: 'Guardrails',
  viewer: 'You',
}

export function agentLabel(id: string): string {
  return AGENT_LABEL[id] ?? id.replace(/_/g, ' ')
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '\u2013'
  if (value >= 90) return clock(value)
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} s`
}

export function usd(value: number | null | undefined): string {
  if (!value) return '$0'
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`
}

export function count(value: number | null | undefined): string {
  return (value ?? 0).toLocaleString()
}
