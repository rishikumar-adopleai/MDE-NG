import type { ESR, EntityId, StageKey, WorkItem, WorkspaceState } from './model.ts';

export const byId = <T extends { id: EntityId }>(map: Record<EntityId, T>, id: EntityId | undefined) => id ? map[id] : undefined;
export const getESRs = (state: WorkspaceState) => Object.values(state.esrsById).sort((a, b) => b.receivedAt.localeCompare(a.receivedAt));
export const getESRById = (state: WorkspaceState, esrId: EntityId) => state.esrsById[esrId];
export const getContentionsByESR = (state: WorkspaceState, esrId: EntityId) => Object.values(state.contentionsById).filter((item) => item.esrId === esrId);
export const getRequirementsByESR = (state: WorkspaceState, esrId: EntityId) => Object.values(state.examinationRequirementsById).filter((item) => item.esrId === esrId);
export const getExaminationsByESR = (state: WorkspaceState, esrId: EntityId) => Object.values(state.examinationsById).filter((item) => item.esrId === esrId);
export const getAppointmentsByExamination = (state: WorkspaceState, examinationId: EntityId) => Object.values(state.appointmentsById).filter((item) => item.examinationId === examinationId);
export const getDBQsByExamination = (state: WorkspaceState, examinationId: EntityId) => Object.values(state.dbqsById).filter((item) => item.examinationId === examinationId);
export const getDBQsByESR = (state: WorkspaceState, esrId: EntityId) => Object.values(state.dbqsById).filter((item) => item.esrId === esrId);
export const getWorkItemsByStage = (state: WorkspaceState, stage: StageKey) => Object.values(state.workItemsById).filter((item) => item.stage === stage && item.status !== 'completed' && item.status !== 'cancelled');
export const getQAFindingById = (state: WorkspaceState, findingId: EntityId) => state.qaFindingsById[findingId];
export const getDeliveryEventsByPackage = (state: WorkspaceState, packageId: EntityId) => Object.values(state.deliveryEventsById).filter((item) => item.packageId === packageId).sort((a, b) => a.recordedAt.localeCompare(b.recordedAt));
export const getAIInsight = (state: WorkspaceState, esrId: EntityId, stage: StageKey) => Object.values(state.aiInsightsById).find((item) => item.esrId === esrId && item.stage === stage);

function openWorkItem(state: WorkspaceState, args: Omit<WorkItem, 'id' | 'version' | 'createdAt' | 'updatedAt'>, now: string, target: Record<string, WorkItem>) {
  const id = `work-${args.stage.toLowerCase().replace(/[^a-z]+/g, '-')}-${args.entityId}`;
  const previous = state.workItemsById[id];
  target[id] = { ...args, id, version: (previous?.version ?? 0) + 1, createdAt: previous?.createdAt ?? now, updatedAt: now };
}

export function refreshWorkItems(state: WorkspaceState, now = new Date().toISOString()): WorkspaceState {
  const next = {} as WorkspaceState['workItemsById'];
  const makeBase = (esrId: string, entityId: string, entityType: WorkItem['entityType'], stage: StageKey, title: string, priority: WorkItem['priority'], blockingReasons: string[], status: WorkItem['status'] = 'queued') => ({
    esrId, entityId, entityType, stage, title, priority, status, blockingReasons, assignedTo: state.esrsById[esrId]?.assignedTeamId,
  });

  Object.values(state.esrsById).forEach((esr) => {
    const exceptions = Object.values(state.intakeExceptionsById).filter((issue) => issue.esrId === esr.id && issue.status === 'open');
    if (!esr.workflowClosedAt && esr.intakeStatus !== 'ready-for-triage' && esr.intakeStatus !== 'completed') {
      const missingReceipt = esr.receiptStatus !== 'received' || !esr.aepRecordedAt;
      const blockers = [...exceptions.map((issue) => issue.title), ...(missingReceipt ? ['Record the local ESR receipt and AEP acknowledgement'] : [])];
      const pendingUpload = esr.evidenceDocumentIds.some(id => state.evidenceDocumentsById[id]?.processing?.status === 'queued');
      openWorkItem(state, makeBase(esr.id, esr.id, 'esr', 'Intake',
        blockers.length ? 'Review intake handoff' : pendingUpload ? 'Process source documents' : 'Validate received ESR',
        esr.priority, blockers, blockers.length ? 'blocked' : pendingUpload ? 'in-progress' : 'queued'), now, next);
    }
  });

  const hasActiveContention = (ids: string[]) => !ids.length || ids.some(id => state.contentionsById[id]?.simulatedDisposition !== 'cancelled');
  Object.values(state.contentionsById).forEach((contention) => {
    const parentESR = state.esrsById[contention.esrId];
    if (parentESR && contention.simulatedDisposition !== 'cancelled' && !parentESR.workflowClosedAt && ['ready-for-triage', 'completed'].includes(parentESR.intakeStatus) && ['awaiting-review', 'in-review', 'clarification-required'].includes(contention.status)) {
      const esr = state.esrsById[contention.esrId];
      const status = contention.status === 'clarification-required' ? 'blocked' : contention.status === 'in-review' ? 'in-progress' : 'queued';
      openWorkItem(state, makeBase(contention.esrId, contention.id, 'contention', 'Medical Triage', contention.title, esr?.priority ?? 'routine', contention.clarificationReason ? [contention.clarificationReason] : [], status), now, next);
    }
  });

  Object.values(state.examinationRequirementsById).forEach((requirement) => {
    const related = Object.values(state.examinationsById).find((exam) => exam.requirementIds.includes(requirement.id));
    const needsScheduling = requirement.status === 'approved' && (!related || related.status === 'awaiting-scheduling' || related.appointmentIds.every((id) => ['cancelled', 'no-show'].includes(state.appointmentsById[id]?.status)));
    if (needsScheduling && hasActiveContention(requirement.contentionIds) && !state.esrsById[requirement.esrId]?.workflowClosedAt) {
      const esr = state.esrsById[requirement.esrId];
      openWorkItem(state, makeBase(requirement.esrId, related?.id ?? requirement.id, related ? 'examination' : 'examination-requirement', 'Scheduling', requirement.title, esr?.priority ?? 'routine', [], 'queued'), now, next);
    }
  });

  Object.values(state.appointmentsById).filter((item) => {
    const exam = state.examinationsById[item.examinationId];
    const canManage = ['held', 'reschedule-requested'].includes(item.status) ||
      (item.status === 'confirmed-locally' && Date.parse(item.startsAt) > Date.parse(now) && exam?.requiredDbqIds.every(id => !state.dbqsById[id]?.signedVersionId));
    return canManage && hasActiveContention(exam?.contentionIds ?? []) && !state.esrsById[item.esrId]?.workflowClosedAt;
  }).forEach((appointment) => {
    const held = appointment.status === 'held';
    openWorkItem(state, makeBase(appointment.esrId, appointment.id, 'appointment', 'Scheduling',
      held ? 'Confirm held appointment' : 'Manage appointment',
      state.esrsById[appointment.esrId]?.priority ?? 'routine', [], held ? 'in-progress' : 'queued'), now, next);
  });

  Object.values(state.dbqsById).forEach((dbq) => {
    const exam = state.examinationsById[dbq.examinationId];
    const hasConfirmedAppointment = exam?.appointmentIds.some(id =>
      ['confirmed-locally', 'completed'].includes(state.appointmentsById[id]?.status));
    if (hasConfirmedAppointment && hasActiveContention(dbq.contentionIds) && !state.esrsById[dbq.esrId]?.workflowClosedAt && ['draft', 'in-progress', 'awaiting-clinician-review', 'correction-required'].includes(dbq.status)) {
      const correction = Object.values(state.correctionRequestsById).some((item) => item.dbqId === dbq.id && ['open', 'in-progress'].includes(item.status));
      const status = dbq.status === 'correction-required' ? 'blocked' : dbq.status === 'in-progress' ? 'in-progress' : 'queued';
      const examiner = state.examinationsById[dbq.examinationId]?.examinerId;
      openWorkItem(state, { ...makeBase(dbq.esrId, dbq.id, correction ? 'correction-request' : 'dbq', 'Clinical Evaluation', correction ? 'Address QA correction' : dbq.formType, state.esrsById[dbq.esrId]?.priority ?? 'routine', dbq.validationErrors, status), assignedTo: examiner }, now, next);
    }
  });

  Object.values(state.qaReviewsById).forEach((review) => {
    const findingIds = review.findingIds;
    const openBlocking = findingIds.some((id) => {
      const finding = state.qaFindingsById[id];
      return finding?.severity === 'blocking' && ['open', 'correction-requested'].includes(finding.status);
    });
    if (hasActiveContention(state.dbqsById[review.dbqId]?.contentionIds ?? []) && !state.esrsById[review.esrId]?.workflowClosedAt && ['queued', 'in-review', 'returned'].includes(review.status)) {
      const qaPriority = review.isVaReportFix ? 'urgent' : (state.esrsById[review.esrId]?.priority ?? 'routine');
      openWorkItem(state, makeBase(review.esrId, review.id, 'qa-review', 'Internal QA', state.dbqsById[review.dbqId]?.formType ?? 'QA review', qaPriority, openBlocking ? ['Blocking finding needs disposition'] : [], review.status === 'in-review' ? 'in-progress' : 'queued'), now, next);
    }
  });

  Object.values(state.resultsPackagesById).forEach((item) => {
    const esr = state.esrsById[item.esrId];
    const isPendingClosure = item.simulatedResponse === 'accepted' && !esr?.workflowClosedAt;
    if (item.preparationStatus !== 'closed' && !esr?.workflowClosedAt) {
      const blocked = item.validationStatus === 'blocked' || item.blockingReasons.length > 0;
      const title = esr?.esrType === 'rework'
        ? `Rework Delivery (${esr.reworkDetails?.reworkCategory ?? 'rework'})`
        : item.simulatedResponse === 'awaiting-response'
          ? 'Record simulated package response'
          : isPendingClosure
            ? 'Complete ESR reconciliation'
            : 'Review results package';
      openWorkItem(state, makeBase(item.esrId, item.id, 'results-package', 'VA Delivery & Review', title, esr?.priority ?? 'routine', item.blockingReasons, blocked ? 'blocked' : 'queued'), now, next);
    }
  });

  // Ensure incoming VA Rework ESRs awaiting assessment / clarification / deferred exam surface in VA Delivery & Review even before a package exists
  Object.values(state.esrsById).forEach((esr) => {
    if (esr.esrType !== 'rework' || esr.workflowClosedAt) return;
    const hasOpenPkg = Object.values(state.resultsPackagesById).some((p) => p.esrId === esr.id && p.preparationStatus !== 'closed');
    if (hasOpenPkg) return;
    const rd = esr.reworkDetails;
    const label = rd?.assessmentStatus === 'pending-assessment'
      ? `Assess VA Rework ESR (${rd.reworkCategory})`
      : rd?.reworkRoute === 'pending-clarification'
        ? 'Rework Pending VA Clarification'
        : rd?.reworkRoute === 'additional-exam-deferred'
          ? 'Rework Additional Exam (Deferred)'
          : 'Rework in Progress';
    const isBlocked = rd?.reworkRoute === 'pending-clarification' || rd?.reworkRoute === 'additional-exam-deferred';
    const blocking = rd?.reworkRoute === 'pending-clarification'
      ? ['Awaiting VA clarification before routing']
      : rd?.reworkRoute === 'additional-exam-deferred'
        ? ['Additional examination flagged as deferred in demo']
        : [];
    openWorkItem(state, makeBase(esr.id, esr.id, 'esr', 'VA Delivery & Review', label, esr.priority, blocking, isBlocked ? 'blocked' : 'queued'), now, next);
  });

  return { ...state, workItemsById: next };
}

export function searchESRs(state: WorkspaceState, query: string): ESR[] {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return getESRs(state);
  return getESRs(state).filter((esr) => {
    const veteran = state.veteransById[esr.veteranId];
    const contentions = getContentionsByESR(state, esr.id).map((item) => item.title).join(' ');
    const examinations = getExaminationsByESR(state, esr.id).map((exam) => {
      const assigned = exam.examinerId ? state.examinersById[exam.examinerId]?.displayName : '';
      return `${exam.id} ${exam.requirementIds.map((id) => state.examinationRequirementsById[id]?.examinationType).join(' ')} ${assigned ?? ''} ${exam.status}`;
    }).join(' ');
    return `${esr.id} ${esr.sourceReference} ${esr.practiceScenario?.name ?? ''} ${esr.practiceScenario?.exercise ?? ''} ${veteran?.claimIdentifier ?? ''} ${veteran?.displayName ?? ''} ${veteran?.serviceGroup ?? ''} ${contentions} ${examinations}`.toLocaleLowerCase().includes(term);
  });
}

export function getESRProgress(state: WorkspaceState, esrId: string) {
  const requirements = getRequirementsByESR(state, esrId);
  const dbqs = getDBQsByESR(state, esrId);
  const approved = dbqs.filter((item) => item.status === 'approved-for-package').length;
  const completed = requirements.filter((item) => item.status === 'completed').length + approved;
  const total = Math.max(requirements.length + dbqs.length, 1);
  return { completed, total, percentage: Math.round((completed / total) * 100) };
}

export function getESRQueueStatus(state: WorkspaceState, esrId: string): string {
  const esr = state.esrsById[esrId];
  if (!esr) return 'Unavailable';
  const hasIntakeIssues = Object.values(state.intakeExceptionsById).some((item) => item.esrId === esrId && item.status === 'open');
  if (esr.workflowClosedAt) return esr.workflowDisposition === 'cancelled' ? 'Cancelled in this workspace' : 'Completed in this workspace';
  if (hasIntakeIssues) return 'Intake exception';
  const dbqs = getDBQsByESR(state, esrId);
  const openFindings = Object.values(state.qaFindingsById).some((item) => item.esrId === esrId && item.severity === 'blocking' && ['open', 'correction-requested'].includes(item.status));
  if (openFindings) return 'Correction required';
  if (dbqs.length > 0 && dbqs.every((item) => item.status === 'approved-for-package')) return 'Ready for package';
  if (Object.values(state.examinationsById).some((item) => item.esrId === esrId && item.status === 'awaiting-clinical-review')) return 'Clinical review';
  if (Object.values(state.examinationsById).some((item) => item.esrId === esrId && item.status === 'awaiting-scheduling')) return 'Scheduling';
  if (getContentionsByESR(state, esrId).some((item) => ['awaiting-review', 'in-review', 'clarification-required'].includes(item.status))) return 'Triage review';
  return esr.intakeStatus === 'ready-for-triage' ? 'Ready for triage' : esr.intakeStatus.replaceAll('-', ' ');
}

export function getCaseMetrics(state: WorkspaceState) {
  const esrs = Object.values(state.esrsById);
  const workItems = Object.values(state.workItemsById);
  return {
    esrs: esrs.length,
    intake: workItems.filter((item) => item.stage === 'Intake').length,
    exceptions: Object.values(state.intakeExceptionsById).filter((item) => ['open', 'escalated'].includes(item.status)).length,
    needsReview: workItems.filter((item) => item.stage === 'Medical Triage' || item.stage === 'Internal QA').length,
    ready: workItems.filter((item) => item.status === 'queued' && ['Scheduling', 'VA Delivery & Review'].includes(item.stage)).length,
  };
}
