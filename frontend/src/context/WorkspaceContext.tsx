import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { AppointmentLogistics, Communication, DiagnosticReportedFlag, DocumentReviewDecision, EntityId, FieldValues, Modality, SimulatedDeliveryResponse, StageKey, TriagePlan } from '../domain/model';
import { generateStageReview, intakeRequest, loadRuntime, loadWorkItem, loadWorkspace, receiveIntakeSample, receiveIntakeXml, requestIntakeDocumentReview, sendWorkspaceCommand, triggerTriagePrepare, WorkspaceApiError, type AIStageReview, type WorkspacePayload, type WorkspaceRuntime } from '../data/repositories/workspaceApi';

export type CommandResult = { ok: true; message: string; value?: unknown } | { ok: false; message: string };

type WorkspaceContextValue = {
  state: WorkspacePayload | undefined;
  loading: boolean;
  storageError: string;
  runtime: WorkspaceRuntime | undefined;
  receiveIntakeExample: (scenario: string) => Promise<string>;
  importIntakeDocument: (body: FormData, esrId?: string, examManagementRequest?: boolean) => Promise<string>;
  retryIntakeDocument: (esrId: string, documentId: string) => Promise<CommandResult>;
  reviewIntakeException: (id: EntityId, disposition: 'resolved' | 'escalated' | 'excluded', resolution: string) => Promise<CommandResult>;
  recordIntakeReceipt: (id: EntityId, receiptReference?: string) => Promise<CommandResult>;
  completeDocumentProcessing: (esrId: EntityId) => Promise<CommandResult>;
  updateDocumentCategory: (documentId: EntityId, category: string) => Promise<CommandResult>;
  validateIntake: (esrId: EntityId) => Promise<CommandResult>;
  saveTriageDraft: (id: EntityId, plan: TriagePlan) => Promise<CommandResult>;
  approveTriagePlan: (id: EntityId, plan: TriagePlan) => Promise<CommandResult>;
  requestTriageClarification: (id: EntityId, note: string) => Promise<CommandResult>;
  resumeTriageReview: (id: EntityId, note: string) => Promise<CommandResult>;
  authorizeTriageAce: (id: EntityId, rationale: string) => Promise<CommandResult>;
  escalateDistressAlert: (id: EntityId, note: string) => Promise<CommandResult>;
  prepareTriageDossier: (esrId: EntityId, force?: boolean) => Promise<CommandResult>;
  getWorkItem: <T>(id: string, signal?: AbortSignal) => Promise<T>;
  requestStageReview: (stage: StageKey, entityId: string, signal?: AbortSignal) => Promise<AIStageReview>;
  reviewIntakeDocuments: (esrId: string, signal: AbortSignal, disposition?: { reviewId: string; decision: DocumentReviewDecision }) => Promise<void>;
  recordOutreach: (esrId: EntityId, appointmentId: EntityId, channel: Communication['channel'], outcome: Communication['outcome'], summary: string, details?: { attemptedAt?: string; voicemail?: 'left' | 'unavailable' }) => Promise<CommandResult>;
  holdAppointment: (requirementOrExamId: EntityId | EntityId[], examinerId: EntityId, slot: { slotId?: string; startsAt: string; endsAt: string; facilityId: string; modality: Modality }) => Promise<CommandResult>;
  holdCoordinatedAppointments: (items: Array<{ requirementId: EntityId; examinerId: EntityId; slot: { slotId?: string; startsAt: string; endsAt: string; facilityId: string; modality: Modality } }>) => Promise<CommandResult>;
  confirmAppointment: (id: EntityId, note?: string) => Promise<CommandResult>;
  setSchedulingDistanceLimit: (miles: number) => Promise<CommandResult>;
  verifyVeteranLocation: (esrId: EntityId, location: { city?: string; stateCode?: string; postalCode?: string }) => Promise<CommandResult>;
  recordAppointmentLogistics: (id: EntityId, input: Omit<AppointmentLogistics, 'travelDistanceMiles' | 'travelLimitMiles' | 'recordedAt'>) => Promise<CommandResult>;
  rescheduleAppointment: (id: EntityId, slot: { startsAt: string; endsAt: string; facilityId: string; modality: Modality }, coordinationReference?: string) => Promise<CommandResult>;
  cancelAppointment: (id: EntityId, reason: string) => Promise<CommandResult>;
  saveDBQDraft: (id: EntityId, values: FieldValues) => Promise<CommandResult>;
  validateDBQ: (id: EntityId) => Promise<CommandResult>;
  reviewDiagnostic: (id: EntityId) => Promise<CommandResult>;
  recordDiagnosticResult: (id: EntityId, summary: string, sourceReference: string, reportedFlag?: DiagnosticReportedFlag) => Promise<CommandResult>;
  recordExamEncounter: (id: EntityId, note: string) => Promise<CommandResult>;
  recordDiagnosticException: (id: EntityId, reason: 'claimant-declined' | 'medically-inadvisable', note: string) => Promise<CommandResult>;
  completeClinicalReview: (id: EntityId, attestation: { versionId: string; confirmed: boolean; draftRevision: number }) => Promise<CommandResult>;
  returnClinicalToScheduling: (examOrDbqId: EntityId, reason: string) => Promise<CommandResult>;
  requestCorrection: (id: EntityId, comment: string) => Promise<CommandResult>;
  addQAFinding: (reviewId: EntityId, input: { title: string; sectionReference: string; issueCategory: import('../domain/model').QAFinding['issueCategory']; severity: import('../domain/model').QAFinding['severity']; comment: string }) => Promise<CommandResult>;
  resubmitCorrection: (id: EntityId, values: FieldValues) => Promise<CommandResult>;
  resolveQAFinding: (id: EntityId, comment: string) => Promise<CommandResult>;
  approveQAReview: (id: EntityId, note: string) => Promise<CommandResult>;
  runQAChecklist: (id: EntityId) => Promise<CommandResult>;
  ensureResultsPackage: (esrId: EntityId) => Promise<CommandResult>;
  validateResultsPackage: (id: EntityId) => Promise<CommandResult>;
  prepareResultsPackage: (id: EntityId) => Promise<CommandResult>;
  completeReconciliationTask: (id: EntityId, note: string) => Promise<CommandResult>;
  createReconciliationTask: (id: EntityId, details: string) => Promise<CommandResult>;
  recordDeliveryReview: (id: EntityId, details: string) => Promise<CommandResult>;
  simulatePackageTransmission: (id: EntityId) => Promise<CommandResult>;
  recordSimulatedPackageResponse: (id: EntityId, response: 'accepted' | 'report-fix' | 'new-exam-requested', details: string, targetId?: EntityId) => Promise<CommandResult>;
  recordSimulatedContentionCancellation: (esrId: EntityId, contentionId: EntityId, reason: string) => Promise<CommandResult>;
  closeESR: (id: EntityId, note: string) => Promise<CommandResult>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | undefined>(undefined);
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<WorkspacePayload>();
  const [runtime, setRuntime] = useState<WorkspaceRuntime>();
  const [loading, setLoading] = useState(true);
  const [storageError, setStorageError] = useState('');
  const stateRef = useRef<WorkspacePayload | undefined>(undefined);
  const sessionVersion = useRef(0);
  const queueRef = useRef<Promise<void>>(Promise.resolve());

  const acceptState = useCallback((incoming: WorkspacePayload, version: number) => {
    if (version !== sessionVersion.current || incoming.revision <= (stateRef.current?.revision ?? -1)) return;
    stateRef.current = incoming;
    setState(incoming);
  }, []);

  useEffect(() => {
    let active = true;
    let mode: WorkspaceRuntime['mode'] | undefined;
    const version = sessionVersion.current;
    loadRuntime().then(async (settings) => {
      if (!active) return;
      mode = settings.mode;
      setRuntime(settings);
      const workspace = await loadWorkspace();
      if (active) acceptState(workspace, version);
    }).catch((error: unknown) => {
      if (mode === 'dev' && error instanceof WorkspaceApiError && error.status === 401) return;
      if (active) setStorageError(error instanceof Error ? error.message : 'The workspace could not be loaded.');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [acceptState]);

  const workspaceLoaded = Boolean(state);
  useEffect(() => {
    if (!workspaceLoaded) return;
    const version = sessionVersion.current;
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        acceptState(await loadWorkspace(), version);
      } catch (error) {
        if (error instanceof WorkspaceApiError && error.status === 401 && version === sessionVersion.current) {
          sessionVersion.current += 1;
          stateRef.current = undefined;
          setState(undefined);
        }
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [workspaceLoaded, acceptState]);

  const getWorkItem = useCallback(async <T,>(id: string, signal?: AbortSignal): Promise<T> => {
    const version = sessionVersion.current;
    try { return await loadWorkItem<T>(id, signal); }
    catch (error) {
      if (!signal?.aborted && error instanceof WorkspaceApiError && error.status === 404) {
        await loadWorkspace().then(incoming => { if (!signal?.aborted) acceptState(incoming, version); }).catch(() => {});
      }
      throw error;
    }
  }, [acceptState]);

  const run = useCallback((action: string, args: unknown[], message: string): Promise<CommandResult> => {
    const version = sessionVersion.current;
    const operation = queueRef.current.then(async (): Promise<CommandResult> => {
      const current = stateRef.current;
      if (!current || version !== sessionVersion.current) return { ok: false, message: 'Open the workspace before making changes.' };
      try {
        const result = await sendWorkspaceCommand(action, args, current.revision);
        acceptState(result.state, version);
        return { ok: true, message, value: result.value };
      } catch (error) {
        if (error instanceof WorkspaceApiError && error.status === 409) {
          await loadWorkspace().then(incoming => acceptState(incoming, version)).catch(() => {});
        }
        return { ok: false, message: error instanceof Error ? error.message : 'The action could not be saved.' };
      }
    });
    queueRef.current = operation.then(() => undefined, () => undefined);
    return operation;
  }, [acceptState]);

  const importIntakeDocument = useCallback(async (body: FormData, esrId?: string, examManagementRequest = false) => {
    const version = sessionVersion.current;
    const result = !esrId && examManagementRequest
      ? await receiveIntakeXml(body)
      : await intakeRequest(esrId ? `/${encodeURIComponent(esrId)}/documents` : '', body);
    acceptState(result.state, version);
    return result.esrId;
  }, [acceptState]);

  const retryIntakeDocument = useCallback(async (esrId: string, documentId: string): Promise<CommandResult> => {
    try {
      const version = sessionVersion.current;
      const result = await intakeRequest(`/${encodeURIComponent(esrId)}/documents/${encodeURIComponent(documentId)}/retry`, undefined);
      acceptState(result.state, version);
      return { ok: true, message: 'Document processing queued again.' };
    } catch (error) {
      return { ok: false, message: error instanceof Error ? error.message : 'The document could not be queued.' };
    }
  }, [acceptState]);

  const receiveIntakeExample = useCallback(async (scenario: string) => {
    const version = sessionVersion.current;
    const result = await receiveIntakeSample(scenario);
    acceptState(result.state, version);
    return result.esrId;
  }, [acceptState]);

  const reviewIntakeDocuments = useCallback(async (esrId: string, signal: AbortSignal, disposition?: { reviewId: string; decision: DocumentReviewDecision }) => {
    const version = sessionVersion.current;
    const result = await requestIntakeDocumentReview(esrId, signal, disposition);
    acceptState(result.state, version);
  }, [acceptState]);

  const prepareTriageDossier = useCallback(async (esrId: EntityId, force = false): Promise<CommandResult> => {
    try {
      const version = sessionVersion.current;
      const result = await triggerTriagePrepare(esrId, force);
      acceptState(result.state, version);
      return { ok: true, message: 'Clinical dossier and examination plans updated.' };
    } catch (error) {
      return { ok: false, message: error instanceof Error ? error.message : 'Unable to prepare clinical triage dossier.' };
    }
  }, [acceptState]);

  const value = useMemo<WorkspaceContextValue>(() => {
    return {
      state, loading, storageError, runtime, importIntakeDocument, retryIntakeDocument, receiveIntakeExample,
      reviewIntakeException: (id, disposition, resolution) => run('reviewIntakeException', [id, disposition, resolution], disposition === 'excluded' ? 'Duplicate document excluded from evidence.' : disposition === 'resolved' ? 'Intake exception disposition saved.' : 'Intake escalation recorded.'),
      recordIntakeReceipt: (id, receiptReference) => run('recordIntakeReceipt', [id, receiptReference], 'ESR receipt and acknowledgement recorded.'),
      completeDocumentProcessing: (esrId) => run('completeDocumentProcessing', [esrId], 'Document processing state saved.'),
      updateDocumentCategory: (documentId, category) => run('updateDocumentCategory', [documentId, category], 'Document category updated.'),
      validateIntake: (esrId) => run('validateIntake', [esrId], 'Intake validated; related contentions are available in Medical Triage.'),
      saveTriageDraft: (id, plan) => run('saveTriageDraft', [id, plan], 'Examination plan draft saved.'),
      approveTriagePlan: (id, plan) => run('approveTriagePlan', [id, plan], 'Examination plan approved and sent to Scheduling.'),
      requestTriageClarification: (id, note) => run('requestTriageClarification', [id, note], 'Clarification request recorded.'),
      resumeTriageReview: (id, note) => run('resumeTriageReview', [id, note], 'Clarification response recorded. Review reopened.'),
      authorizeTriageAce: (id, rationale) => run('authorizeTriageAce', [id, rationale], 'ACE eligibility authorized by clinician for this examination.'),
      escalateDistressAlert: (id, note) => run('escalateDistressAlert', [id, note], 'Clinical safety alert escalated to authorized staff.'),
      prepareTriageDossier,
      getWorkItem,
      requestStageReview: generateStageReview,
      reviewIntakeDocuments,
      recordOutreach: (esrId, appointmentId, channel, outcome, summary, details) => run('recordOutreach', [esrId, appointmentId, channel, outcome, summary, details], 'Claimant contact attempt recorded.'),
      holdAppointment: (id, examinerId, slot) => run('holdAppointment', [id, examinerId, slot], 'Appointment slot recorded; claimant confirmation is pending.'),
      holdCoordinatedAppointments: (items) => run('holdCoordinatedAppointments', [items], 'Coordinated appointment package held; claimant confirmation is pending.'),
      confirmAppointment: (id, note) => run('confirmAppointment', [id, note], 'Appointment confirmation saved.'),
      setSchedulingDistanceLimit: (miles) => run('setSchedulingDistanceLimit', [miles], 'Travel limit saved.'),
      verifyVeteranLocation: (esrId, location) => run('verifyVeteranLocation', [esrId, location], 'Veteran scheduling location verified.'),
      recordAppointmentLogistics: (id, input) => run('recordAppointmentLogistics', [id, input], 'Logistics details recorded.'),
      rescheduleAppointment: (id, slot, coordinationReference) => run('rescheduleAppointment', [id, slot, coordinationReference], 'Appointment rescheduled after eligibility and conflict checks.'),
      cancelAppointment: (id, reason) => run('cancelAppointment', [id, reason], 'Appointment cancellation recorded.'),
      saveDBQDraft: (id, values) => run('saveDBQDraft', [id, values], 'Draft saved to the workspace.'),
      validateDBQ: (id) => run('validateDBQ', [id], 'DBQ validation completed.'),
      reviewDiagnostic: (id) => run('reviewDiagnostic', [id], 'Diagnostic review recorded.'),
      recordDiagnosticResult: (id, summary, sourceReference, reportedFlag) => run('recordDiagnosticResult', [id, summary, sourceReference, reportedFlag], 'Diagnostic result and source reference saved.'),
      recordExamEncounter: (id, note) => run('recordExamEncounter', [id, note], 'Encounter recorded.'),
      recordDiagnosticException: (id, reason, note) => run('recordDiagnosticException', [id, reason, note], 'Diagnostic exception recorded.'),
      completeClinicalReview: (id, attestation) => run('completeClinicalReview', [id, attestation], 'Clinician review recorded and routed to QA.'),
      returnClinicalToScheduling: (examOrDbqId, reason) => run('returnClinicalToScheduling', [examOrDbqId, reason], 'Examination returned to Stage 3 (Scheduling) for rework.'),
      requestCorrection: (id, comment) => run('requestCorrection', [id, comment], 'Correction task created for the linked DBQ.'),
      addQAFinding: (id, input) => run('addQAFinding', [id, input], 'QA finding added to this report review.'),
      resubmitCorrection: (id, values) => run('resubmitCorrection', [id, values], 'Correction draft saved. Record clinician sign-off to send it to QA.'),
      resolveQAFinding: (id, comment) => run('resolveQAFinding', [id, comment], 'QA finding disposition saved.'),
      approveQAReview: (id, note) => run('approveQAReview', [id, note], 'QA approval recorded; result package readiness updated.'),
      runQAChecklist: (id) => run('runQAChecklist', [id], 'Deterministic QA checks completed for the current report version.'),
      ensureResultsPackage: (esrId) => run('ensureResultsPackage', [esrId], 'Results package opened or created for review.'),
      validateResultsPackage: (id) => run('validateResultsPackage', [id], 'Package validation completed.'),
      prepareResultsPackage: (id) => run('prepareResultsPackage', [id], 'Package preparation recorded. External submission is not enabled.'),
      completeReconciliationTask: (id, note) => run('completeReconciliationTask', [id, note], 'Reconciliation resolution recorded.'),
      createReconciliationTask: (id, details) => run('createReconciliationTask', [id, details], 'Reconciliation task recorded.'),
      recordDeliveryReview: (id, details) => run('recordDeliveryReview', [id, details], 'Staff review note saved; external response remains unverified.'),
      simulatePackageTransmission: (id) => run('simulatePackageTransmission', [id], 'Package opened for internal delivery review.'),
      recordSimulatedPackageResponse: (id, response, details, targetId) => run('recordSimulatedPackageResponse', [id, response, details, targetId], 'Internal review response and handoff recorded.'),
      recordSimulatedContentionCancellation: (esrId, contentionId, reason) => run('recordSimulatedContentionCancellation', [esrId, contentionId, reason], 'Contention cancellation recorded.'),
      closeESR: (id, note) => run('closeESR', [id, note], 'ESR closure recorded.'),
    };
  }, [state, loading, storageError, runtime, importIntakeDocument, retryIntakeDocument, receiveIntakeExample, reviewIntakeDocuments, prepareTriageDossier, getWorkItem, run]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('useWorkspace must be used inside WorkspaceProvider.');
  return context;
}

