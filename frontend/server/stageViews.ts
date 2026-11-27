import type { Appointment, Modality, WorkItem, WorkspaceState } from '../src/domain/model.ts';
import { appointmentConfirmation, appointmentTravel, dbqForm, diagnosticReady, examinerEligibility, validateDbqValues, qaApprovalBlockers, qaFindingCategories, qaFindingSeverities, closureBlockers, packageContents, pendingReconciliation, WorkflowRuleError } from '../src/services/workflowService.ts';

export function schedulingView(state: WorkspaceState, work: WorkItem) {
  const esr = state.esrsById[work.esrId];
  const veteran = state.veteransById[esr.veteranId];
  const appointment = work.entityType === 'appointment' ? state.appointmentsById[work.entityId] as Appointment | undefined : undefined;
  const exam = appointment ? state.examinationsById[appointment.examinationId] : state.examinationsById[work.entityId];
  const requirements = exam ? exam.requirementIds.map(id => state.examinationRequirementsById[id]) : [state.examinationRequirementsById[work.entityId]];
  if (!requirements[0]) throw new WorkflowRuleError('This examination requirement is unavailable.');
  const focusId = exam?.id ?? work.entityId;
  const modalities = requirements[0].permittedModalities.filter(modality => requirements.every(item => item.permittedModalities.includes(modality)));
  const specialist = requirements.some(item => item.requiredSpecialties.length > 0);
  const slotsFor = (modality: Modality, examinerId: string) => state.examinersById[examinerId].availableSlots
    .filter(slot => slot.modality === modality && !examinerEligibility(state, focusId, examinerId, modality, slot, appointment?.id))
    .map(slot => {
      const facility = state.facilitiesById[slot.facilityId];
      return { slot, facility: facility.name, city: facility.city, access: facility.accessibilityFeatures, ...appointmentTravel(state, esr.id, facility.id, modality, specialist) };
    }).sort((a, b) => a.travelDistanceMiles - b.travelDistanceMiles || Date.parse(a.slot.startsAt) - Date.parse(b.slot.startsAt));
  const candidates = appointment ? [] : Object.values(state.examinersById).flatMap(examiner => {
    const pathways = modalities.flatMap(modality => {
      const slots = slotsFor(modality, examiner.id);
      return slots.length ? [{ modality, slots }] : [];
    });
    return pathways.length ? [{ id: examiner.id, name: examiner.displayName, specialties: examiner.specialties, credentials: examiner.credentials, pathways }] : [];
  });
  const confirmation = appointment ? appointmentConfirmation(state, appointment) : undefined;
  return {
    focusId, requirements, modalities, recommendedModality: requirements[0].selectedModality,
    accessibilityNeeds: veteran?.accessibilityNeeds ?? [], examinerPreference: veteran?.examinerPreference,
    contactChannels: ['phone' as const, ...(veteran?.communicationOptIns ?? [])],
    candidates, appointment, confirmation, requiresMilitaryReference: esr.schedulingProgram === 'IDES',
    appointmentFacility: appointment && state.facilitiesById[appointment.facilityId],
    appointmentExaminer: appointment && state.examinersById[appointment.examinerId]?.displayName,
    canReschedule: Boolean(appointment && !appointment.history.some(item => item.status === 'reschedule-requested')),
    replacements: appointment ? slotsFor(appointment.modality, appointment.examinerId).filter(({slot}) =>
      slot.startsAt !== appointment.startsAt || slot.facilityId !== appointment.facilityId) : [],
  };
}

export function evidenceFor(state: WorkspaceState, contentionIds: string[], esrId: string) {
  const ids = new Set(contentionIds.flatMap(id => state.contentionsById[id]?.evidenceDocumentIds ?? []));
  const allowedEsrIds = new Set([esrId, state.esrsById[esrId]?.linkedOriginalEsrId].filter(Boolean));
  return Array.from(ids).flatMap(id => {
    const item = state.evidenceDocumentsById[id];
    if (!item || !allowedEsrIds.has(item.esrId)) return [];
    return [{ id, title: item.title, sourceReference: item.sourceReference,
      sources: item.syntheticExcerpts?.filter(source => contentionIds.includes(source.contentionId)).map(source => ({page: source.pageNumber, text: source.text}))
        ?? item.processing?.sources ?? [],
    }];
  });
}

export function clinicalView(state: WorkspaceState, work: WorkItem) {
  const dbq = state.dbqsById[work.entityId];
  const exam = dbq && state.examinationsById[dbq.examinationId];
  if (!dbq || !exam) throw new WorkflowRuleError('This clinical report is unavailable.');
  const appointments = exam.appointmentIds.map(id => state.appointmentsById[id]).filter(Boolean);
  const requiresEncounter = dbq.formVersion === 'synthetic-v2' && !appointments.some(item => item.status === 'completed');
  const diagnostics = exam.diagnosticOrderIds.map(id => state.diagnosticOrdersById[id]).filter(Boolean).map(order => ({
    order, ready: diagnosticReady(order), results: order.resultIds.map(id => state.diagnosticResultsById[id]).filter(Boolean),
  }));
  return {
    dbq, examId: exam.id, fields: dbqForm(dbq), requiresEncounter, diagnostics,
    canRecordEncounter: appointments.some(item => item.status === 'confirmed-locally'),
    examiner: exam.examinerId ? state.examinersById[exam.examinerId]?.displayName : undefined,
    correction: Object.values(state.correctionRequestsById).find(item => item.dbqId === dbq.id && ['open','in-progress','resubmitted'].includes(item.status)),
    evidence: evidenceFor(state, dbq.contentionIds, dbq.esrId),
    versions: dbq.versionIds.map(id => state.dbqVersionsById[id]).filter(Boolean).reverse(),
    errors: validateDbqValues(dbq),
  };
}

export function qaView(state: WorkspaceState, work: WorkItem) {
  const review = state.qaReviewsById[work.entityId];
  const dbq = review && state.dbqsById[review.dbqId];
  const current = review && state.dbqVersionsById[review.versionId];
  if (!review || !dbq || !current || current.dbqId !== dbq.id) throw new WorkflowRuleError('This signed report version is unavailable.');
  return {
    review, dbq, current, categories: qaFindingCategories, severities: qaFindingSeverities,
    fields: dbqForm(dbq, current.values),
    versions: dbq.versionIds.map(id => state.dbqVersionsById[id]).filter(item => item?.dbqId === dbq.id).reverse(),
    findings: review.findingIds.map(id => state.qaFindingsById[id]).filter(Boolean),
    evidence: evidenceFor(state, dbq.contentionIds, dbq.esrId),
    blockers: qaApprovalBlockers(state, review),
  };
}

export function deliveryView(state: WorkspaceState, work: WorkItem) {
  const existingPack = state.resultsPackagesById[work.entityId];
  const esrId = existingPack?.esrId ?? work.esrId;
  const esr = state.esrsById[esrId];
  const pack = existingPack ?? {
    id: `virtual-pkg-${esrId}`,
    version: 1,
    createdAt: esr?.createdAt ?? work.createdAt,
    updatedAt: esr?.updatedAt ?? work.updatedAt,
    esrId,
    dbqIds: [],
    diagnosticResultIds: [],
    qaApprovalIds: [],
    validationStatus: 'blocked' as const,
    preparationStatus: 'blocked' as const,
    externalState: 'not-enabled' as const,
    blockingReasons: work.blockingReasons,
    versionNumber: 1,
    submissions: [],
  };
  const contents = packageContents(state, esrId);
  const submitted = pack.submissions?.at(-1)?.manifest;
  const contentions = (esr?.contentionIds ?? []).map(id => state.contentionsById[id]).filter(Boolean);
  const locked = ['awaiting-response','accepted'].includes(pack.simulatedResponse ?? '');
  return {
    pack, esr, contentions, blockers: contents.issues, closureBlockers: closureBlockers(state, esrId),
    deliverableContentionIds: contents.deliverableContentionIds,
    pendingContentionIds: contents.pendingContentionIds,
    contentionEligibility: contents.contentionEligibility,
    esrCompletionStatus: contents.esrCompletionStatus,
    reports: contents.dbqIds.map(id => state.dbqsById[id]).filter(Boolean),
    reportTargets: (submitted ?? contents.manifest).reports.map(report => state.dbqsById[report.dbqId]).filter(Boolean),
    contentionTargets: contentions.filter(item => (submitted ?? contents.manifest).contentions.includes(item.id)),
    diagnostics: (locked && submitted ? submitted : contents.manifest).diagnosticResultIds.map(id => {
      const result = state.diagnosticResultsById[id];
      return { id, reference: result?.sourceReference, reviewedAt: result?.reviewedAt };
    }),
    timeline: Object.values(state.deliveryEventsById).filter(item => item.esrId === esrId).sort((a,b) => b.recordedAt.localeCompare(a.recordedAt)),
    tasks: pendingReconciliation(state, esrId).filter(item => item.packageId === pack.id),
    actions: {
      validate: !locked && Boolean(existingPack),
      prepare: !locked && Boolean(existingPack) && pack.validationStatus === 'passed' && contents.issues.length === 0 && pack.preparationStatus !== 'prepared-locally',
      simulate: !locked && Boolean(existingPack) && pack.preparationStatus === 'prepared-locally' && contents.issues.length === 0,
      respond: pack.simulatedResponse === 'awaiting-response', cancel: pack.simulatedResponse !== 'awaiting-response',
    },
  };
}

export const stageViews = {
  Scheduling: schedulingView,
  "Clinical Evaluation": clinicalView,
  "Internal QA": qaView,
  "VA Delivery & Review": deliveryView,
};
