// Mirrors backend/headsup/models.py. Keep field names identical; the backend is the source of truth.

export type ISODateTime = string;
export type EventMode = "in_person" | "remote" | "unknown";
export type ProviderMode = "mock" | "live";

export interface Contact {
  id: string;
  display_name: string;
  relationship: string;
  email: string | null;
  beeper_chat_id: string | null;
  preferred_channel: string;
  tone: string;
  auto_notify_allowed: boolean;
}

export interface Event {
  id: string;
  provider: string;
  provider_event_id: string | null;
  title: string;
  start_at: ISODateTime;
  end_at: ISODateTime;
  timezone: string;
  mode: EventMode;
  destination: string | null;
  contact_ids: string[];
  organizer_self: boolean;
  writable: boolean;
  provider_version: string | null;
}

export interface Position {
  latitude: number;
  longitude: number;
  accuracy_meters: number | null;
}

export interface Signal {
  id: string;
  kind: string;
  source: string;
  source_mode: ProviderMode;
  observed_at: ISODateTime;
  flight_instance_id?: string | null;
  estimated_arrival_at?: ISODateTime | null;
  position?: Position | null;
  route_duration_seconds?: number | null;
}

export interface Impact {
  id: string;
  event_id: string;
  signal_ids: string[];
  earliest_arrival_at: ISODateTime;
  late_by_seconds: number;
  reason: string;
  confidence: string;
  evidence_times: Record<string, ISODateTime>;
  status: string;
}

export type ActionStatus = "pending" | "executing" | "succeeded" | "simulated" | "failed" | "unknown" | "skipped";

export interface Action {
  id: string;
  type: string;
  contact_id: string | null;
  event_id: string | null;
  message_text: string | null;
  new_start_at: ISODateTime | null;
  new_end_at: ISODateTime | null;
  depends_on_action_ids: string[];
  status: ActionStatus;
}

export type PlanStatus =
  | "proposed"
  | "approved"
  | "executing"
  | "completed"
  | "partially_failed"
  | "failed"
  | "rejected"
  | "expired";

export interface Plan {
  id: string;
  version: number;
  impact_id: string;
  status: PlanStatus;
  actions: Action[];
  created_at: ISODateTime;
  expires_at: ISODateTime;
  evidence_snapshot: Record<string, unknown>;
}

export type ResultStatus = "succeeded" | "simulated" | "failed" | "unknown" | "skipped";

export interface ActionResult {
  action_id: string;
  provider: string;
  provider_mode: ProviderMode;
  status: ResultStatus;
  provider_reference: string | null;
  error_code: string | null;
  completed_at: ISODateTime | null;
}

export interface Approval {
  id: string;
  plan_id: string;
  plan_version: number;
  user_id: string;
  approved_at: ISODateTime;
  approval_channel: string;
}

export interface SessionState {
  id: string;
  scenario_id: string;
  contacts: Contact[];
  events: Event[];
  signals: Signal[];
  impacts: Impact[];
  plans: Plan[];
  actions: Action[];
  results: ActionResult[];
  approvals: Approval[];
}

export interface SignalResult {
  signal_id: string;
  duplicate: boolean;
  impact_ids: string[];
  plan_ids: string[];
}

export interface PlanPatch {
  expected_version: number;
  message_text?: string;
  new_start_at?: ISODateTime;
  new_end_at?: ISODateTime;
}

export interface ExecutionOutcome {
  plan: Plan;
  results: ActionResult[];
  duplicate: boolean;
}

export interface Health {
  status: "ok" | "degraded";
  providers: Record<string, { mode?: string; status?: string; [key: string]: unknown }>;
}

export interface LocationInput {
  latitude: number;
  longitude: number;
  accuracy_meters: number;
  captured_at: ISODateTime;
  route_duration_seconds?: number | null;
}

export interface LocationResult {
  status: "accepted" | "uncertain" | "stale";
  signal: SignalResult | null;
  error_code: string | null;
}

export interface PairingResult {
  token: string;
  expires_at: ISODateTime;
}

export type DemoScenario = "dinner" | "flight";
