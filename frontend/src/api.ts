// The only module that talks to the backend. VITE_API_MODE=mock replays responses
// recorded from the real backend (scripts/capture_fixtures.py); anything else calls it.
import captured from "./mocks/captured.json";
import demoSignals from "./mocks/demoSignals.json";
import type {
  DemoScenario,
  ExecutionOutcome,
  Health,
  LocationInput,
  LocationResult,
  PairingResult,
  Plan,
  PlanPatch,
  SessionState,
  Signal,
  SignalResult,
} from "./types";

export const API_MODE: "mock" | "real" = import.meta.env.VITE_API_MODE === "mock" ? "mock" : "real";
const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }

  get isStale(): boolean {
    return this.status === 409 && this.code === "STALE_PLAN";
  }
}

export interface Api {
  health(): Promise<Health>;
  session(): Promise<SessionState>;
  reset(scenarioId: string): Promise<SessionState>;
  injectSignal(signal: Signal): Promise<SignalResult>;
  getPlan(planId: string): Promise<Plan>;
  revisePlan(planId: string, patch: PlanPatch): Promise<Plan>;
  approvePlan(planId: string, expectedVersion: number): Promise<ExecutionOutcome>;
  rejectPlan(planId: string, expectedVersion: number): Promise<Plan>;
  pairPhone(): Promise<PairingResult>;
  postLocation(token: string, sample: LocationInput): Promise<LocationResult>;
}

export const DEMOS = demoSignals as Record<DemoScenario, { scenario_id: string; signal: Signal }>;

// ---------- real backend ----------

async function request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const response = await fetch(BASE + path, {
    method,
    headers: body === undefined ? headers : { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const code = typeof detail === "object" && detail?.code ? detail.code : `HTTP_${response.status}`;
    const message = typeof detail === "object" ? (detail?.message ?? JSON.stringify(detail)) : String(detail ?? response.statusText);
    throw new ApiError(response.status, code, message);
  }
  return payload as T;
}

const realApi: Api = {
  health: () => request("GET", "/api/health"),
  session: () => request("GET", "/api/session"),
  reset: (scenarioId) => request("POST", "/api/demo/reset", { scenario_id: scenarioId }),
  injectSignal: (signal) => request("POST", "/api/signals", signal),
  getPlan: (planId) => request("GET", `/api/plans/${encodeURIComponent(planId)}`),
  revisePlan: (planId, patch) => request("PATCH", `/api/plans/${encodeURIComponent(planId)}`, patch),
  approvePlan: (planId, expectedVersion) =>
    request("POST", `/api/plans/${encodeURIComponent(planId)}/approve`, { expected_version: expectedVersion }),
  rejectPlan: (planId, expectedVersion) =>
    request("POST", `/api/plans/${encodeURIComponent(planId)}/reject`, { expected_version: expectedVersion }),
  pairPhone: () => request("POST", "/api/pairing"),
  postLocation: (token, sample) => request("POST", "/api/location", sample, { "X-Pairing-Token": token }),
};

// ---------- mock: replays captured backend responses ----------

type Capture = {
  reset: SessionState;
  injected: SessionState;
  revised: Plan;
  approved: ExecutionOutcome;
  after_approve: SessionState;
};
const recorded = captured as unknown as { health: Health } & Record<DemoScenario, Capture>;

function mockApi(): Api {
  let scenario: DemoScenario = "dinner";
  let state: SessionState = structuredClone(recorded.dinner.reset);
  const clone = <T>(value: T) => Promise.resolve(structuredClone(value));
  const stale = () => new ApiError(409, "STALE_PLAN", "plan version changed; refresh before continuing");
  const findPlan = (planId: string) => {
    const plan = state.plans.find((item) => item.id === planId);
    if (!plan) throw new ApiError(404, "NOT_FOUND", "plan not found");
    return plan;
  };
  const replacePlan = (plan: Plan) => {
    state.plans = state.plans.map((item) => (item.id === plan.id ? plan : item));
    state.actions = [...state.actions.filter((a) => !plan.actions.some((b) => b.id === a.id)), ...plan.actions];
  };
  const scenarioFor = (scenarioId: string): DemoScenario =>
    (Object.keys(DEMOS) as DemoScenario[]).find((key) => DEMOS[key].scenario_id === scenarioId) ?? "dinner";

  return {
    health: () => clone(recorded.health),
    session: () => clone(state),
    async reset(scenarioId) {
      scenario = scenarioFor(scenarioId);
      state = structuredClone(recorded[scenario].reset);
      return clone(state);
    },
    async injectSignal(signal) {
      const match = (Object.keys(DEMOS) as DemoScenario[]).find((key) => DEMOS[key].signal.id === signal.id);
      if (!match) throw new ApiError(400, "MOCK_UNKNOWN_SIGNAL", "mock mode only replays the demo signals");
      const duplicate = state.signals.some((item) => item.id === signal.id);
      if (!duplicate) {
        scenario = match;
        state = structuredClone(recorded[match].injected);
      }
      return clone({
        signal_id: signal.id,
        duplicate,
        impact_ids: state.impacts.map((i) => i.id),
        plan_ids: state.plans.map((p) => p.id),
      });
    },
    async getPlan(planId) {
      return clone(findPlan(planId));
    },
    async revisePlan(planId, patch) {
      const plan = findPlan(planId);
      if (plan.version !== patch.expected_version) throw stale();
      if (plan.status !== "proposed") throw new ApiError(409, "INVALID_STATE", "only a proposed plan can be revised");
      // Replay the recorded revision shape, carrying the edited text and bumping the version.
      const revised: Plan = structuredClone(recorded[scenario].revised);
      revised.version = plan.version + 1;
      revised.actions = revised.actions.map((action) => ({
        ...action,
        id: action.id.replace(/_v\d+$/, `_v${revised.version}`),
        message_text: action.type === "send_message" && patch.message_text ? patch.message_text : action.message_text,
      }));
      replacePlan(revised);
      return clone(revised);
    },
    async approvePlan(planId, expectedVersion) {
      const plan = findPlan(planId);
      if (plan.version !== expectedVersion) throw stale();
      const existing = state.results.filter((r) => plan.actions.some((a) => a.id === r.action_id));
      if (existing.length && existing.length === plan.actions.length) {
        return clone({ plan, results: existing, duplicate: true });
      }
      if (plan.status !== "proposed") throw new ApiError(409, "INVALID_STATE", "only a proposed plan can be approved");
      const template = recorded[scenario].approved;
      const actions = plan.actions.map((action) => ({ ...action, status: "simulated" as const }));
      const results = plan.actions.map((action) => ({
        ...template.results[0],
        action_id: action.id,
        provider_reference: `outbox:${action.id}`,
        completed_at: new Date().toISOString(),
      }));
      const completed: Plan = { ...plan, status: "completed", actions };
      replacePlan(completed);
      state.results = [...state.results, ...results];
      state.approvals = [
        ...state.approvals,
        {
          id: `approval_${plan.id}_v${plan.version}`,
          plan_id: plan.id,
          plan_version: plan.version,
          user_id: "demo_user",
          approved_at: new Date().toISOString(),
          approval_channel: "button",
        },
      ];
      return clone({ plan: completed, results, duplicate: false });
    },
    async rejectPlan(planId, expectedVersion) {
      const plan = findPlan(planId);
      if (plan.version !== expectedVersion) throw stale();
      const rejected: Plan = { ...plan, status: "rejected" };
      replacePlan(rejected);
      return clone(rejected);
    },
    async pairPhone() {
      return { token: "mock-pairing-token", expires_at: new Date(Date.now() + 3600_000).toISOString() };
    },
    async postLocation(token, sample) {
      if (token !== "mock-pairing-token") throw new ApiError(401, "AUTH_REQUIRED", "valid pairing token required");
      const age = (Date.now() - Date.parse(sample.captured_at)) / 1000;
      if (age > 120) return { status: "stale", signal: null, error_code: "STALE_LOCATION" };
      return { status: sample.accuracy_meters > 200 ? "uncertain" : "accepted", signal: null, error_code: null };
    },
  };
}

export const api: Api = API_MODE === "mock" ? mockApi() : realApi;
