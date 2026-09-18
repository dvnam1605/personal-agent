/**
 * Canonical frontend types strictly reflecting FastAPI backend Pydantic models.
 */

export interface QueryRouteInfo {
  route_type: string;
  target_agent?: string | null;
  target_workflow_id?: string | null;
  reason_code?: string | null;
  domains: string[];
  confidence: number;
}

export interface QueryResult {
  run_id: string;
  status:
    | 'completed'
    | 'needs_approval'
    | 'clarification_needed'
    | 'blocked'
    | 'casual_response'
    | 'rejected'
    | 'routed'
    | 'failed'
    | 'processing'
    | (string & {});
  message: string;
  route: QueryRouteInfo;
  data: Record<string, any>;
  approval_id?: string | null;
}

export interface QueryRequest {
  query: string;
}

/* Approval Plane */
export type RiskLevel = 'SAFE' | 'SENSITIVE' | 'DESTRUCTIVE' | string;

export interface ProposedAction {
  action_type: string;
  description: string;
  tool_name?: string | null;
  parameters: Record<string, any>;
  risk_level: RiskLevel;
  requires_approval?: boolean;
}

export interface ApprovalRequestResponse {
  id: string;
  run_id: string;
  action_type: string;
  description: string;
  target?: string | null;
  important_arguments: Record<string, any>;
  tool_name?: string | null;
  risk_level: RiskLevel;
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  expires_at?: string | null;
  created_at: string;
  approved?: boolean | null;
  approver_id?: string | null;
  reason?: string | null;
}

export interface ApproveRequestBody {
  reason?: string | null;
  execute?: boolean;
}

export interface ApproveResponse {
  approval_id: string;
  status: string;
  outcome: string;
  approved: boolean;
  token?: string | null;
  executed: boolean;
  execution?: Record<string, any> | null;
  error?: string | null;
}

export interface DenyRequestBody {
  reason?: string | null;
  outcome?: 'rejected' | 'cancelled' | 'unavailable';
}

export interface DenyResponse {
  approval_id: string;
  status: string;
  outcome: string;
  approved: boolean;
  reason?: string | null;
}

export interface ExecuteApprovalBody {
  token: string;
}

/* Question Plane (Clarification) */
export interface UserQuestionOption {
  label: string;
  description?: string | null;
}

export interface UserQuestionItem {
  id: string;
  question: string;
  detail?: string | null;
  options?: UserQuestionOption[] | null;
  multi_select: boolean;
  intent?: 'plan-review' | null;
}

export interface UserQuestionAnswer {
  question_id: string;
  selected_options: string[];
  free_text?: string | null;
}

export interface AnswerQuestionRequestBody {
  answers: UserQuestionAnswer[];
  expected_version: number;
}

export interface AnswerQuestionResponse {
  question_id: string;
  status: string;
  answered_by: string;
  answers: Record<string, any>[];
  version: number;
}

/* Google Workspace Integration */
export interface GoogleIntegrationStatus {
  connected: boolean;
  healthy: boolean;
  email?: string | null;
  scopes: string[];
  missing_scopes: string[];
  access_token_expires_at?: string | null;
  revoked_at?: string | null;
}

export interface GoogleDisconnectResponse {
  disconnected: boolean;
}

/* Health & Diagnostics */
export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  environment: string;
}

export interface ReadinessResponse {
  status: string;
  checks: {
    database: { status: string };
    redis: { status: string };
    configuration: { status: string };
  };
}

/* Domain Data Payloads from QueryResult.data */
export interface CalendarEventDateTime {
  dateTime?: string;
  date?: string;
}

export interface CalendarEvent {
  id: string;
  summary: string;
  when: string;
  start?: CalendarEventDateTime;
  end?: CalendarEventDateTime;
  location?: string | null;
  html_link?: string | null;
}

export interface CalendarDataPayload {
  window?: { start: string; end: string };
  events?: CalendarEvent[];
  count?: number;
  proposal?: ProposedAction;
}

export interface GmailMessage {
  id: string;
  thread_id?: string;
  subject: string;
  from: string;
  from_email?: string | null;
  when: string;
  snippet: string;
  unread: boolean;
}

export interface GmailDataPayload {
  query?: string;
  messages?: GmailMessage[];
  count?: number;
}

export interface RAGCitation {
  document_id: string;
  title: string;
  source_type: string;
  source_uri?: string;
  chunk_id: string;
  parent_id?: string;
  section_title?: string;
  page_number?: number;
  score?: number;
  text: string;
}

export interface RAGDataPayload {
  citations?: RAGCitation[];
  sufficiency?: 'sufficient' | 'insufficient' | 'ambiguous' | string;
}

export interface MeetingPrepDataPayload {
  meeting?: {
    id: string;
    summary: string;
    when: string;
    attendees: string[];
  };
  discussions?: string[];
  documents?: RAGCitation[];
}

export interface AppErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: Record<string, any>;
  };
}
