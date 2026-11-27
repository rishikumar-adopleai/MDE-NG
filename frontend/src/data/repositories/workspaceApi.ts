import type { AIStageReview, ClinicalWorkspaceView, DocumentReviewDecision, EsrInspectionResult, IntakeWorkspaceView, SchedulingWorkspaceView, StageKey, TriageWorkspaceView, WorkspaceState } from '../../domain/model';
export type { AIStageReview, EsrInspectionResult } from '../../domain/model';

export type WorkspacePayload = WorkspaceState & { intake?: IntakeWorkspaceView; triage?: TriageWorkspaceView; scheduling?: SchedulingWorkspaceView; clinical?: ClinicalWorkspaceView };

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

async function request<T>(path: string, options?: RequestInit, timeoutMs?: number): Promise<T> {
  const headers = new Headers(options?.headers);
  headers.set('Accept', 'application/json');
  const effectiveTimeout = timeoutMs ?? (options?.body instanceof FormData ? 120000 : 25000);
  const controller = new AbortController();
  const externalSignal = options?.signal;
  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener('abort', onExternalAbort, { once: true });
  }
  const timer = globalThis.setTimeout(() => controller.abort(), effectiveTimeout);
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, { credentials: 'same-origin', ...options, headers, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError' && !externalSignal?.aborted) {
      throw new WorkspaceApiError(408, 'The workspace request timed out. Please retry.');
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
    externalSignal?.removeEventListener('abort', onExternalAbort);
  }
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
  }, 120000);
}

export function requestIntakeDocumentReview(esrId: string, signal: AbortSignal, disposition?: {
  reviewId: string; decision: DocumentReviewDecision;
}) {
  const path = `/intake/esrs/${encodeURIComponent(esrId)}/document-review`;
  return request<{ state: WorkspacePayload }>(disposition ? `${path}/decision` : path, {
    signal, method: disposition ? 'PUT' : 'POST',
    headers: disposition && { 'Content-Type': 'application/json' },
    body: disposition && JSON.stringify(disposition),
  }, 120000);
}

export function triggerTriagePrepare(esrId: string, force = false) {
  return request<{ ok: boolean; esrId: string; state: WorkspacePayload }>(
    `/triage/esrs/${encodeURIComponent(esrId)}/prepare?force=${force ? 'true' : 'false'}`,
    { method: 'POST' },
    120000,
  );
}

export function matchSchedulingOptions(
  requirementIds: string[],
  facilityId?: string,
  examinerId?: string,
) {
  return request<import('../../domain/model').SchedulingMatchResponse>('/scheduling/match', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      requirementIds,
      facilityId: facilityId || null,
      examinerId: examinerId || null,
    }),
  });
}

export function matchCoordinatedScheduling(
  esrId: string,
  requirementIds?: string[],
) {
  return request<import('../../domain/model').CoordinatedMatchResponse>('/scheduling/match-coordinated', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      esrId,
      requirementIds: requirementIds && requirementIds.length ? requirementIds : null,
    }),
  });
}

export function uploadClinicalDBQ(dbqId: string, file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return request<{
    ok: boolean;
    dbqId: string;
    extractedValues: import('../../domain/model').FieldValues;
    validationAlerts: import('../../domain/model').ClinicalValidationAlert[];
    draftMedicalReview?: import('../../domain/model').DraftMedicalReview;
    state: WorkspacePayload;
  }>(`/clinical/${encodeURIComponent(dbqId)}/upload-dbq`, {
    method: 'POST',
    body: formData,
  });
}

export function uploadClinicalDiagnostic(orderId: string, file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return request<{ ok: boolean; orderId: string; state: WorkspacePayload }>(
    `/clinical/${encodeURIComponent(orderId)}/upload-diagnostic`,
    { method: 'POST', body: formData },
  );
}

export function validateAndDraftClinical(dbqId: string, values?: import('../../domain/model').FieldValues) {
  return request<{
    ok: boolean;
    dbqId: string;
    validationAlerts: import('../../domain/model').ClinicalValidationAlert[];
    draftMedicalReview?: import('../../domain/model').DraftMedicalReview;
    state: WorkspacePayload;
  }>(`/clinical/${encodeURIComponent(dbqId)}/validate-and-draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values: values ?? null }),
  });
}

export function triggerQAAudit(reviewId: string, force = false) {
  return request<{
    ok: boolean;
    reviewId: string;
    state: WorkspacePayload;
  }>(`/qa/reviews/${encodeURIComponent(reviewId)}/audit?force=${force ? 'true' : 'false'}`, {
    method: 'POST',
  }, 120000);
}
