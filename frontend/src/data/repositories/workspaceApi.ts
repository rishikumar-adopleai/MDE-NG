import type { AIStageReview, DocumentReviewDecision, EsrInspectionResult, IntakeWorkspaceView, StageKey, TriageWorkspaceView, WorkspaceState } from '../../domain/model';
export type { AIStageReview, EsrInspectionResult } from '../../domain/model';

export type WorkspacePayload = WorkspaceState & { intake?: IntakeWorkspaceView; triage?: TriageWorkspaceView };

export type WorkspaceRuntime = {
  mode: 'dev' | 'prod';
  authRequired: boolean;
  simulationEnabled: boolean;
  aiEnabled: boolean;
};


export class WorkspaceApiError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}

const apiBase = import.meta.env.BASE_URL ? import.meta.env.BASE_URL.replace(/\/$/, '') + '/api' : '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  headers.set('Accept', 'application/json');
  const response = await fetch(`${apiBase}${path}`, { credentials: 'same-origin', ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    throw new WorkspaceApiError(response.status, typeof body?.detail === 'string' ? body.detail : `Workspace request failed (${response.status}).`);
  }
  try {
    return await response.json();
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new WorkspaceApiError(response.status, 'The workspace API returned an unexpected response. Check the API route and retry.');
  }
}

export const loadRuntime = () => request<WorkspaceRuntime>('/runtime');
export const loadWorkspace = () => request<WorkspacePayload>('/workspace');
export const authenticateAccount = (mode: 'login' | 'signup', email: string, password: string) =>
  request<{ ok: boolean }>(`/auth/${mode}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }),
  });
export const logout = () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' });

export function sendWorkspaceCommand(action: string, args: unknown[], revision: number) {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  const key = [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  return request<{ state: WorkspacePayload; value?: unknown }>('/workspace/commands', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify({ action, args, revision }),
  });
}

export function intakeRequest(path: string, body: FormData | undefined) {
  return request<{ esrId: string; state: WorkspacePayload }>(`/intake/esrs${path}`, {
    method: 'POST', body,
  });
}

export const listIntakeScenarios = () =>
  request<{ items: { id: string; title: string }[] }>('/intake/exam-management/examples');

export const receiveIntakeXml = (body: FormData) =>
  request<{ esrId: string; state: WorkspacePayload }>('/intake/exam-management/esrs', { method: 'POST', body });

export const receiveIntakeSample = (scenario: string) =>
  request<{ esrId: string; state: WorkspacePayload }>(`/intake/exam-management/examples/${encodeURIComponent(scenario)}`, {
    method: 'POST',
  });

export const inspectIntakeEsr = (body: FormData) =>
  request<EsrInspectionResult>('/intake/esrs/inspect', { method: 'POST', body });

export interface ExchangeEvent {
  id: string; title: string; scenario: string; claimant: string; at: string;
  appointmentAt: string; source: string; facts: Record<string, string>;
}

export interface ReferenceForm {
  id: string; title: string; bodySystem: string; revision: number; documentId: number;
  source: string; sections: string[]; fieldCount: number; ruleCount: number;
}

export const loadSourceRecords = (stage: StageKey, signal: AbortSignal) =>
  request<{ events: ExchangeEvent[]; forms: ReferenceForm[] }>(`/workspace/source-records?${new URLSearchParams({stage})}`, { signal });

export function loadWorkItem<T>(id: string, signal?: AbortSignal) {
  return request<{ stage: string; detail: T }>(`/workspace/work-items/${encodeURIComponent(id)}`, {
    signal,
  }).then(response => response.detail);
}

export function generateStageReview(stage: StageKey, entityId: string, signal?: AbortSignal) {
  return request<AIStageReview>('/workspace/ai-review', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage, entityId }),
  });
}

export function requestIntakeDocumentReview(esrId: string, signal: AbortSignal, disposition?: {
  reviewId: string; decision: DocumentReviewDecision;
}) {
  const path = `/intake/esrs/${encodeURIComponent(esrId)}/document-review`;
  return request<{ state: WorkspacePayload }>(disposition ? `${path}/decision` : path, {
    signal, method: disposition ? 'PUT' : 'POST',
    headers: disposition && { 'Content-Type': 'application/json' },
    body: disposition && JSON.stringify(disposition),
  });
}

export function triggerTriagePrepare(esrId: string, force = false) {
  return request<{ ok: boolean; esrId: string; state: WorkspacePayload }>(
    `/triage/esrs/${encodeURIComponent(esrId)}/prepare?force=${force ? 'true' : 'false'}`,
    { method: 'POST' },
  );
}
