/** Shapes shared with the Python API (backend/conceptflow/server.py). */

export type Framework = 'adk' | 'crewai'

export interface NodeInfo {
  id: string
  label: string
  framework: Framework
  role: string
}

export interface Showcase {
  slug: string
  title: string
  prompt: string
  keywords: string[]
}

export interface ToolInfo {
  name: string
  title?: string | null
  description: string
}

export interface Health {
  providers: { gemini: boolean; elevenlabs: boolean; sarvam: boolean; latex: boolean; replay: boolean; langfuse?: boolean }
  mode: 'live' | 'replay'
  quality: string
  max_revisions: number
  languages: { code: string; name: string }[]
  showcases: Showcase[]
  usage: { date: string; calls: Record<string, number>; parked: Record<string, string>; missing: string[] }
  ladders: Record<string, string[]>
  nodes: NodeInfo[]
  tools?: ToolInfo[]
  mcp?: string
  a2a?: { agent: string; card: string; skills: string[] }
  observability?: { exporters: string[]; langfuse: boolean; langfuse_host: string | null }
}

export type LessonStatus = 'queued' | 'running' | 'done' | 'failed'

export interface LessonSummary {
  id: string
  status: LessonStatus
  title: string
  prompt: string
  created: number
  mode: 'live' | 'replay'
  showcase: string | null
  language: string
  poster: string | null
  duration: number | null
  error: string | null
  trace_id?: string | null
}

export interface Chapter {
  scene: string
  title: string
  start: number
  end: number
  thumb?: string
}

export interface QuizItem {
  question: string
  choices: string[]
  answer: number
  why: string
}

export interface Manifest {
  video: string
  duration: number
  captions: string
  captions_json: string
  chapters: Chapter[]
  poster: string | null
  title: string
  logline: string
  quality: string
  language: string
  voice: string | null
  quiz: QuizItem[]
  fallback_scenes: string[]
  rounds: number
}

export interface StoryBeat {
  id: string
  narration: string
  visual: string
}

export interface StoryScene {
  id: string
  title: string
  purpose: string
  beats: StoryBeat[]
}

export interface Storyboard {
  title: string
  logline: string
  color_story: string
  scenes: StoryScene[]
}

export interface ReviewIssue {
  scene: string
  beat: string
  severity: 'high' | 'medium' | 'low'
  problem: string
  fix: string
  source: 'visual' | 'pedagogy'
}

export interface ReviewRecord {
  round: number
  scores: Record<string, number>
  mean: number
  issues: ReviewIssue[]
  visual_summary: string
  pedagogy_summary: string
  verdict: 'approve' | 'revise'
  forced: boolean
}

export interface LessonDetail extends LessonSummary {
  request: Record<string, string>
  manifest: Manifest | null
  artifacts: {
    analysis?: Record<string, unknown> | null
    plan?: {
      title: string
      hook: string
      objectives: string[]
      sections: { title: string; goal: string; explanation: string; key_formula: string; visual_idea: string }[]
      takeaway: string
      quiz: QuizItem[]
    } | null
    storyboard?: Storyboard | null
    reviews?: ReviewRecord[] | null
    narration?: { provider: string; voice: string; language: string; lines: Record<string, string> }
  }
  code: string | null
  has_report?: boolean
}

export interface CaptionCue {
  start: number
  end: number
  text: string
  words: [string, number, number][]
}

export interface StudioEvent {
  type: string
  seq: number
  t: number
  [key: string]: unknown
}

export interface LessonOptions {
  audience: 'school' | 'undergraduate' | 'graduate'
  length: 'brief' | 'standard' | 'deep'
  language: string
  voice: 'auto' | 'elevenlabs' | 'sarvam' | 'edge' | 'silent'
  quality: 'draft' | 'hd' | 'hd60' | 'preview'
}

export interface DocumentPreview {
  name: string
  kind: string
  pages: number
  title: string
  abstract: string
  sections: string[]
  key_terms: string[]
  warnings: string[]
  chars: number
  guardrail: { action: 'allow' | 'sanitize' | 'block'; categories: string[]; reasons: string[] }
  needs_key: boolean
  message: string | null
}

export interface UsageRow {
  calls: number
  tokens_in: number
  tokens_out: number
  seconds: number
  usd: number
}

export interface StageMetric {
  agent: string
  framework: string | null
  seconds: number
  runs: number
  first_start: number
}

export interface Metrics {
  status: string
  mode: string | null
  wall_seconds: number
  video_seconds: number | null
  realtime_factor: number | null
  stages: StageMetric[]
  llm: {
    calls: number
    failed_calls: number
    tokens_in: number
    tokens_out: number
    seconds: number
    estimated: boolean
    paid_tier_usd: number
    by_role: Record<string, UsageRow>
    by_model: Record<string, UsageRow>
    by_framework: Record<string, UsageRow>
  }
  tools: { calls: number; by_tool: Record<string, { calls: number; errors: number; seconds: number; callers: string[] }> }
  a2a: { exchanges: number; seconds: number | null; stream_updates: number | null }
  render: { scene_renders: number; failed: number; render_seconds: number; rounds: number }
  voice: { provider: string; language: string; lines: number; characters: number; speech_seconds: number | null; usd: number }
  reviews: { rounds: number; final_mean: number | null; verdicts: string[] }
  guardrails: { checks: number; interventions: number; by_stage: Record<string, { checks: number; interventions: number }> }
  trace: { spans: number; by_scope: Record<string, number>; trace_id: string | null }
  cost: {
    free_tier_usd: number
    paid_tier_llm_usd: number
    voice_usd: number
    hosting_usd: number
    paid_tier_total_usd: number
    prices_as_of: string
    assumptions: string[]
  }
}

export interface Span {
  name: string
  trace_id: string
  span_id: string
  parent_id: string | null
  start: number | null
  end: number | null
  status: string
  scope: string
  attributes: Record<string, unknown>
}

export interface TraceData {
  trace_id: string | null
  exporters: string[]
  spans: Span[]
}

export interface ProvenanceRecord {
  at: number
  artifact: string
  sha: string
  producer: string
  framework: string
  model: string | null
  inputs: Record<string, string>
  trace_id: string
  span_id: string
  note: string
}

export interface MathCheckRow {
  latex: string
  status: 'holds' | 'differs' | 'parsed' | 'unchecked'
  detail: string
}

export interface DocumentDigest {
  name: string
  kind: string
  pages: number
  title: string
  abstract: string
  sections: string[]
  key_terms: string[]
  warnings: string[]
  flagged: boolean
}
