// Shapes returned by the backend (spec §9, §12). Mirrors backend/portolan/models/*.
// TODO(M11): generate from the backend JSON Schema with `pnpm gen:types`.

export type Grounding = "in_code" | "in_library" | "shown_by_running" | "concept_only" | "unknown";
export type GenState = "skeleton" | "detailed" | "generating" | "failed";
export type LinkKind = "part_of" | "uses" | "causes" | "enables" | "contrasts" | "example_of" | "other";

export interface Concept {
  id: string;
  canonical_name: string;
  title: string;
  gist: string;
  grounding: Grounding;
  is_first_principle: boolean;
  depth: number;
  anchors: string[];
  has_layer: boolean;
  gen_state: GenState;
  issue_count_self: number;
}

export interface Link {
  id: string; // "<layer_id>:<source>-><target>"
  layer_id: string;
  source: string;
  target: string;
  label: string;
  kind: LinkKind;
}

export interface Layer {
  id: string; // concept id, or "master"
  paper_id: string;
  center: string;
  concepts: string[];
  links: Link[];
  gen_state: GenState;
}

export interface LayerPayload {
  layer: Layer;
  concepts: Record<string, Concept>;
}

export interface ConceptPayload {
  concept: Concept;
  also_appears_in: string[];
  issues_inside: number;
  details: { explanation: boolean; code: boolean; example: boolean; graph: boolean };
}

export interface RepoStatus {
  kind: "official_found" | "unofficial_only" | "placeholder_only" | "none";
  message: string;
}

export interface RepoCandidate {
  id: string;
  url: string;
  owner: string;
  name: string;
  official: boolean | null;
  official_evidence: string | null;
  status: "ok" | "placeholder" | "dead" | "unknown";
  stars: number | null;
}

export interface PaperInfo {
  paper_id: string;
  status: "ready" | "processing";
  title?: string;
  authors?: string[];
  arxiv_id?: string | null;
  version?: string | null;
  low_structure?: boolean;
  repo_status?: RepoStatus | null;
  repo?: RepoCandidate | null;
  chart_ready?: boolean;
  jobs?: Record<string, string>;
}

export interface TitleCandidate {
  arxiv_id: string;
  version: string | null;
  title: string;
  authors: string[];
  year: string;
}

export type CreatePaperResult =
  | { paper_id: string; status: "queued" | "ready" }
  | { status: "needs_choice"; candidates: TitleCandidate[] };

export type Angle = "default" | "simpler" | "mathematical" | "analogy" | "why" | "another_take";

export interface ExplanationSections {
  what_it_is: string;
  how_it_works: string;
  why_its_here: string;
  where_it_appears: string[]; // anchor ids
  common_confusion: string;
}

export interface Explanation {
  concept_id: string;
  angle: Angle;
  gist: string;
  sections: ExplanationSections;
  body_markdown: string;
  anchors_cited: string[];
  model: string;
}

export interface EdgeExplanation {
  link_id: string;
  headline: string;
  body_markdown: string;
  why_it_matters: string;
}

export interface Budget {
  day: string;
  requests_today: number;
  limits: { rpm: number; rpd: number; tpm: number };
  provider: string;
  models: { model: string; requests_today: number; cooling_down_seconds: number }[];
}

export interface Pending {
  status: "generating" | "pending" | "processing";
}

export type SseType =
  | "progress"
  | "layer_ready"
  | "concept_updated"
  | "detail_ready"
  | "route_ready"
  | "finding_added"
  | "faq_ready"
  | "rate_limited"
  | "error";

export interface ProgressEvent {
  stage: string;
  percent: number;
  message: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface CodeSpan {
  repo_id: string;
  commit_sha: string;
  path: string;
  start_line: number;
  end_line: number;
}

export type NoteKind = "implements" | "detail_not_in_paper" | "mismatch" | "context";

export interface LineNote {
  line: number;
  tag: string;
  note: string;
  kind: NoteKind;
}

export interface CodeGrounding {
  concept_id: string;
  grounding: Grounding;
  spans: CodeSpan[];
  notes: LineNote[];
  library_calls: string[];
  summary: string;
  /** {path: {line number: text}} for every line of every span. */
  code: Record<string, Record<string, string>>;
  /** path -> URL of the file at the commit */
  links: Record<string, string>;
}

export interface Example {
  concept_id: string;
  variant: number;
  source_label: "generic" | "paper_notation" | "from_repo_run";
  caption: string;
  program: string;
  columns: string[];
  rows: string[][];
  note: string | null;
}

export type GraphTemplate = "bar" | "line" | "heatmap" | "before_after" | "distribution";

export interface GraphSpec {
  concept_id: string;
  template: GraphTemplate;
  caption: string;
  /** Computed by the sandbox. Shape per template: see src/detail/graphs. */
  data: Record<string, unknown>;
  note: string | null;
}

export type GraphResult = GraphSpec | { template: "none" };

export type StopRole = "first_mention" | "definition" | "setting" | "result" | "argument" | "other";

export interface Stop {
  id: string;
  route_id: string;
  order: number;
  title: string;
  section_label: string; // "§3.2.1" or "§3.2.1 ¶2"
  anchor_id: string;
  role: StopRole;
  concept_id: string | null;
  summary: string;
  transition_to_next: string | null;
  sub_route_id: string | null; // set when jumping in is possible
  segment_title: string | null; // first stop of each top-level section
  first_stop_id: string | null; // later mentions: where the concept first appeared
  seen_again_at: string[]; // first mention: stop ids where it appears again
  side_roads: string[]; // appendix anchor ids this stop refers to
}

export interface Route {
  id: string; // "main" or a stop id
  paper_id: string;
  parent_stop_id: string | null;
  stops: Stop[];
}

export type Verdict = "matches" | "mismatch" | "undocumented_in_paper" | "uncertain" | "not_found";
export type Severity = "high" | "medium" | "low";

export interface Finding {
  id: string;
  claim_text: string;
  claim_type: string;
  paper_anchor: string;
  code_spans: CodeSpan[];
  verdict: Verdict;
  severity: Severity;
  confidence: number;
  method: "literal_match" | "static" | "llm_judge" | "executed_test";
  evidence: string;
  concept_ids: string[];
}

export interface FindingsPayload {
  findings: Finding[];
  state: "none" | "running" | "done" | "failed";
}

export type FaqSource =
  | "reviewer_discussion"
  | "github_issue"
  | "paper_page_discussion"
  | "finding"
  | "anticipated"
  | "user_asked";

export interface FAQItem {
  id: string;
  question: string;
  answer_markdown: string;
  source_kind: FaqSource;
  source_url: string | null;
  source_label: string;
  concept_ids: string[];
  anchors_cited: string[];
  supported: boolean;
}

export interface FaqPayload {
  items: FAQItem[];
}
