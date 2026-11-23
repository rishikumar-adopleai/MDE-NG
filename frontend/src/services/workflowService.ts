import type {
  ActivityAction, ActivityEvent, Appointment, AppointmentLogistics, Communication, Contention, DBQ, DBQVersion, DiagnosticOrder, DeliveryEvent, EntityId, Examination, ExaminationRequirement,
  FieldValues, IntakeException, Modality, PackageManifest, QAReview, QAFinding, ResultsPackage, WorkspaceState,
} from '../domain/model.ts';
import { refreshWorkItems } from '../domain/selectors.ts';

export class WorkflowRuleError extends Error {
  constructor(message: string) { super(message); this.name = 'WorkflowRuleError'; }
}

let activitySequence = 0;
const nowIso = () => new Date().toISOString();

function copyMaps(state: WorkspaceState, keys: (keyof WorkspaceState)[]): WorkspaceState {
  const next = { ...state };
  keys.forEach((key) => { (next as unknown as Record<string, unknown>)[key] = { ...(state[key] as Record<string, unknown>) }; });
  return next;
}

function update<T extends { id: EntityId; version: number; updatedAt: string }>(record: T, patch: Partial<T>, now: string): T {
  return { ...record, ...patch, version: record.version + 1, updatedAt: now };
}

function addActivity(next: WorkspaceState, args: Omit<ActivityEvent, 'id' | 'at' | 'actorId'> & { actorId?: string }, now = nowIso()) {
  const id = `activity-${args.entityId}-${Date.now()}-${++activitySequence}`;
  next.activitiesById[id] = { ...args, id, at: now, actorId: args.actorId ?? 'user-operations' };
}

function changedStatus(next: WorkspaceState, args: Omit<ActivityEvent, 'id' | 'at' | 'actorId'> & { actorId?: string }, previous?: string, current?: string, now = nowIso()) {
  addActivity(next, { ...args, previousStatus: args.previousStatus ?? previous, newStatus: args.newStatus ?? current }, now);
}

const text = (value: FieldValues[string] | undefined) => value === null || value === undefined ? '' : String(value).trim();

const distanceMiles = (aLat: number, aLng: number, bLat: number, bLng: number) => {
  const radians = (value: number) => value * Math.PI / 180;
  const dLat = radians(bLat - aLat); const dLng = radians(bLng - aLng);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(radians(aLat)) * Math.cos(radians(bLat)) * Math.sin(dLng / 2) ** 2;
  return Math.round(3959 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
};

export function getTravelDistanceMiles(state: WorkspaceState, esrId: EntityId, facilityId: EntityId) {
  const esr = state.esrsById[esrId];
  const veteran = esr ? state.veteransById[esr.veteranId] : undefined;
  const home = veteran ? state.facilitiesById[veteran.homeFacilityId] : undefined;
  const destination = state.facilitiesById[facilityId];
  return home && destination ? distanceMiles(home.latitude, home.longitude, destination.latitude, destination.longitude) : undefined;
}

export function recordIntakeReceipt(state: WorkspaceState, esrId: EntityId, receiptReference = '') {
  const esr = state.esrsById[esrId];
  if (!esr) throw new WorkflowRuleError('The selected ESR is no longer available.');
  if (esr.workflowClosedAt) throw new WorkflowRuleError('This ESR is already closed in the local workflow.');
  if (esr.receiptStatus === 'received' && esr.aepRecordedAt) throw new WorkflowRuleError('A local receipt and AEP acknowledgement are already recorded.');
  const now = nowIso();
  const localReference = receiptReference.trim() || `LOCAL-ESR-${esr.id}`;
  const next = copyMaps(state, ['esrsById', 'activitiesById']);
  next.esrsById[esrId] = update(esr, { receiptStatus: 'received', receiptReference: localReference, aepRecordedAt: now, intakeStatus: ['new', 'acknowledgement-pending'].includes(esr.intakeStatus) ? 'processing' : esr.intakeStatus }, now);
  addActivity(next, { esrId, entityId: esrId, entityType: 'esr', action: 'intake-aep-recorded', previousStatus: 'receipt-pending', newStatus: 'acknowledged-locally', rationale: `Synthetic ESR receipt ${localReference} and AEP acknowledgement recorded before document processing. No external acknowledgement was sent.` }, now);
  return refreshWorkItems(next, now);
}

export function dbqForm(dbq: DBQ, values = dbq.values) {
  const fields = dbq.formType.toLowerCase().includes('opinion')
    ? [{ key: 'opinionQuestion', label: 'Medical opinion question' }, { key: 'rationale', label: 'Clinical rationale' }]
    : [{ key: 'history', label: 'Medical history' }, { key: 'findings', label: 'Examination findings' }, { key: 'rationale', label: 'Clinical rationale' }];
  if (dbq.formVersion === 'synthetic-v2') {
    fields.push({ key: 'recordsReviewed', label: 'Records reviewed and source references' });
    if (!dbq.formType.toLowerCase().includes('opinion')) fields.push({ key: 'diagnosisOutcome', label: 'Diagnosis outcome' }, { key: 'diagnosisDetails', label: 'Diagnosis or reason it could not be established' });
  }
  return [
    ...fields.map(field => ({ ...field, required: true, options: field.key === 'diagnosisOutcome' ? ['Established', 'Could not be established'] : undefined })),
    { key: 'remarks', label: 'Remarks', required: false },
  ];
}

export function validateDbqValues(dbq: DBQ, values = dbq.values): string[] {
  const fields = dbqForm(dbq, values);
  return fields.flatMap(field => {
    if (field.required && !text(values[field.key])) return [`${field.label} is required.`];
    if ('options' in field && field.options && !field.options.includes(text(values[field.key]))) return [`Select a valid ${field.label.toLowerCase()}.`];
    return [];
  });
}

function assertClinicalAccess(state: WorkspaceState, esrId: string, examId: string) {
  const exam = state.examinationsById[examId];
  if (!exam || exam.esrId !== esrId || state.esrsById[esrId]?.workflowClosedAt || (exam.contentionIds.length > 0 && exam.contentionIds.every(id => state.contentionsById[id]?.simulatedDisposition === 'cancelled'))) throw new WorkflowRuleError('This examination is closed or unavailable.');
  if (!exam.appointmentIds.some(id => ['confirmed-locally','completed'].includes(state.appointmentsById[id]?.status))) throw new WorkflowRuleError('A confirmed appointment is required to work on this examination.');
  return exam;
}

export function recordExamEncounter(state: WorkspaceState, dbqId: string, note: string) {
  const dbq = state.dbqsById[dbqId];
  if (!dbq || !note?.trim() || note.length > 3000) throw new WorkflowRuleError('Enter a note for this simulated encounter.');
  const exam = assertClinicalAccess(state, dbq.esrId, dbq.examinationId);
  const appointment = exam.appointmentIds.map(id => state.appointmentsById[id]).find(item => item?.status === 'confirmed-locally');
  if (!appointment) throw new WorkflowRuleError('There is no confirmed appointment awaiting a completed encounter.');
  const now = nowIso();
  const next = copyMaps(state, ['appointmentsById','examinationsById','activitiesById']);
  next.appointmentsById[appointment.id] = update(appointment, { status: 'completed',
    history: [...appointment.history, { status: 'completed', at: now, actorId: 'user-clinician', note: note.trim() }],
  }, now);
  next.examinationsById[exam.id] = update(exam, { status: 'in-progress' }, now);
  addActivity(next, { esrId: dbq.esrId, entityId: appointment.id, entityType: 'appointment', action: 'exam-encounter-recorded', rationale: note.trim() }, now);
  return refreshWorkItems(next, now);
}

export function diagnosticReady(order: DiagnosticOrder) {
  return !order.required || order.status === 'reviewed' || Boolean(order.exception?.note);
}

export function recordDiagnosticException(state: WorkspaceState, orderId: string, reason: NonNullable<DiagnosticOrder['exception']>['reason'], note: string) {
  const order = state.diagnosticOrdersById[orderId];
  if (!order || !['claimant-declined', 'medically-inadvisable'].includes(reason) || !note?.trim() || note.length > 3000) throw new WorkflowRuleError('Choose a diagnostic exception and record the explanation.');
  const exam = assertClinicalAccess(state, order.esrId, order.examinationId);
  if (exam.requiredDbqIds.some(id => state.dbqsById[id]?.signedVersionId === state.dbqsById[id]?.currentVersionId && state.dbqsById[id]?.signedVersionId) || order.resultIds.length) throw new WorkflowRuleError('An existing diagnostic result or signed report prevents this exception.');
  const now = nowIso();
  const next = copyMaps(state, ['diagnosticOrdersById', 'activitiesById']);
  next.diagnosticOrdersById[order.id] = update(order, { exception: { reason, note: note.trim(), at: now } }, now);
  addActivity(next, { esrId: order.esrId, entityId: order.id, entityType: 'diagnostic-order', action: 'diagnostic-exception-recorded', rationale: note.trim() }, now);
  return refreshWorkItems(next, now);
}

export function reviewIntakeException(state: WorkspaceState, exceptionId: EntityId, disposition: 'resolved' | 'escalated', resolution: string) {
  const current = state.intakeExceptionsById[exceptionId];
  if (!current) throw new WorkflowRuleError('The selected intake exception is no longer available.');
  if (!resolution.trim()) throw new WorkflowRuleError('Add a note describing the review or escalation.');
  const now = nowIso();
  const next = copyMaps(state, ['intakeExceptionsById', 'activitiesById']);
  next.intakeExceptionsById[exceptionId] = update(current, { status: disposition, resolution: resolution.trim() }, now);
  changedStatus(next, { esrId: current.esrId, entityId: exceptionId, entityType: current.documentId ? 'evidence-document' : 'esr', action: disposition === 'resolved' ? 'intake-exception-resolved' : 'intake-exception-escalated', rationale: resolution.trim() }, current.status, disposition, now);
  return refreshWorkItems(next, now);
}

export function completeDocumentProcessing(state: WorkspaceState, esrId: EntityId) {
  const esr = state.esrsById[esrId];
  if (!esr) throw new WorkflowRuleError('The selected ESR is no longer available.');
  if (esr.receiptStatus !== 'received' || !esr.aepRecordedAt) throw new WorkflowRuleError('Record the local ESR receipt and AEP acknowledgement before document processing.');
  const documents = Object.values(state.evidenceDocumentsById).filter((item) => item.esrId === esrId);
  if (!documents.length) throw new WorkflowRuleError('No evidence documents are linked to this ESR.');
  if (!documents.some(doc => !doc.processing && ['queued', 'processing'].includes(doc.classificationStatus))) throw new WorkflowRuleError('Uploaded documents are processed by the Intake worker.');
  const now = nowIso();
  const next = copyMaps(state, ['evidenceDocumentsById', 'esrsById', 'activitiesById']);
  documents.forEach((doc) => {
    if (!doc.processing && (doc.classificationStatus === 'queued' || doc.classificationStatus === 'processing')) {
      next.evidenceDocumentsById[doc.id] = update(doc, { classificationStatus: 'indexed', classifiedPageCount: doc.pageCount - doc.unreadablePageCount }, now);
    }
  });
  if (next.esrsById[esrId].intakeStatus === 'processing' && !documents.some(doc => doc.processing?.status === 'queued')) next.esrsById[esrId] = update(next.esrsById[esrId], { intakeStatus: 'requires-attention' }, now);
  addActivity(next, { esrId, entityId: esrId, entityType: 'esr', action: 'intake-document-reviewed', rationale: 'Structured document processing was completed in the workspace.' }, now);
  return refreshWorkItems(next, now);
}

export function updateDocumentCategory(state: WorkspaceState, documentId: EntityId, category: string) {
  const doc = state.evidenceDocumentsById[documentId];
  if (!doc) throw new WorkflowRuleError('The selected document is no longer available.');
  const now = nowIso();
  const next = copyMaps(state, ['evidenceDocumentsById', 'activitiesById']);
  next.evidenceDocumentsById[documentId] = update(doc, { category: category as EvidenceDocument['category'] }, now);
  addActivity(next, {
    esrId: doc.esrId,
    entityId: documentId,
    entityType: 'evidence-document',
    action: 'intake-document-reviewed',
    rationale: `Document classified as ${category} by staff review.`,
  }, now);
  return refreshWorkItems(next, now);
}

export function validateIntake(state: WorkspaceState, esrId: EntityId) {
  const esr = state.esrsById[esrId];
  if (!esr) throw new WorkflowRuleError('The selected ESR is no longer available.');
  if (esr.receiptStatus !== 'received' || !esr.aepRecordedAt) throw new WorkflowRuleError('Record the local ESR receipt and AEP acknowledgement before validating intake.');
  if (!esr.evidenceDocumentIds.some(id => state.evidenceDocumentsById[id])) throw new WorkflowRuleError('Attach at least one source document before validating Intake.');
  const issues = Object.values(state.intakeExceptionsById).filter((item) => item.esrId === esrId && item.status === 'open');
  if (issues.length) throw new WorkflowRuleError(`${issues.length} intake exception${issues.length === 1 ? '' : 's'} still need a recorded disposition.`);
  const pendingDocuments = esr.evidenceDocumentIds.map((id) => state.evidenceDocumentsById[id]).filter((item) => item && ['queued', 'processing'].includes(item.classificationStatus));
  if (pendingDocuments.length) throw new WorkflowRuleError('Document processing is still in progress for this ESR.');
  const now = nowIso();
  const next = copyMaps(state, ['esrsById', 'activitiesById']);
  next.esrsById[esrId] = update(esr, { intakeStatus: 'ready-for-triage' }, now);
  changedStatus(next, { esrId, entityId: esrId, entityType: 'esr', action: 'intake-validated', rationale: 'Intake exceptions were dispositioned and document references were validated.' }, esr.intakeStatus, 'ready-for-triage', now);
  return refreshWorkItems(next, now);
}

const examMappings: Record<string, { specialty: string; credentials: string[]; examType: string; dbq: string; modality: Modality[]; diagnostics: string[] }> = {
  knee: { specialty: 'Orthopedics', credentials: ['Board certified'], examType: 'Knee and Lower Leg', dbq: 'Knee and Lower Leg Conditions DBQ', modality: ['in-person', 'tele-c-and-p'], diagnostics: ['Imaging report review'] },
  hearing: { specialty: 'Audiology', credentials: ['AuD', 'State licensed'], examType: 'Hearing Loss and Tinnitus', dbq: 'Hearing Loss and Tinnitus DBQ', modality: ['in-person', 'tele-c-and-p'], diagnostics: ['Audiology test'] },
  back: { specialty: 'Orthopedics', credentials: ['Board certified'], examType: 'Back (Thoracolumbar Spine)', dbq: 'Back Conditions DBQ', modality: ['in-person', 'tele-c-and-p'], diagnostics: ['Imaging report review'] },
  mental: { specialty: 'Psychology', credentials: ['Licensed psychologist'], examType: 'Mental Disorders', dbq: 'Mental Disorders DBQ', modality: ['in-person', 'tele-c-and-p', 'ace-records-review'], diagnostics: [] },
  migraine: { specialty: 'Neurology', credentials: ['Board certified'], examType: 'Headaches', dbq: 'Headaches DBQ', modality: ['in-person', 'tele-c-and-p', 'ace-records-review'], diagnostics: ['Diagnostic study review'] },
  skin: { specialty: 'Dermatology', credentials: ['Board certified'], examType: 'Skin Diseases', dbq: 'Skin Diseases DBQ', modality: ['in-person', 'tele-c-and-p'], diagnostics: [] },
  nerve: { specialty: 'Neurology', credentials: ['Board certified'], examType: 'Peripheral Nerves', dbq: 'Peripheral Nerves DBQ', modality: ['in-person', 'tele-c-and-p', 'ace-records-review'], diagnostics: ['Diagnostic study review'] },
  shoulder: { specialty: 'Orthopedics', credentials: ['Board certified'], examType: 'Shoulder and Arm', dbq: 'Shoulder and Arm Conditions DBQ', modality: ['in-person', 'tele-c-and-p'], diagnostics: ['Imaging report review'] },
};

export function getTriageMapping(title: string) {
  const normalized = title.toLowerCase();
  const key = Object.keys(examMappings).find((item) => normalized.includes(item));
  return examMappings[key ?? 'knee'];
}

export function reviewContention(state: WorkspaceState, contentionId: EntityId, rationale: string, disposition: 'approved-for-exam' | 'clarification-required', dbqTypes: string[] = [], modality?: Modality, diagnostics: string[] = []) {
  const contention = state.contentionsById[contentionId];
  if (!contention) throw new WorkflowRuleError('The selected contention is no longer available.');
  if (state.esrsById[contention.esrId]?.intakeStatus !== 'ready-for-triage' && state.esrsById[contention.esrId]?.intakeStatus !== 'completed') throw new WorkflowRuleError('Validate Intake for this ESR before reviewing contentions.');
  if (!rationale.trim()) throw new WorkflowRuleError('Add reviewer rationale before recording the decision.');
  const mapping = getTriageMapping(contention.title);
  if (disposition === 'approved-for-exam' && !dbqTypes.length) throw new WorkflowRuleError('Select at least one examination DBQ before approval.');
  if (modality && !mapping.modality.includes(modality)) throw new WorkflowRuleError('That modality is not allowed for the selected examination type.');
  const now = nowIso();
  const selectedDbqs = dbqTypes.length ? dbqTypes : [mapping.dbq];
  const keys: (keyof WorkspaceState)[] = ['contentionsById', 'examinationRequirementsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  const updatedContention = update(contention, { status: disposition, reviewerRationale: rationale.trim(), clarificationReason: disposition === 'clarification-required' ? rationale.trim() : undefined, simulatedDisposition: disposition === 'approved-for-exam' ? 'pending' : contention.simulatedDisposition }, now);
  if (disposition === 'approved-for-exam') {
    const existing = [...contention.examinationRequirementIds].reverse().map((id) => next.examinationRequirementsById[id]).find((item) => item && ['awaiting-review', 'clarification-required', 'approved'].includes(item.status));
    if (existing) {
      next.examinationRequirementsById[existing.id] = update(existing, { status: 'approved', reviewerRationale: rationale.trim(), selectedModality: modality, requiredSpecialties: [mapping.specialty], requiredCredentials: mapping.credentials, requestedDbqTypes: selectedDbqs, requestedDiagnostics: diagnostics.length ? diagnostics : mapping.diagnostics }, now);
    } else {
      const requirementId = `req-${contention.id}-${Date.now()}`;
      const requirement: ExaminationRequirement = {
        id: requirementId, version: 1, createdAt: now, updatedAt: now, esrId: contention.esrId, contentionIds: [contentionId],
        title: `${mapping.examType} examination`, examinationType: mapping.examType, requiredSpecialties: [mapping.specialty], requiredCredentials: mapping.credentials, permittedModalities: mapping.modality,
        selectedModality: modality, requestedDbqTypes: selectedDbqs, requestedDiagnostics: diagnostics.length ? diagnostics : mapping.diagnostics, dbqIds: [], diagnosticOrderIds: [], status: 'approved', reviewerRationale: rationale.trim(),
      };
      next.examinationRequirementsById[requirementId] = requirement;
      updatedContention.examinationRequirementIds.push(requirementId);
    }
  }
  next.contentionsById[contentionId] = updatedContention;
  changedStatus(next, { esrId: contention.esrId, entityId: contentionId, entityType: 'contention', action: disposition === 'approved-for-exam' ? 'triage-contention-approved' : 'triage-contention-clarified', rationale }, contention.status, disposition, now);
  return refreshWorkItems(next, now);
}

export type AppointmentSlot = ExaminerSlot;
type ExaminerSlot = WorkspaceState['examinersById'][string]['availableSlots'][number];

function isExaminerEligible(state: WorkspaceState, exam: Examination, examinerId: EntityId, modality: Modality, selected?: Pick<AppointmentSlot, 'startsAt' | 'endsAt'> & { facilityId?: string }, excludeAppointmentId?: string) {
  const examiner = state.examinersById[examinerId];
  const esr = state.esrsById[exam.esrId];
  const veteran = esr ? state.veteransById[esr.veteranId] : undefined;
  const requirements = exam.requirementIds.map(id => state.examinationRequirementsById[id]).filter(Boolean);
  if (!esr || esr.workflowClosedAt || (exam.contentionIds.length > 0 && exam.contentionIds.every(id => state.contentionsById[id]?.simulatedDisposition === 'cancelled'))) return 'The examination is no longer open.';
  if (!examiner || examiner.status !== 'active' || !examiner.eligible) return 'Selected examiner is not eligible for assignments.';
  if (Boolean(examiner.practiceOnly) !== Boolean(esr.practiceScenario)) return 'Choose an examiner assigned to this type of case.';
  if (examiner.training.some(item => item.status !== 'current' || (item.expiresAt && Date.parse(item.expiresAt) <= Date.parse(selected?.startsAt ?? nowIso())))) return 'Selected examiner has incomplete or expired required training.';
  if (!examiner.permittedModalities.includes(modality) || requirements.some(item => !item.permittedModalities.includes(modality))) return 'Selected examiner or examination does not permit this modality.';
  if (requirements.some(item => item.requiredSpecialties.some(value => !examiner.specialties.includes(value)))) return 'Selected examiner does not have every required specialty.';
  if (requirements.some(item => item.requiredCredentials.some(value => !examiner.credentials.includes(value)))) return 'Selected examiner does not hold every required credential.';
  if (requirements.some(item => !examiner.supportedExamTypes.includes(item.examinationType))) return 'Selected examiner is not approved for this examination type.';
  if (veteran?.serviceGroup && !examiner.serviceGroups.includes(veteran.serviceGroup)) return 'Selected examiner is not approved for the claimant’s service group.';
  if (veteran?.examinerPreference && examiner.sex !== veteran.examinerPreference) return 'Selected examiner does not meet the recorded claimant preference.';
  if (!selected) return undefined;

  const { startsAt, endsAt, facilityId } = selected;
  const slot = examiner.availableSlots.find(item => item.startsAt === startsAt && item.endsAt === endsAt && item.modality === modality && (!facilityId || item.facilityId === facilityId));
  if (!slot || !examiner.facilityIds.includes(slot.facilityId)) return 'The selected time is not an available slot at this facility for this examiner and modality.';
  if (!(Date.parse(startsAt) > Date.now() && Date.parse(endsAt) > Date.parse(startsAt))) return 'Select a future appointment slot with a valid duration.';
  const facility = state.facilitiesById[slot.facilityId];
  if (facility?.status !== 'active' || (veteran?.serviceGroup && !facility.serviceGroups.includes(veteran.serviceGroup))) return 'The selected facility is unavailable for the claimant’s service group.';
  if (modality === 'in-person' && veteran?.accessibilityNeeds.some(need => !facility.accessibilityFeatures.includes(need))) return 'The selected facility does not meet the claimant’s recorded accessibility requirements.';
  const distance = modality === 'in-person' ? getTravelDistanceMiles(state, exam.esrId, slot.facilityId) : 0;
  if (distance === undefined) return 'A local travel distance could not be established for this facility.';
  const hardLimit = state.schedulingPolicy?.maxTravelDistanceMiles ?? 500;
  if (distance > hardLimit) return `Selected facility is ${distance} miles away, beyond the ${hardLimit}-mile local demo limit.`;

  const conflicts = Object.values(state.appointmentsById).filter(item => item.id !== excludeAppointmentId && ['held', 'confirmed-locally', 'reschedule-requested'].includes(item.status));
  const overlaps = (item: Appointment) => Date.parse(item.startsAt) < Date.parse(endsAt) && Date.parse(item.endsAt) > Date.parse(startsAt);
  if (conflicts.some(item => item.examinerId === examinerId && overlaps(item))) return 'This examiner already has an appointment in that time slot.';
  const sameClaimant = conflicts.filter(item => state.esrsById[item.esrId]?.veteranId === esr.veteranId);
  if (sameClaimant.some(overlaps)) return 'The claimant already has an appointment in that time slot.';
  if (modality === 'in-person' && sameClaimant.some(item => {
    if (item.modality !== 'in-person' || item.facilityId === slot.facilityId) return false;
    const other = state.facilitiesById[item.facilityId];
    if (!other) return false;
    const gap = Math.max(Date.parse(startsAt) - Date.parse(item.endsAt), Date.parse(item.startsAt) - Date.parse(endsAt)) / 60000;
    return gap < 15 + Math.ceil(distanceMiles(other.latitude, other.longitude, facility.latitude, facility.longitude) * 2);
  })) return 'The claimant needs more travel time between the two appointment locations.';
  return undefined;
}

export function examinerEligibility(state: WorkspaceState, requirementOrExamId: EntityId, examinerId: EntityId, modality: Modality, slot?: Pick<AppointmentSlot, 'startsAt' | 'endsAt'> & { facilityId?: string }, excludeAppointmentId?: string) {
  const existing = state.examinationsById[requirementOrExamId] ?? Object.values(state.examinationsById).find(item => item.requirementIds.includes(requirementOrExamId));
  const requirement = state.examinationRequirementsById[requirementOrExamId] ?? existing?.requirementIds.map(id => state.examinationRequirementsById[id]).find(Boolean);
  if (!requirement) return 'Examination requirement is unavailable.';
  const exam: Examination = existing ?? { id: `eligibility-${requirement.id}`, version: 1, createdAt: requirement.createdAt, updatedAt: requirement.updatedAt, esrId: requirement.esrId, requirementIds: [requirement.id], contentionIds: requirement.contentionIds, requiredDbqIds: [], appointmentIds: [], diagnosticOrderIds: [], status: 'awaiting-scheduling' };
  return isExaminerEligible(state, exam, examinerId, modality, slot, excludeAppointmentId);
}

export function appointmentOutreach(state: WorkspaceState, appointment: Appointment) {
  return Object.values(state.outreachAttemptsById).filter(item => item.appointmentId === appointment.id &&
    Date.parse(item.attemptedAt) >= Date.parse(appointment.outreachWindowOpenedAt ?? appointment.createdAt))
    .sort((a, b) => Date.parse(a.attemptedAt) - Date.parse(b.attemptedAt));
}

export function appointmentTravel(state: WorkspaceState, esrId: string, facilityId: string, modality: Modality, requireSpecialist: boolean) {
  return {
    travelDistanceMiles: modality === 'in-person' ? getTravelDistanceMiles(state, esrId, facilityId) ?? 0 : 0,
    travelLimitMiles: modality === 'in-person' ? (requireSpecialist ? 100 : 50) : 0,
  };
}

export function recordOutreach(state: WorkspaceState, esrId: EntityId, appointmentId: EntityId, channel: Communication['channel'], outcome: Communication['outcome'], summary: string, details?: { attemptedAt?: string; voicemail?: 'left' | 'unavailable' }) {
  const esr = state.esrsById[esrId];
  const appointment = state.appointmentsById[appointmentId];
  if (!esr || esr.workflowClosedAt || !appointment || appointment.esrId !== esrId || appointment.status !== 'held') throw new WorkflowRuleError('Select an active held appointment for this ESR.');
  if (!['phone', 'sms', 'email'].includes(channel) || !['answered', 'delivered', 'no-response', 'returned'].includes(outcome)) throw new WorkflowRuleError('Select a supported contact channel and outcome.');
  if (!summary.trim() || summary.length > 3000) throw new WorkflowRuleError('Add a brief note about the contact attempt.');
  const veteran = state.veteransById[esr.veteranId];
  if ((channel === 'sms' || channel === 'email') && !veteran?.communicationOptIns?.includes(channel)) throw new WorkflowRuleError('This claimant has no recorded opt-in for that channel.');
  if (channel === 'phone' && outcome === 'no-response' && !['left', 'unavailable'].includes(details?.voicemail ?? '')) throw new WorkflowRuleError('Record whether a voicemail was left or unavailable.');
  const now = nowIso();
  const attempted = Date.parse(details?.attemptedAt ?? now);
  if (!Number.isFinite(attempted) || attempted > Date.parse(now) || attempted < Date.parse(appointment.outreachWindowOpenedAt ?? appointment.createdAt)) throw new WorkflowRuleError('Contact time must fall between the start of this hold and now.');
  const attemptedAt = new Date(attempted).toISOString();
  const attempts = appointmentOutreach(state, appointment);
  const sameDay = attempts.filter(item => item.attemptedAt.slice(0, 10) === attemptedAt.slice(0, 10));
  if (attempts.length >= 3) throw new WorkflowRuleError('Three contact attempts are already recorded. Review reserve eligibility or escalate the appointment.');
  if (sameDay.length >= 2 || sameDay.some(item => item.channel === channel || Math.abs(Date.parse(item.attemptedAt) - attempted) < 60000)) throw new WorkflowRuleError('Use at most two attempts per UTC day, at distinct times and using different channels.');
  const next = copyMaps(state, ['communicationsById', 'outreachAttemptsById', 'activitiesById']);
  const communicationId = `comm-${esrId}-${Date.now()}-${++activitySequence}`;
  const attemptId = `outreach-${esrId}-${Date.now()}-${++activitySequence}`;
  next.communicationsById[communicationId] = { id: communicationId, version: 1, createdAt: now, updatedAt: now, esrId, appointmentId, channel, direction: 'outbound', recipient: 'claimant', summary: summary.trim(), outcome, actorId: 'user-scheduler' };
  next.outreachAttemptsById[attemptId] = { id: attemptId, version: 1, createdAt: now, updatedAt: now, esrId, appointmentId, attemptNumber: attempts.length + 1, channel, attemptedAt, outcome, note: summary.trim(), voicemail: details?.voicemail, actorId: 'user-scheduler' };
  addActivity(next, { esrId, entityId: appointmentId, entityType: 'appointment', action: 'outreach-recorded', rationale: summary.trim() }, now);
  return refreshWorkItems(next, now);
}

export function appointmentConfirmation(state: WorkspaceState, appointment: Appointment) {
  const veteran = state.veteransById[state.esrsById[appointment.esrId]?.veteranId];
  const attempts = appointmentOutreach(state, appointment);
  const contact = attempts.some(item => item.outcome === 'answered');
  const reserve = !contact && attempts.length >= 3 && veteran?.reserveSchedulingAllowed;
  const logistics = appointment.localLogistics;
  const reasons = [
    ...(!contact && !reserve ? ['Record answered claimant contact or complete three attempts for an authorized reserve appointment.'] : []),
    ...(!logistics?.recordedAt ? ['Record travel, transport, consent, and preapproval checks.'] : []),
  ];
  return { attempts, reasons, method: reserve ? 'reserve' as const : 'contact-confirmed' as const };
}

export function holdAppointment(state: WorkspaceState, requirementOrExamId: EntityId, examinerId: EntityId, slot: { startsAt: string; endsAt: string; facilityId: string; modality: Modality }) {
  const requirement = state.examinationRequirementsById[requirementOrExamId];
  const existingExam = state.examinationsById[requirementOrExamId] ?? Object.values(state.examinationsById).find((item) => item.requirementIds.includes(requirementOrExamId));
  const requirementIds = existingExam?.requirementIds ?? (requirement ? [requirement.id] : []);
  if (!requirementIds.length) throw new WorkflowRuleError('The selected examination requirement is no longer available.');
  const requirementRecords = requirementIds.map((id) => state.examinationRequirementsById[id]).filter(Boolean);
  if (requirementRecords.some((item) => item.status !== 'approved')) throw new WorkflowRuleError('Approve the examination plan before scheduling.');
  const esrId = existingExam?.esrId ?? requirementRecords[0].esrId;
  const now = nowIso();
  const examinationId = existingExam?.id ?? `exam-${requirementIds[0]}-${Date.now()}`;
  const dbqSpecs = requirementRecords.flatMap(item => (item.requestedDbqTypes ?? []).map(formType => ({ formType, requirement: item })));
  const requiredDbqIds = existingExam?.requiredDbqIds ?? dbqSpecs.map((_, index) => `dbq-${examinationId}-${index + 1}`);
  const diagnosticSpecs = requirementRecords.flatMap(item => (item.requestedDiagnostics ?? []).map(orderType => ({ orderType, requirementId: item.id })));
  const diagnosticOrderIds = existingExam?.diagnosticOrderIds ?? diagnosticSpecs.map((_, index) => `diagnostic-${examinationId}-${index + 1}`);
  const examination: Examination = existingExam ?? {
    id: examinationId, version: 1, createdAt: now, updatedAt: now, esrId, requirementIds, contentionIds: requirementRecords.flatMap((item) => item.contentionIds),
    requiredDbqIds, appointmentIds: [], diagnosticOrderIds, status: 'awaiting-scheduling',
  };
  if (typeof slot?.facilityId !== 'string' || !slot.facilityId) throw new WorkflowRuleError('Select the exact available slot and facility.');
  const reason = isExaminerEligible(state, examination, examinerId, slot.modality, slot);
  if (reason) throw new WorkflowRuleError(reason);
  if (examination.appointmentIds.some(id => ['held', 'confirmed-locally', 'reschedule-requested', 'completed'].includes(state.appointmentsById[id]?.status))) throw new WorkflowRuleError('This examination already has an active appointment.');
  const veteran = state.veteransById[state.esrsById[esrId].veteranId];
  const travel = appointmentTravel(state, esrId, slot.facilityId, slot.modality, requirementRecords.some(item => item.requiredSpecialties.length > 0));
  const appointmentId = `appt-${examinationId}-${Date.now()}`;
  const appointment: Appointment = {
    id: appointmentId, version: 1, createdAt: now, updatedAt: now, esrId, examinationId, examinerId, facilityId: slot.facilityId,
    startsAt: slot.startsAt, endsAt: slot.endsAt, modality: slot.modality, status: 'held', accessibilityNeeds: veteran?.accessibilityNeeds ?? [],
    history: [{ status: 'held', at: now, actorId: 'user-scheduler', note: 'Time slot recorded; claimant confirmation is pending.' }],
    outreachWindowOpenedAt: now,
    localLogistics: { ...travel, specialTransportRequired: false, specialTransportStatus: 'pending', consentStatus: 'pending', vaPreapprovalStatus: 'pending', note: '' },
  };
  const keys: (keyof WorkspaceState)[] = ['examinationsById', 'appointmentsById', 'dbqsById', 'dbqVersionsById', 'diagnosticOrdersById', 'examinationRequirementsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  next.examinationsById[examinationId] = update(examination, { examinerId, status: 'scheduled', assignedAt: now, appointmentIds: [...examination.appointmentIds, appointmentId], requiredDbqIds, diagnosticOrderIds }, now);
  next.appointmentsById[appointmentId] = appointment;
  for (const item of requirementRecords) {
    const attached = existingExam ? item.dbqIds : dbqSpecs.flatMap((spec, index) => spec.requirement.id === item.id ? [requiredDbqIds[index]] : []);
    const ordered = existingExam ? item.diagnosticOrderIds : diagnosticSpecs.flatMap((spec, index) => spec.requirementId === item.id ? [diagnosticOrderIds[index]] : []);
    next.examinationRequirementsById[item.id] = update(item, { selectedModality: slot.modality, dbqIds: attached, diagnosticOrderIds: ordered }, now);
  }
  if (!existingExam) {
    dbqSpecs.forEach(({formType, requirement}, index) => {
      const id = requiredDbqIds[index];
      const versionId = `${id}-v1`;
      const dbq: DBQ = { id, version: 1, createdAt: now, updatedAt: now, examinationId, esrId, contentionIds: requirement.contentionIds, formType, formVersion: 'synthetic-v2', catalogReference: requirement.dbqCatalogReferences?.[formType], values: {}, versionIds: [versionId], currentVersionId: versionId, status: 'draft', validationErrors: [], localReviewState: 'not-ready' };
      next.dbqsById[id] = dbq;
      next.dbqVersionsById[versionId] = { id: versionId, version: 1, createdAt: now, updatedAt: now, dbqId: id, versionNumber: 1, formVersion: dbq.formVersion, values: {}, authoredBy: examinerId, changeSummary: 'Initial clinical draft.', createdFor: 'draft' };
    });
    diagnosticSpecs.forEach(({orderType, requirementId}, index) => {
      const id = diagnosticOrderIds[index];
      next.diagnosticOrdersById[id] = { id, version: 1, createdAt: now, updatedAt: now, esrId, examinationId, requirementId, orderType, status: 'ordered', required: true, resultIds: [] };
    });
  }
  addActivity(next, { esrId, entityId: examinationId, entityType: 'examination', action: 'examiner-assigned', rationale: `${state.examinersById[examinerId].displayName} assigned; appointment slot recorded.` }, now);
  return { state: refreshWorkItems(next, now), appointmentId };
}

export function confirmAppointment(state: WorkspaceState, appointmentId: EntityId, confirmationNote = 'Claimant contact attempt recorded; appointment confirmation saved.') {
  const appointment = state.appointmentsById[appointmentId];
  if (!appointment) throw new WorkflowRuleError('The selected appointment is no longer available.');
  if (!['held', 'reschedule-requested'].includes(appointment.status)) throw new WorkflowRuleError('Only a held appointment can be confirmed.');
  const { reasons, method } = appointmentConfirmation(state, appointment);
  if (reasons[0]) throw new WorkflowRuleError(reasons[0]);
  const logistics = appointment.localLogistics!;
  if (logistics.travelDistanceMiles > logistics.travelLimitMiles && logistics.consentStatus !== 'simulated-recorded') throw new WorkflowRuleError('Record express claimant consent before confirming travel beyond the applicable mileage threshold.');
  const exam = state.examinationsById[appointment.examinationId];
  const reason = exam && isExaminerEligible(state, exam, appointment.examinerId, appointment.modality, appointment, appointment.id);
  if (!exam || reason) throw new WorkflowRuleError(reason ?? 'The examination is no longer available.');
  const now = nowIso();
  const next = copyMaps(state, ['appointmentsById', 'activitiesById', 'communicationsById']);
  const veteran = state.veteransById[state.esrsById[appointment.esrId].veteranId];
  const reference = `LOCAL-NOTICE-${appointment.id}-v${appointment.version + 1}`;
  const notice = { recordedAt: now, reference, representativeReference: veteran?.representativeReference };
  const summary = `Local notice ${reference} · ${appointment.startsAt} · ${state.facilitiesById[appointment.facilityId]?.name} · ${state.examinersById[appointment.examinerId]?.displayName}`;
  const recipients = veteran?.representativeReference ? ['claimant', 'representative'] as const : ['claimant'] as const;
  for (const recipient of recipients) {
    const id = `${reference}-${recipient}`;
    next.communicationsById[id] = { id, version: 1, createdAt: now, updatedAt: now, esrId: appointment.esrId, appointmentId, channel: 'mail', direction: 'outbound', recipient, summary, outcome: 'pending', actorId: 'user-scheduler' };
  }
  next.appointmentsById[appointmentId] = update(appointment, { status: 'confirmed-locally', schedulingMethod: method, localNotification: notice, confirmationNote: confirmationNote.trim(), history: [...appointment.history, { status: 'confirmed-locally', at: now, actorId: 'user-scheduler', note: confirmationNote.trim() }] }, now);
  changedStatus(next, { esrId: appointment.esrId, entityId: appointmentId, entityType: 'appointment', action: 'appointment-confirmed-locally', rationale: confirmationNote }, appointment.status, 'confirmed-locally', now);
  return refreshWorkItems(next, now);
}

export function setSchedulingDistanceLimit(state: WorkspaceState, miles: number) {
  if (!Number.isFinite(miles) || miles < 10 || miles > 2000) throw new WorkflowRuleError('Set a demo travel limit from 10 to 2,000 miles.');
  const now = nowIso();
  const next = { ...state, schedulingPolicy: { maxTravelDistanceMiles: Math.round(miles) }, activitiesById: { ...state.activitiesById } };
  addActivity(next, { esrId: Object.keys(state.esrsById)[0] ?? 'workspace', entityId: 'scheduling-policy', entityType: 'esr', action: 'scheduling-policy-updated', rationale: `Local demo travel limit set to ${Math.round(miles)} miles.` }, now);
  return refreshWorkItems(next, now);
}

export function recordAppointmentLogistics(state: WorkspaceState, appointmentId: EntityId, input: Omit<AppointmentLogistics, 'travelDistanceMiles' | 'travelLimitMiles' | 'recordedAt'>) {
  const appointment = state.appointmentsById[appointmentId];
  if (!appointment) throw new WorkflowRuleError('The selected appointment is no longer available.');
  if (!['held', 'reschedule-requested'].includes(appointment.status)) throw new WorkflowRuleError('Local logistics can only be recorded for a held appointment.');
  if (!input.note.trim()) throw new WorkflowRuleError('Add a note for the local scheduling checks.');
  if (!['simulated-recorded', 'not-required'].includes(input.consentStatus) || !['simulated-recorded', 'not-required'].includes(input.vaPreapprovalStatus) ||
    typeof input.specialTransportRequired !== 'boolean' || input.specialTransportStatus !== (input.specialTransportRequired ? 'simulated-arranged' : 'not-required')) throw new WorkflowRuleError('Complete each logistics disposition before saving.');
  const exam = state.examinationsById[appointment.examinationId];
  const travel = appointmentTravel(state, appointment.esrId, appointment.facilityId, appointment.modality, exam?.requirementIds.some(id => state.examinationRequirementsById[id]?.requiredSpecialties.length > 0));
  if (travel.travelDistanceMiles > travel.travelLimitMiles && input.consentStatus !== 'simulated-recorded') throw new WorkflowRuleError('Record express claimant consent for travel beyond the applicable mileage threshold.');
  const now = nowIso();
  const localLogistics: AppointmentLogistics = { specialTransportRequired: input.specialTransportRequired, specialTransportStatus: input.specialTransportStatus, consentStatus: input.consentStatus, vaPreapprovalStatus: input.vaPreapprovalStatus, note: input.note.trim(), ...travel, recordedAt: now };
  const next = copyMaps(state, ['appointmentsById', 'activitiesById']);
  next.appointmentsById[appointmentId] = update(appointment, { localLogistics }, now);
  addActivity(next, { esrId: appointment.esrId, entityId: appointmentId, entityType: 'appointment', action: 'appointment-logistics-recorded', rationale: `${localLogistics.note} These logistics and authorization values are local simulations.` }, now);
  return refreshWorkItems(next, now);
}

export function rescheduleAppointment(state: WorkspaceState, appointmentId: EntityId, slot: AppointmentSlot, coordinationReference = '') {
  const appointment = state.appointmentsById[appointmentId];
  if (!appointment) throw new WorkflowRuleError('The selected appointment is no longer available.');
  if (!['held', 'confirmed-locally', 'reschedule-requested'].includes(appointment.status)) throw new WorkflowRuleError('Only an active appointment can be rescheduled.');
  const esr = state.esrsById[appointment.esrId];
  if (esr?.schedulingProgram === 'IDES' && !coordinationReference.trim()) throw new WorkflowRuleError('Record the authorized military coordination reference for IDES.');
  if (appointment.history.some(item => item.status === 'reschedule-requested')) throw new WorkflowRuleError('A local rescheduling opportunity is already recorded. Escalate for review.');
  if (typeof slot?.facilityId !== 'string' || !slot.facilityId) throw new WorkflowRuleError('Select the exact available replacement slot and facility.');
  if (appointment.startsAt === slot.startsAt && appointment.facilityId === slot.facilityId && appointment.modality === slot.modality) throw new WorkflowRuleError('Select a different appointment slot.');
  const exam = state.examinationsById[appointment.examinationId];
  const reason = exam && isExaminerEligible(state, exam, appointment.examinerId, slot.modality, slot, appointment.id);
  if (!exam || reason) throw new WorkflowRuleError(reason ?? 'The examination is no longer available.');
  if (exam.requiredDbqIds.some(id => state.dbqsById[id]?.signedVersionId)) throw new WorkflowRuleError('A signed clinical report is already on file for this examination.');
  const travel = appointmentTravel(state, appointment.esrId, slot.facilityId, slot.modality, exam.requirementIds.some(id => state.examinationRequirementsById[id]?.requiredSpecialties.length > 0));
  const now = nowIso();
  const next = copyMaps(state, ['appointmentsById', 'activitiesById']);
  const localLogistics: AppointmentLogistics = { ...travel, specialTransportRequired: false, specialTransportStatus: 'pending', consentStatus: 'pending', vaPreapprovalStatus: 'pending', note: '' };
  next.appointmentsById[appointmentId] = update(appointment, { startsAt: slot.startsAt, endsAt: slot.endsAt, facilityId: slot.facilityId, modality: slot.modality, status: 'held', confirmationNote: undefined, schedulingMethod: undefined, localNotification: undefined, outreachWindowOpenedAt: now, localLogistics, history: [...appointment.history, { status: 'reschedule-requested', at: now, actorId: 'user-scheduler', note: coordinationReference.trim() || 'Replacement slot selected.' }, { status: 'held', at: now, actorId: 'user-scheduler', note: 'Replacement appointment time held pending contact and logistics.' }] }, now);
  changedStatus(next, { esrId: appointment.esrId, entityId: appointmentId, entityType: 'appointment', action: 'appointment-rescheduled', rationale: 'Replacement slot saved locally; logistics and contact checks are pending.' }, appointment.status, 'held', now);
  return refreshWorkItems(next, now);
}

export function cancelAppointment(state: WorkspaceState, appointmentId: EntityId, reason: string) {
  const appointment = state.appointmentsById[appointmentId];
  if (!appointment) throw new WorkflowRuleError('The selected appointment is no longer available.');
  if (!['held', 'confirmed-locally', 'reschedule-requested'].includes(appointment.status) || state.examinationsById[appointment.examinationId]?.requiredDbqIds.some(id => state.dbqsById[id]?.signedVersionId)) throw new WorkflowRuleError('Only an active appointment with no signed clinical report can be cancelled.');
  if (!reason.trim()) throw new WorkflowRuleError('Add a cancellation reason.');
  const now = nowIso();
  const next = copyMaps(state, ['appointmentsById', 'examinationsById', 'activitiesById']);
  next.appointmentsById[appointmentId] = update(appointment, { status: 'cancelled', history: [...appointment.history, { status: 'cancelled', at: now, actorId: 'user-scheduler', note: reason.trim() }] }, now);
  const examination = next.examinationsById[appointment.examinationId];
  if (examination && !examination.appointmentIds.some((id) => id !== appointmentId && ['held', 'confirmed-locally', 'completed'].includes(state.appointmentsById[id]?.status ?? ''))) next.examinationsById[examination.id] = update(examination, { status: 'awaiting-scheduling', examinerId: undefined }, now);
  changedStatus(next, { esrId: appointment.esrId, entityId: appointmentId, entityType: 'appointment', action: 'appointment-cancelled', rationale: reason.trim() }, appointment.status, 'cancelled', now);
  return refreshWorkItems(next, now);
}

export function saveDBQDraft(state: WorkspaceState, dbqId: EntityId, values: FieldValues) {
  const dbq = state.dbqsById[dbqId];
  if (!dbq) throw new WorkflowRuleError('The selected DBQ draft is no longer available.');
  assertClinicalAccess(state, dbq.esrId, dbq.examinationId);
  if (dbq.formVersion === 'synthetic-v2') {
    if (!values || typeof values !== 'object' || Array.isArray(values)) throw new WorkflowRuleError('Enter valid structured form values.');
    const fields = new Set(dbqForm(dbq).map(field => field.key));
    if (Object.entries(values).some(([key, value]) => !fields.has(key) || typeof value !== 'string' || value.length > 8000)) throw new WorkflowRuleError('Use only fields from the assigned form and keep each entry within 8,000 characters.');
  }
  if (['ready-for-qa', 'in-qa', 'approved-for-package'].includes(dbq.status)) throw new WorkflowRuleError('This report is in QA or approved. Request a correction before editing it.');
  const opensCorrectionDraft = dbq.signedVersionId === dbq.currentVersionId;
  const correction = Object.values(state.correctionRequestsById).find((item) => item.dbqId === dbqId && ['open', 'in-progress'].includes(item.status));
  if (opensCorrectionDraft && !correction) throw new WorkflowRuleError('The signed report version is locked. A QA correction request is required before editing.');
  const now = nowIso();
  const keys: (keyof WorkspaceState)[] = ['dbqsById', 'dbqVersionsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  if (opensCorrectionDraft) {
    const versionNumber = dbq.versionIds.map((id) => state.dbqVersionsById[id]?.versionNumber ?? 0).reduce((a, b) => Math.max(a, b), 0) + 1;
    const versionId = `dbqver-${dbqId}-${versionNumber}-${Date.now()}`;
    next.dbqVersionsById[versionId] = { id: versionId, version: 1, createdAt: now, updatedAt: now, dbqId, versionNumber, formVersion: dbq.formVersion, values: { ...values }, authoredBy: 'user-clinician', changeSummary: correction?.requestComment ?? 'Draft opened after signed-version correction.', createdFor: 'correction-resubmission' };
    next.dbqsById[dbqId] = update(dbq, { values: { ...values }, status: 'in-progress', validationErrors: validateDbqValues(dbq, values), versionIds: [...dbq.versionIds, versionId], currentVersionId: versionId, localReviewState: 'not-ready' }, now);
  } else {
    next.dbqsById[dbqId] = update(dbq, { values: { ...values }, status: dbq.status === 'draft' ? 'in-progress' : dbq.status, validationErrors: validateDbqValues(dbq, values) }, now);
    const version = next.dbqVersionsById[dbq.currentVersionId];
    if (version) next.dbqVersionsById[version.id] = update(version, { values: { ...values } }, now);
  }
  addActivity(next, { esrId: dbq.esrId, entityId: dbqId, entityType: 'dbq', action: 'dbq-draft-saved', rationale: 'Structured DBQ values saved in the workspace.' }, now);
  return refreshWorkItems(next, now);
}

export function validateDBQ(state: WorkspaceState, dbqId: EntityId) {
  const dbq = state.dbqsById[dbqId];
  if (!dbq) throw new WorkflowRuleError('The selected DBQ is no longer available.');
  assertClinicalAccess(state, dbq.esrId, dbq.examinationId);
  const errors = validateDbqValues(dbq);
  const now = nowIso();
  const next = copyMaps(state, ['dbqsById', 'activitiesById']);
  next.dbqsById[dbqId] = update(dbq, { validationErrors: errors }, now);
  addActivity(next, { esrId: dbq.esrId, entityId: dbqId, entityType: 'dbq', action: 'dbq-validated', rationale: errors.length ? errors.join(' ') : 'Required fields are present; clinician review remains a separate action.' }, now);
  return { state: refreshWorkItems(next, now), errors };
}

export function reviewDiagnostic(state: WorkspaceState, orderId: EntityId) {
  const order = state.diagnosticOrdersById[orderId];
  if (!order) throw new WorkflowRuleError('The selected diagnostic order is no longer available.');
  assertClinicalAccess(state, order.esrId, order.examinationId);
  if (!order.resultIds.length && order.required) throw new WorkflowRuleError('A required diagnostic result is not available to review.');
  const now = nowIso();
  const next = copyMaps(state, ['diagnosticOrdersById', 'diagnosticResultsById', 'activitiesById']);
  next.diagnosticOrdersById[orderId] = update(order, { status: 'reviewed' }, now);
  order.resultIds.forEach((id) => { const result = next.diagnosticResultsById[id]; if (result) next.diagnosticResultsById[id] = update(result, { reviewedBy: 'user-clinician', reviewedAt: now }, now); });
  addActivity(next, { esrId: order.esrId, entityId: orderId, entityType: 'diagnostic-order', action: 'diagnostic-reviewed', rationale: 'Diagnostic result reviewed by the clinician.' }, now);
  return refreshWorkItems(next, now);
}

export function recordDiagnosticResult(state: WorkspaceState, orderId: EntityId, summary: string, sourceReference: string, reportedFlag?: WorkspaceState['diagnosticResultsById'][string]['reportedFlag']) {
  const order = state.diagnosticOrdersById[orderId];
  if (!order) throw new WorkflowRuleError('The selected diagnostic order is no longer available.');
  const exam = assertClinicalAccess(state, order.esrId, order.examinationId);
  if (exam.requiredDbqIds.some(id => state.dbqsById[id]?.signedVersionId && state.dbqsById[id]?.signedVersionId === state.dbqsById[id]?.currentVersionId)) throw new WorkflowRuleError('A signed report already references these diagnostics.');
  if (!summary.trim() || !sourceReference.trim() || summary.length > 8000 || sourceReference.length > 300) throw new WorkflowRuleError('Enter a result summary and source reference.');
  if (reportedFlag !== undefined && reportedFlag !== 'abnormal' && reportedFlag !== 'critical') throw new WorkflowRuleError('Select a supported diagnostic source designation.');
  const now = nowIso();
  const next = copyMaps(state, ['diagnosticOrdersById', 'diagnosticResultsById', 'activitiesById']);
  const id = `diagnostic-result-${order.id}-${Date.now()}-${++activitySequence}`;
  next.diagnosticResultsById[id] = { id, version: 1, createdAt: now, updatedAt: now, esrId: order.esrId, orderId, examinationId: order.examinationId, summary: summary.trim(), sourceReference: sourceReference.trim(), reportedFlag };
  next.diagnosticOrdersById[order.id] = update(order, { status: 'result-available', exception: undefined, resultIds: [...order.resultIds, id] }, now);
  addActivity(next, { esrId: order.esrId, entityId: order.id, entityType: 'diagnostic-order', action: 'diagnostic-result-recorded', rationale: `Diagnostic result reference recorded: ${sourceReference.trim()}` }, now);
  return refreshWorkItems(next, now);
}

export function completeClinicalReview(state: WorkspaceState, dbqId: EntityId, attestation?: { versionId: string; confirmed: boolean; draftRevision: number }, actorId = 'user-clinician') {
  const dbq = state.dbqsById[dbqId];
  if (!dbq) throw new WorkflowRuleError('The selected DBQ is no longer available.');
  if (!['draft', 'in-progress', 'awaiting-clinician-review'].includes(dbq.status)) throw new WorkflowRuleError('This DBQ is not in an editable clinical review state.');
  if (dbq.signedVersionId === dbq.currentVersionId) throw new WorkflowRuleError('This report version already has a local clinician sign-off and is locked.');
  const errors = validateDbqValues(dbq);
  if (errors.length) throw new WorkflowRuleError(errors.join(' '));
  const exam = assertClinicalAccess(state, dbq.esrId, dbq.examinationId);
  const savedVersion = state.dbqVersionsById[dbq.currentVersionId];
  if (!savedVersion || savedVersion.dbqId !== dbq.id || attestation?.confirmed !== true ||
    attestation.versionId !== savedVersion.id || attestation.draftRevision !== savedVersion.version) {
    throw new WorkflowRuleError('Attest to the exact persisted report version before sending it to QA.');
  }
  if (dbq.formVersion === 'synthetic-v2' && !exam.appointmentIds.some(id => state.appointmentsById[id]?.status === 'completed')) throw new WorkflowRuleError('Record the completed encounter first.');
  const examiner = exam.examinerId ? state.examinersById[exam.examinerId] : undefined;
  if (!examiner) throw new WorkflowRuleError('An assigned examiner is required for local sign-off.');
  const orders = exam.diagnosticOrderIds.map(id => state.diagnosticOrdersById[id]);
  if (orders.some(item => !item || !diagnosticReady(item))) throw new WorkflowRuleError('Review each required diagnostic result or document the applicable exception.');
  const now = nowIso();
  const keys: (keyof WorkspaceState)[] = ['dbqsById', 'examinationsById', 'qaReviewsById', 'qaFindingsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  next.dbqsById[dbqId] = update(dbq, { status: 'ready-for-qa', validationErrors: [], localReviewState: 'review-recorded', reviewerId: actorId, reviewedAt: now, signedVersionId: dbq.currentVersionId,
    localAttestation: { versionId: dbq.currentVersionId, staffActorId: actorId, examinerId: examiner.id, examinerName: examiner.displayName, credentials: examiner.credentials, specialty: examiner.specialties, at: now } }, now);
  if (exam && !Object.values(state.dbqsById).some((item) => item.examinationId === exam.id && item.id !== dbqId && !['ready-for-qa','in-qa','approved-for-package'].includes(item.status))) next.examinationsById[exam.id] = update(exam, { status: 'ready-for-qa' }, now);
  const existing = Object.values(state.qaReviewsById).find((review) => review.dbqId === dbqId);
  if (existing) next.qaReviewsById[existing.id] = update(existing, { versionId: dbq.currentVersionId, status: 'queued', reviewerComment: 'Ready for independent QA review.', checks: [] }, now);
  else {
    const reviewId = `qa-${dbqId}-${Date.now()}`;
    next.qaReviewsById[reviewId] = { id: reviewId, version: 1, createdAt: now, updatedAt: now, esrId: dbq.esrId, dbqId, versionId: dbq.currentVersionId, reviewerId: 'user-qa', findingIds: [], status: 'queued', reviewerComment: '' };
  }
  addActivity(next, { esrId: dbq.esrId, entityId: dbqId, entityType: 'dbq', action: 'clinical-review-completed', rationale: 'Clinician review recorded. This is not an electronic signature.' }, now);
  return refreshWorkItems(next, now);
}

export const qaFindingCategories = ['missing-required-field', 'incomplete-rationale', 'form-version', 'signature-readiness', 'evidence-consistency', 'attachment'] as const;
export const qaFindingSeverities = ['blocking', 'review', 'advisory'] as const;

function requireQAReview(state: WorkspaceState, review: QAReview | undefined) {
  if (!review || state.esrsById[review.esrId]?.workflowClosedAt || !['queued','in-review','returned'].includes(review.status)) throw new WorkflowRuleError('Select an active QA review.');
  const dbq = state.dbqsById[review.dbqId];
  if (!dbq || dbq.contentionIds.every(id => state.contentionsById[id]?.simulatedDisposition === 'cancelled') || dbq.signedVersionId !== review.versionId || dbq.currentVersionId !== review.versionId) throw new WorkflowRuleError('This QA review requires the current signed report version.');
  return dbq;
}

export function qaApprovalBlockers(state: WorkspaceState, review: QAReview) {
  const dbq = state.dbqsById[review.dbqId];
  const checks = review.checks ?? [];
  const active = review.findingIds.map(id => state.qaFindingsById[id]).filter(item => ['open','correction-requested'].includes(item?.status));
  return [
    ...(dbq?.signedVersionId !== review.versionId || dbq.currentVersionId !== review.versionId || dbq.localReviewState !== 'review-recorded' ? ['A recorded sign-off is required for the current report revision.'] : []),
    ...(checks.length !== 5 || checks.some(item => item.versionId !== review.versionId) ? ['Run the deterministic checks for the current signed version.'] : []),
    ...(checks.some(item => item.severity === 'blocking' && item.status !== 'passed') ? ['Resolve failed deterministic QA checks before approval.'] : []),
    ...(active.some(item => item.severity === 'blocking') ? ['Resolve or return every blocking finding.'] : []),
  ];
}

export function requestCorrection(state: WorkspaceState, findingId: EntityId, comment: string) {
  const finding = state.qaFindingsById[findingId];
  const review = finding ? state.qaReviewsById[finding.qaReviewId] : undefined;
  const dbq = finding ? state.dbqsById[finding.dbqId] : undefined;
  if (!finding || !review || !dbq) throw new WorkflowRuleError('The selected QA finding is no longer available.');
  requireQAReview(state, review);
  if (finding.status !== 'open') throw new WorkflowRuleError('The selected finding is already dispositioned.');
  if (!comment.trim() || comment.length > 3000) throw new WorkflowRuleError('Add a correction request for the responsible clinician.');
  const now = nowIso();
  const keys: (keyof WorkspaceState)[] = ['qaFindingsById', 'qaReviewsById', 'dbqsById', 'examinationsById', 'correctionRequestsById', 'resultsPackagesById', 'activitiesById'];
  const next = copyMaps(state, keys);
  const updatedFinding = update(finding, { status: 'correction-requested', comments: [...finding.comments, comment.trim()], resolutionHistory: [...finding.resolutionHistory, { at: now, actorId: 'user-qa', status: 'correction-requested', comment: comment.trim() }] }, now);
  next.qaFindingsById[findingId] = updatedFinding;
  next.qaReviewsById[review.id] = update(review, { status: 'correction-requested', reviewerComment: comment.trim() }, now);
  next.dbqsById[dbq.id] = update(dbq, { status: 'correction-required' }, now);
  const examination = next.examinationsById[dbq.examinationId];
  if (examination) next.examinationsById[examination.id] = update(examination, { status: 'correction-required' }, now);
  refreshPackages(next, dbq.esrId, now);
  const existing = Object.values(state.correctionRequestsById).find((item) => item.dbqId === dbq.id && item.status !== 'closed');
  if (existing) next.correctionRequestsById[existing.id] = update(existing, { status: 'open', findingIds: [...new Set([...existing.findingIds, findingId])], requestComment: comment.trim(), history: [...existing.history, { at: now, status: 'open', note: comment.trim(), actorId: 'user-qa' }] }, now);
  else {
    const id = `correction-${dbq.id}-${Date.now()}`;
    next.correctionRequestsById[id] = { id, version: 1, createdAt: now, updatedAt: now, esrId: dbq.esrId, dbqId: dbq.id, qaReviewId: review.id, findingIds: [findingId], assignedTo: state.examinationsById[dbq.examinationId]?.examinerId ?? 'user-clinician', requestedBy: 'user-qa', status: 'open', requestComment: comment.trim(), history: [{ at: now, status: 'open', note: comment.trim(), actorId: 'user-qa' }] };
  }
  addActivity(next, { esrId: dbq.esrId, entityId: findingId, entityType: 'qa-finding', action: 'correction-requested', rationale: comment.trim() }, now);
  return refreshWorkItems(next, now);
}

export function addQAFinding(state: WorkspaceState, reviewId: EntityId, input: { title: string; sectionReference: string; issueCategory: QAFinding['issueCategory']; severity: QAFinding['severity']; comment: string }) {
  const review = state.qaReviewsById[reviewId];
  if (!review) throw new WorkflowRuleError('The selected QA review is no longer available.');
  requireQAReview(state, review);
  if (!qaFindingCategories.includes(input.issueCategory) || !qaFindingSeverities.includes(input.severity)) throw new WorkflowRuleError('Choose a valid finding category and severity.');
  if (!input.title.trim() || input.title.length > 180 || !input.sectionReference.trim() || input.sectionReference.length > 180 || !input.comment.trim() || input.comment.length > 3000) throw new WorkflowRuleError('Enter a finding title, section, and reviewer comment.');
  const now = nowIso();
  const keys: (keyof WorkspaceState)[] = ['qaReviewsById', 'qaFindingsById', 'resultsPackagesById', 'activitiesById'];
  const next = copyMaps(state, keys);
  const id = `finding-${review.dbqId}-${Date.now()}`;
  next.qaFindingsById[id] = {
    id, version: 1, createdAt: now, updatedAt: now, esrId: review.esrId, qaReviewId: review.id, dbqId: review.dbqId,
    sectionReference: input.sectionReference.trim(), issueCategory: input.issueCategory, severity: input.severity, title: input.title.trim(),
    supportingEvidenceIds: [], reviewerId: 'user-qa', status: 'open', comments: [input.comment.trim()], resolutionHistory: [], origin: 'reviewer', versionId: review.versionId,
  };
  next.qaReviewsById[reviewId] = update(review, { findingIds: [...review.findingIds, id], status: 'in-review', reviewerComment: input.comment.trim() }, now);
  if (input.severity === 'blocking') refreshPackages(next, review.esrId, now);
  addActivity(next, { esrId: review.esrId, entityId: id, entityType: 'qa-finding', action: 'qa-review-opened', rationale: input.comment.trim() }, now);
  return { state: refreshWorkItems(next, now), findingId: id };
}

export function resubmitCorrection(state: WorkspaceState, dbqId: EntityId, values: FieldValues) {
  const dbq = state.dbqsById[dbqId];
  const correction = Object.values(state.correctionRequestsById).find((item) => item.dbqId === dbqId && ['open', 'in-progress'].includes(item.status));
  if (!dbq || !correction) throw new WorkflowRuleError('No open correction task is linked to this DBQ.');
  assertClinicalAccess(state, dbq.esrId, dbq.examinationId);
  const errors = validateDbqValues(dbq, values);
  if (errors.length) throw new WorkflowRuleError(errors.join(' '));
  const now = nowIso();
  const keys: (keyof WorkspaceState)[] = ['dbqsById', 'dbqVersionsById', 'qaReviewsById', 'examinationsById', 'correctionRequestsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  let versionId = dbq.currentVersionId;
  let versionNumber = next.dbqVersionsById[versionId]?.versionNumber ?? 1;
  if (!versionId || dbq.signedVersionId === dbq.currentVersionId) {
    versionNumber = dbq.versionIds.map((id) => state.dbqVersionsById[id]?.versionNumber ?? 0).reduce((a, b) => Math.max(a, b), 0) + 1;
    versionId = `dbqver-${dbqId}-${versionNumber}-${Date.now()}`;
    const version: DBQVersion = { id: versionId, version: 1, createdAt: now, updatedAt: now, dbqId, versionNumber, formVersion: dbq.formVersion, values: { ...values }, authoredBy: 'user-clinician', changeSummary: correction.requestComment, createdFor: 'correction-resubmission' };
    next.dbqVersionsById[versionId] = version;
  } else {
    const draftVersion = next.dbqVersionsById[versionId];
    if (draftVersion) next.dbqVersionsById[versionId] = update(draftVersion, { values: { ...values }, changeSummary: correction.requestComment }, now);
  }
  next.dbqsById[dbqId] = update(dbq, { values: { ...values }, status: 'in-progress', validationErrors: [], versionIds: dbq.versionIds.includes(versionId) ? dbq.versionIds : [...dbq.versionIds, versionId], currentVersionId: versionId, localReviewState: 'not-ready', reviewerId: undefined, reviewedAt: undefined }, now);
  const review = next.qaReviewsById[correction.qaReviewId];
  if (review) next.qaReviewsById[review.id] = update(review, { versionId, status: 'correction-requested', reviewerComment: 'Correction draft is awaiting a new clinician sign-off.', checks: [] }, now);
  next.correctionRequestsById[correction.id] = update(correction, { status: 'resubmitted', history: [...correction.history, { at: now, status: 'resubmitted', note: `Submitted version ${versionNumber}.`, actorId: 'user-clinician' }] }, now);
  const examination = next.examinationsById[dbq.examinationId];
  if (examination) next.examinationsById[examination.id] = update(examination, { status: 'awaiting-clinical-review' }, now);
  addActivity(next, { esrId: dbq.esrId, entityId: dbqId, entityType: 'dbq', action: 'correction-resubmitted', rationale: `Correction draft version ${versionNumber} saved. The clinician must explicitly sign off this version before it returns to QA; earlier versions remain available.` }, now);
  return refreshWorkItems(next, now);
}

export function resolveQAFinding(state: WorkspaceState, findingId: EntityId, comment: string) {
  const finding = state.qaFindingsById[findingId];
  if (!finding) throw new WorkflowRuleError('The selected QA finding is no longer available.');
  const review = state.qaReviewsById[finding.qaReviewId];
  requireQAReview(state, review);
  if (!['open','correction-requested'].includes(finding.status)) throw new WorkflowRuleError('The finding has already been dispositioned.');
  if (finding.origin === 'deterministic' && review.checks?.some(check => check.key === finding.checkKey && check.status === 'failed')) throw new WorkflowRuleError('The current revision still fails this check. Request a correction.');
  if (!comment.trim() || comment.length > 3000) throw new WorkflowRuleError('Add a disposition note.');
  const now = nowIso();
  const next = copyMaps(state, ['qaFindingsById', 'activitiesById']);
  next.qaFindingsById[findingId] = update(finding, { status: 'resolved', comments: [...finding.comments, comment.trim()], resolutionHistory: [...finding.resolutionHistory, { at: now, actorId: 'user-qa', status: 'resolved', comment: comment.trim() }] }, now);
  addActivity(next, { esrId: finding.esrId, entityId: findingId, entityType: 'qa-finding', action: 'qa-finding-resolved', rationale: comment.trim() }, now);
  return refreshWorkItems(next, now);
}

export function runQAChecklist(state: WorkspaceState, reviewId: EntityId) {
  const review = state.qaReviewsById[reviewId];
  const dbq = review ? state.dbqsById[review.dbqId] : undefined;
  const version = review ? state.dbqVersionsById[review.versionId] : undefined;
  if (!review || !dbq || !version || version.dbqId !== dbq.id) throw new WorkflowRuleError('The selected QA report version is no longer available.');
  requireQAReview(state, review);
  const now = nowIso();
  const exam = state.examinationsById[dbq.examinationId];
  const evidenceIds = [...new Set(dbq.contentionIds.flatMap((id) => state.contentionsById[id]?.evidenceDocumentIds ?? []))].filter(id => state.evidenceDocumentsById[id]?.esrId === review.esrId && state.evidenceDocumentsById[id].classifiedPageCount > 0);
  const orders = exam?.diagnosticOrderIds.map((id) => state.diagnosticOrdersById[id]).filter(Boolean) ?? [];
  const fieldErrors = validateDbqValues(dbq, version.values);
  const checks: NonNullable<QAReview['checks']> = [
    { key: 'required-fields', label: 'Required report fields', status: fieldErrors.length ? 'failed' : 'passed', severity: 'blocking', detail: fieldErrors.length ? fieldErrors.join(' ') : 'All required fields have content.', versionId: version.id, evaluatedAt: now },
    { key: 'clinician-signoff', label: 'Local clinician sign-off', status: dbq.signedVersionId === version.id && dbq.currentVersionId === version.id && dbq.localReviewState === 'review-recorded' && (dbq.formVersion !== 'synthetic-v2' || dbq.localAttestation?.versionId === version.id) ? 'passed' : 'failed', severity: 'blocking', detail: dbq.signedVersionId === version.id ? 'The currently reviewed version has a recorded local clinician sign-off.' : 'This exact report version does not have a local clinician sign-off.', versionId: version.id, evaluatedAt: now },
    { key: 'evidence-linkage', label: 'Contention evidence linkage', status: evidenceIds.length ? 'passed' : 'failed', severity: 'blocking', detail: evidenceIds.length ? `${evidenceIds.length} linked source record(s) are available for comparison.` : 'No linked evidence document is available for the report contention(s).', versionId: version.id, evaluatedAt: now },
    { key: 'form-version', label: 'Form version consistency', status: version.formVersion && version.formVersion === dbq.formVersion ? 'passed' : 'failed', severity: 'blocking', detail: version.formVersion === dbq.formVersion ? `Report and version both use ${dbq.formVersion}.` : 'The report and saved version form versions do not match.', versionId: version.id, evaluatedAt: now },
    { key: 'diagnostics', label: 'Required diagnostic review', status: orders.every(diagnosticReady) ? 'passed' : 'failed', severity: 'blocking', detail: orders.every(diagnosticReady) ? 'All required diagnostic orders have a clinician review record.' : 'A required diagnostic order is not reviewed.', versionId: version.id, evaluatedAt: now },
  ];
  const keys: (keyof WorkspaceState)[] = ['qaReviewsById', 'qaFindingsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  const findingIds = [...review.findingIds];
  const categories: Record<NonNullable<QAReview['checks']>[number]['key'], QAFinding['issueCategory']> = {
    'required-fields': 'missing-required-field',
    'clinician-signoff': 'signature-readiness',
    'evidence-linkage': 'evidence-consistency',
    'form-version': 'form-version',
    diagnostics: 'attachment',
  };
  checks.filter((check) => check.status === 'failed').forEach((check) => {
    const alreadyOpen = findingIds.map((id) => next.qaFindingsById[id]).some((finding) => finding?.origin === 'deterministic' && finding.versionId === version.id && finding.checkKey === check.key && ['open', 'correction-requested'].includes(finding.status));
    if (alreadyOpen) return;
    const id = `finding-rule-${review.id}-${check.key}-${Date.now()}-${++activitySequence}`;
    next.qaFindingsById[id] = {
      id, version: 1, createdAt: now, updatedAt: now, esrId: review.esrId, qaReviewId: review.id, dbqId: dbq.id,
      sectionReference: check.label, issueCategory: categories[check.key], severity: check.severity, title: check.detail,
      supportingEvidenceIds: evidenceIds, reviewerId: 'system-rules', status: 'open', comments: ['Created by deterministic QA check; reviewer disposition is required.'], resolutionHistory: [], origin: 'deterministic', checkKey: check.key, versionId: version.id,
    };
    findingIds.push(id);
  });
  next.qaReviewsById[reviewId] = update(review, { checks, findingIds, status: 'in-review' }, now);
  const failedCount = checks.filter((item) => item.status === 'failed').length;
  addActivity(next, { esrId: review.esrId, entityId: reviewId, entityType: 'qa-review', action: 'qa-checks-completed', rationale: `${checks.length} deterministic checks completed for version ${version.versionNumber}; ${failedCount} failed. Reviewer disposition remains required.` }, now);
  return { state: refreshWorkItems(next, now), checks, failedCount };
}

export function approveQAReview(state: WorkspaceState, reviewId: EntityId, approvalNote = '', actorId = 'user-qa') {
  const review = state.qaReviewsById[reviewId];
  const dbq = requireQAReview(state, review);
  const blockers = qaApprovalBlockers(state, review);
  if (blockers[0]) throw new WorkflowRuleError(blockers[0]);
  if (!approvalNote.trim() || approvalNote.length > 3000) throw new WorkflowRuleError('Record the independent reviewer decision before approving.');
  const now = nowIso();
  const next = copyMaps(state, ['qaReviewsById', 'dbqsById', 'examinationsById', 'correctionRequestsById', 'resultsPackagesById', 'activitiesById']);
  next.qaReviewsById[reviewId] = update(review, { status: 'approved', approvedAt: now, reviewerId: actorId, reviewerComment: approvalNote.trim() }, now);
  next.dbqsById[dbq.id] = update(dbq, { status: 'approved-for-package' }, now);
  const examination = next.examinationsById[dbq.examinationId];
  if (examination && examination.requiredDbqIds.every((id) => id === dbq.id || state.dbqsById[id]?.status === 'approved-for-package')) next.examinationsById[examination.id] = update(examination, { status: 'completed' }, now);
  Object.values(state.correctionRequestsById).filter((item) => item.dbqId === dbq.id && item.status === 'resubmitted').forEach((item) => {
    next.correctionRequestsById[item.id] = update(item, { status: 'closed', history: [...item.history, { at: now, status: 'closed', note: 'Correction accepted in QA.', actorId: 'user-qa' }] }, now);
  });
  refreshPackages(next, dbq.esrId, now);
  addActivity(next, { esrId: dbq.esrId, entityId: reviewId, entityType: 'qa-review', action: 'qa-review-approved', rationale: 'QA approval recorded; package preparation may proceed.' }, now);
  const contents = packageContents(next, dbq.esrId);
  if (!contents.issues.length) return ensureResultsPackage(next, dbq.esrId);
  return refreshWorkItems(next, now);
}

export function packageContents(state: WorkspaceState, esrId: string) {
  const esr = state.esrsById[esrId];
  const contentions = (esr?.contentionIds ?? []).map(id => state.contentionsById[id]).filter(item => item?.simulatedDisposition !== 'cancelled');
  const issues = new Set<string>();
  const reports = new Map<string, PackageManifest['reports'][number]>();
  const dbqIds = new Set<string>();
  const diagnosticResultIds = new Set<string>();
  if (!esr?.aepRecordedAt) issues.add('The ESR acknowledgement is missing.');
  if (!contentions.length) issues.add('There are no active contentions to include in this package.');
  for (const contention of contentions) {
    if (!contention) { issues.add('A contention linked to this ESR could not be found.'); continue; }
    const requirement = state.examinationRequirementsById[contention.examinationRequirementIds.at(-1) ?? ''];
    if (!requirement || !requirement.dbqIds.length || !['approved','completed'].includes(requirement.status)) {
      issues.add(`${contention.title} is awaiting an examination report for its current scope.`);
      continue;
    }
    for (const id of requirement.dbqIds) {
      const dbq = state.dbqsById[id];
      if (!dbq || dbq.esrId !== esrId || !dbq.contentionIds.includes(contention.id)) {
        issues.add(`${contention.title} has an invalid report link.`);
        continue;
      }
      dbqIds.add(dbq.id);
      if (dbq.status !== 'approved-for-package') { issues.add(`${dbq.formType} is not QA approved.`); continue; }
      const revision = state.dbqVersionsById[dbq.currentVersionId];
      const approval = Object.values(state.qaReviewsById).find(item =>
        item.esrId === esrId && item.dbqId === dbq.id && item.status === 'approved' && item.versionId === dbq.currentVersionId);
      if (!revision || revision.dbqId !== dbq.id || !approval || dbq.signedVersionId !== dbq.currentVersionId || dbq.localReviewState !== 'review-recorded') {
        issues.add(`${dbq.formType} does not have matching clinician review and QA approval for its current revision.`);
        continue;
      }
      reports.set(id, { dbqId: id, revisionId: revision.id, approvalId: approval.id });
      for (const orderId of state.examinationsById[dbq.examinationId]?.diagnosticOrderIds ?? []) {
        const order = state.diagnosticOrdersById[orderId];
        if (!order || !diagnosticReady(order)) { issues.add(`${dbq.formType} has a required diagnostic awaiting disposition.`); continue; }
        for (const resultId of order.resultIds) {
          const result = state.diagnosticResultsById[resultId];
          if (result?.esrId === esrId && result.orderId === order.id && result.reviewedAt && !result.containsRestrictedRawMaterial) diagnosticResultIds.add(result.id);
        }
      }
    }
  }
  const manifest: PackageManifest = { contentions: contentions.filter(Boolean).map(item => item.id), reports: [...reports.values()],
    diagnosticResultIds: [...diagnosticResultIds] };
  return { manifest, dbqIds: [...dbqIds], issues: [...issues] };
}

function refreshPackages(next: WorkspaceState, esrId: string, now: string) {
  const contents = packageContents(next, esrId);
  for (const item of Object.values(next.resultsPackagesById)) {
    if (item.esrId !== esrId || item.preparationStatus === 'closed' || item.simulatedResponse === 'awaiting-response' || item.simulatedResponse === 'accepted') continue;
    const ready = contents.issues.length === 0;
    const reworkResolved = ready && ['report-fix','new-exam-requested'].includes(item.simulatedResponse ?? '');
    next.resultsPackagesById[item.id] = update(item, {
      dbqIds: contents.dbqIds, diagnosticResultIds: contents.manifest.diagnosticResultIds, qaApprovalIds: contents.manifest.reports.map(report => report.approvalId),
      validationStatus: ready ? 'not-validated' : 'blocked', blockingReasons: contents.issues,
      preparationStatus: ready ? 'draft' : 'blocked', preparedManifest: undefined,
      simulatedResponse: reworkResolved ? 'not-sent' : item.simulatedResponse,
      versionNumber: reworkResolved ? item.versionNumber + 1 : item.versionNumber,
    }, now);
  }
}

function requirePackage(state: WorkspaceState, packageId: string) {
  const pack = state.resultsPackagesById[packageId];
  if (!pack || pack.preparationStatus === 'closed' || state.esrsById[pack.esrId]?.workflowClosedAt) throw new WorkflowRuleError('Select an open results package.');
  return pack;
}

export function ensureResultsPackage(state: WorkspaceState, esrId: EntityId) {
  const esr = state.esrsById[esrId];
  if (!esr) throw new WorkflowRuleError('The selected ESR is no longer available.');
  const existing = Object.values(state.resultsPackagesById).find((item) => item.esrId === esrId && item.preparationStatus !== 'closed');
  if (existing) return refreshWorkItems(state);
  if (esr.workflowClosedAt) throw new WorkflowRuleError('This ESR is already closed.');
  const contents = packageContents(state, esrId);
  if (!contents.dbqIds.length) throw new WorkflowRuleError('This ESR does not yet have a clinical report to package.');
  const now = nowIso();
  const next = copyMaps(state, ['resultsPackagesById', 'deliveryEventsById', 'activitiesById']);
  const id = `pkg-${esrId}-${Date.now()}`;
  const pack: ResultsPackage = {
    id, version: 1, createdAt: now, updatedAt: now, esrId, dbqIds: contents.dbqIds,
    diagnosticResultIds: contents.manifest.diagnosticResultIds, qaApprovalIds: contents.manifest.reports.map(report => report.approvalId),
    validationStatus: contents.issues.length ? 'blocked' : 'not-validated', preparationStatus: contents.issues.length ? 'blocked' : 'draft',
    externalState: 'not-enabled', blockingReasons: contents.issues, versionNumber: 1,
  };
  next.resultsPackagesById[id] = pack;
  const eventId = `delivery-event-${id}-created`;
  next.deliveryEventsById[eventId] = { id: eventId, version: 1, createdAt: now, updatedAt: now, packageId: id, esrId, eventType: 'package-created', recordedAt: now, actorId: 'user-operations', details: 'Results package created for review.', externalState: 'not-enabled' };
  addActivity(next, { esrId, entityId: id, entityType: 'results-package', action: 'results-package-prepared', rationale: 'Package record created for review; no external submission occurred.' }, now);
  return refreshWorkItems(next, now);
}

export function validateResultsPackage(state: WorkspaceState, packageId: EntityId) {
  const pack = requirePackage(state, packageId);
  if (['awaiting-response','accepted'].includes(pack.simulatedResponse ?? '')) throw new WorkflowRuleError('The submitted revision is locked while its response is being reconciled.');
  const contents = packageContents(state, pack.esrId);
  const blockingReasons = contents.issues;
  const now = nowIso();
  const next = copyMaps(state, ['resultsPackagesById', 'deliveryEventsById', 'activitiesById']);
  const passed = blockingReasons.length === 0;
  const reworkResolved = passed && ['report-fix','new-exam-requested'].includes(pack.simulatedResponse ?? '');
  next.resultsPackagesById[packageId] = update(pack, {
    validationStatus: passed ? 'passed' : 'blocked', preparationStatus: passed ? 'ready-for-review' : 'blocked', blockingReasons,
    dbqIds: contents.dbqIds, diagnosticResultIds: contents.manifest.diagnosticResultIds, qaApprovalIds: contents.manifest.reports.map(report => report.approvalId),
    preparedManifest: undefined, simulatedResponse: reworkResolved ? 'not-sent' : pack.simulatedResponse,
    versionNumber: reworkResolved ? pack.versionNumber + 1 : pack.versionNumber,
  }, now);
  const id = `delivery-event-${packageId}-validation-${Date.now()}`;
  next.deliveryEventsById[id] = { id, version: 1, createdAt: now, updatedAt: now, packageId, esrId: pack.esrId, eventType: 'validation-completed', recordedAt: now, actorId: 'user-operations', details: passed ? 'Package checks passed.' : blockingReasons.join(' '), externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: packageId, entityType: 'results-package', action: 'package-validation-completed', rationale: passed ? 'Package checks passed.' : blockingReasons.join(' ') }, now);
  return refreshWorkItems(next, now);
}

export function prepareResultsPackage(state: WorkspaceState, packageId: EntityId) {
  const pack = requirePackage(state, packageId);
  if (pack.validationStatus !== 'passed' || ['awaiting-response','accepted'].includes(pack.simulatedResponse ?? '')) throw new WorkflowRuleError(pack.blockingReasons[0] ?? 'Run package validation and resolve its blockers before preparation.');
  const contents = packageContents(state, pack.esrId);
  if (contents.issues[0]) throw new WorkflowRuleError(contents.issues[0]);
  const now = nowIso();
  const next = copyMaps(state, ['resultsPackagesById', 'deliveryEventsById', 'activitiesById']);
  next.resultsPackagesById[packageId] = update(pack, { preparationStatus: 'prepared-locally', preparedManifest: contents.manifest, externalState: 'not-enabled' }, now);
  const id = `delivery-event-${packageId}-prepared-${Date.now()}`;
  next.deliveryEventsById[id] = { id, version: 1, createdAt: now, updatedAt: now, packageId, esrId: pack.esrId, eventType: 'authorized-review-recorded', recordedAt: now, actorId: 'user-operations', details: 'Authorized staff recorded package preparation. External submission is not enabled.', externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: packageId, entityType: 'results-package', action: 'results-package-prepared', rationale: 'Package preparation recorded; no VA transmission or receipt is claimed.' }, now);
  return refreshWorkItems(next, now);
}

export function createReconciliationTask(state: WorkspaceState, packageId: EntityId, details: string) {
  const pack = requirePackage(state, packageId);
  if (!details.trim() || details.length > 3000) throw new WorkflowRuleError('Describe the reconciliation follow-up.');
  const now = nowIso();
  const next = copyMaps(state, ['deliveryEventsById', 'activitiesById']);
  const id = `delivery-event-${packageId}-reconcile-${Date.now()}`;
  next.deliveryEventsById[id] = { id, version: 1, createdAt: now, updatedAt: now, packageId, esrId: pack.esrId, eventType: 'reconciliation-task-created', recordedAt: now, actorId: 'user-operations', details: details.trim(), externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: id, entityType: 'delivery-event', action: 'reconciliation-task-created', rationale: details.trim() }, now);
  return refreshWorkItems(next, now);
}

export function recordDeliveryReview(state: WorkspaceState, packageId: EntityId, details: string) {
  const pack = state.resultsPackagesById[packageId];
  if (!pack || state.esrsById[pack.esrId]?.workflowClosedAt || pack.preparationStatus !== 'prepared-locally') throw new WorkflowRuleError('Prepare the package before recording delivery review.');
  if (!details.trim()) throw new WorkflowRuleError('Add a review note.');
  const now = nowIso();
  const next = copyMaps(state, ['deliveryEventsById', 'activitiesById']);
  const id = `delivery-event-${packageId}-staff-review-${Date.now()}`;
  next.deliveryEventsById[id] = { id, version: 1, createdAt: now, updatedAt: now, packageId, esrId: pack.esrId, eventType: 'staff-reconciliation-recorded', recordedAt: now, actorId: 'user-operations', details: `${details.trim()} External response remains unverified.`, externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: id, entityType: 'delivery-event', action: 'delivery-review-recorded', rationale: details.trim() }, now);
  return refreshWorkItems(next, now);
}

export function simulatePackageTransmission(state: WorkspaceState, packageId: EntityId) {
  const pack = requirePackage(state, packageId);
  if (pack.preparationStatus !== 'prepared-locally' || pack.validationStatus !== 'passed') throw new WorkflowRuleError('Validate and locally prepare the package before starting a simulated delivery.');
  if (['report-fix', 'new-exam-requested'].includes(pack.simulatedResponse ?? '')) throw new WorkflowRuleError('Complete the requested rework before resubmitting this package.');
  if (pack.simulatedResponse === 'awaiting-response') throw new WorkflowRuleError('A simulated response is already pending for this package.');
  if (pack.simulatedResponse === 'accepted') throw new WorkflowRuleError('This package already has a simulated accepted response.');
  const contents = packageContents(state, pack.esrId);
  if (contents.issues[0]) throw new WorkflowRuleError(contents.issues[0]);
  if (pack.preparedManifest && JSON.stringify(pack.preparedManifest) !== JSON.stringify(contents.manifest)) throw new WorkflowRuleError('Package contents changed. Validate and prepare the current revision again.');
  const now = nowIso();
  const next = copyMaps(state, ['resultsPackagesById', 'deliveryEventsById', 'activitiesById']);
  next.resultsPackagesById[packageId] = update(pack, {
    simulatedResponse: 'awaiting-response', simulatedResponseNote: 'Awaiting a local simulated response.',
    submissions: [...(pack.submissions ?? []), { sequence: pack.versionNumber, manifest: contents.manifest, openedAt: now }],
  }, now);
  const eventId = `delivery-event-${packageId}-simulated-transmission-${Date.now()}`;
  next.deliveryEventsById[eventId] = { id: eventId, version: 1, createdAt: now, updatedAt: now, packageId, esrId: pack.esrId, eventType: 'simulated-transmission', recordedAt: now, actorId: 'user-operations', details: 'Simulated delivery handoff opened in this browser. Nothing was transmitted to VA.', externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: packageId, entityType: 'results-package', action: 'delivery-review-recorded', rationale: 'A simulated delivery response was requested locally; external transmission is not enabled.' }, now);
  return refreshWorkItems(next, now);
}

export function recordSimulatedPackageResponse(state: WorkspaceState, packageId: EntityId, response: 'accepted' | 'report-fix' | 'new-exam-requested', details: string, targetId?: EntityId) {
  const pack = requirePackage(state, packageId);
  if (pack.simulatedResponse !== 'awaiting-response') throw new WorkflowRuleError('Start a simulated delivery handoff before recording a response.');
  if (!['accepted','report-fix','new-exam-requested'].includes(response) || !details.trim() || details.length > 3000) throw new WorkflowRuleError('Select and describe the simulated response.');
  const now = nowIso();
  const keys: (keyof WorkspaceState)[] = ['resultsPackagesById', 'contentionsById', 'examinationRequirementsById', 'qaReviewsById', 'qaFindingsById', 'dbqsById', 'examinationsById', 'correctionRequestsById', 'deliveryEventsById', 'activitiesById'];
  const next = copyMaps(state, keys);
  const current = pack.submissions?.at(-1);
  const sent = current?.manifest ?? packageContents(state, pack.esrId).manifest;
  const responseRecord = { kind: response, details: details.trim(), targetId, at: now };
  const submissions = current ? [...pack.submissions!.slice(0, -1), { ...current, response: responseRecord }] :
    [{ sequence: pack.versionNumber, manifest: sent, openedAt: pack.updatedAt, response: responseRecord }];
  next.resultsPackagesById[packageId] = update(pack, {
    submissions, simulatedResponse: response, simulatedResponseNote: details.trim(),
    ...(response === 'accepted' ? {} : { preparationStatus: 'blocked' as const, validationStatus: 'blocked' as const, blockingReasons: [`Simulated ${response === 'report-fix' ? 'report correction' : 'new examination'} response needs follow-up.`] }),
  }, now);
  if (response === 'accepted') {
    const acceptedContentionIds = sent.contentions;
    acceptedContentionIds.forEach((id) => {
      const contention = next.contentionsById[id];
      if (contention && contention.simulatedDisposition !== 'cancelled') next.contentionsById[id] = update(contention, { simulatedDisposition: 'accepted', simulatedDispositionNote: details.trim() }, now);
    });
  } else if (response === 'report-fix') {
    const dbq = targetId ? state.dbqsById[targetId] : undefined;
    if (!dbq || !sent.reports.some(report => report.dbqId === dbq.id && report.revisionId === dbq.currentVersionId)) throw new WorkflowRuleError('Select a report included in this package for the simulated correction request.');
    const review = Object.values(state.qaReviewsById).find((item) => item.dbqId === dbq.id && item.status === 'approved');
    if (!review) throw new WorkflowRuleError('The selected report does not have an approved internal QA review.');
    const findingId = `finding-simulated-fix-${dbq.id}-${Date.now()}`;
    next.qaFindingsById[findingId] = { id: findingId, version: 1, createdAt: now, updatedAt: now, esrId: pack.esrId, qaReviewId: review.id, dbqId: dbq.id, sectionReference: 'Simulated delivery response', issueCategory: 'attachment', severity: 'blocking', title: 'Simulated report-fix response', supportingEvidenceIds: [], reviewerId: 'user-qa', status: 'open', comments: [details.trim()], resolutionHistory: [{ at: now, actorId: 'user-qa', status: 'open', comment: details.trim() }], origin: 'reviewer', versionId: dbq.currentVersionId };
    next.qaReviewsById[review.id] = update(review, { findingIds: [...review.findingIds, findingId], status: 'queued', checks: [], reviewerComment: details.trim() }, now);
    next.dbqsById[dbq.id] = update(dbq, { status: 'ready-for-qa' }, now);
    refreshPackages(next, pack.esrId, now);
  } else {
    const contention = targetId ? state.contentionsById[targetId] : undefined;
    if (!contention || contention.esrId !== pack.esrId || !sent.contentions.includes(contention.id) || contention.simulatedDisposition === 'cancelled') throw new WorkflowRuleError('Select an active contention included in the submitted package.');
    const previous = state.examinationRequirementsById[contention.examinationRequirementIds.at(-1) ?? ''];
    if (!previous || !['approved','completed'].includes(previous.status)) throw new WorkflowRuleError('The selected contention requires an approved examination scope.');
    const id = `req-rework-${contention.id}-${Date.now()}`;
    next.examinationRequirementsById[id] = { ...previous, id, version: 1, createdAt: now, updatedAt: now, contentionIds: [contention.id], dbqIds: [], diagnosticOrderIds: [], status: 'approved', reviewerRationale: 'Re-examination requested using the previously approved scope.' };
    next.contentionsById[contention.id] = update(contention, { status: 'approved-for-exam', simulatedDisposition: 'new-exam-requested', simulatedDispositionNote: details.trim(), examinationRequirementIds: [...contention.examinationRequirementIds, id] }, now);

  }
  const eventId = `delivery-event-${packageId}-simulated-response-${Date.now()}`;
  const responseLabel = response === 'accepted' ? 'accepted' : response === 'report-fix' ? 'report fix requested' : 'new examination requested';
  next.deliveryEventsById[eventId] = { id: eventId, version: 1, createdAt: now, updatedAt: now, packageId, esrId: pack.esrId, eventType: 'simulated-response', recordedAt: now, actorId: 'user-operations', details: `Local simulated response (${responseLabel}): ${details.trim()} No VA response was received.`, externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: packageId, entityType: 'results-package', action: 'simulated-response-recorded', rationale: `Local simulated response (${responseLabel}) recorded. It does not represent an external VA response.` }, now);
  return refreshWorkItems(next, now);
}

export function recordSimulatedContentionCancellation(state: WorkspaceState, esrId: EntityId, contentionId: EntityId, reason: string) {
  const esr = state.esrsById[esrId];
  const contention = state.contentionsById[contentionId];
  if (!esr || !contention || contention.esrId !== esrId) throw new WorkflowRuleError('The selected contention is not linked to this ESR.');
  if (esr.workflowClosedAt) throw new WorkflowRuleError('This ESR is already closed in the local workflow.');
  if (!reason.trim() || reason.length > 3000) throw new WorkflowRuleError('Record a cancellation reason.');
  if (contention.simulatedDisposition === 'cancelled') throw new WorkflowRuleError('The contention is already cancelled.');
  if (Object.values(state.resultsPackagesById).some(item => item.esrId === esrId && item.simulatedResponse === 'awaiting-response')) throw new WorkflowRuleError('Resolve the simulated submission before recording a contention cancellation.');
  if (contention.simulatedDisposition === 'accepted') throw new WorkflowRuleError('An accepted simulated result cannot be changed to cancelled.');
  const now = nowIso();
  const next = copyMaps(state, ['contentionsById', 'examinationsById', 'appointmentsById', 'correctionRequestsById', 'resultsPackagesById', 'deliveryEventsById', 'activitiesById']);
  next.contentionsById[contentionId] = update(contention, { simulatedDisposition: 'cancelled', simulatedDispositionNote: reason.trim() }, now);
  const exclusivelyCancelled = (ids: string[]) => ids.includes(contentionId) && ids.every(id => next.contentionsById[id]?.simulatedDisposition === 'cancelled');
  for (const exam of Object.values(state.examinationsById)) {
    if (exam.esrId !== esrId || exam.status === 'completed' || !exclusivelyCancelled(exam.contentionIds)) continue;
    next.examinationsById[exam.id] = update(exam, { status: 'cancelled' }, now);
    for (const id of exam.appointmentIds) {
      const appointment = state.appointmentsById[id];
      if (!appointment || !['held','confirmed-locally','reschedule-requested'].includes(appointment.status)) continue;
      next.appointmentsById[id] = update(appointment, { status: 'cancelled', history: [...appointment.history,
        { status: 'cancelled', at: now, actorId: 'user-operations', note: 'All linked contentions were cancelled in the local simulation.' }],
      }, now);
    }
  }
  for (const correction of Object.values(state.correctionRequestsById)) {
    const dbq = state.dbqsById[correction.dbqId];
    if (correction.esrId !== esrId || correction.status === 'closed' || !dbq || !exclusivelyCancelled(dbq.contentionIds)) continue;
    next.correctionRequestsById[correction.id] = update(correction, { status: 'closed', history: [...correction.history,
      { status: 'closed', at: now, actorId: 'user-operations', note: 'All linked contentions were cancelled in the local simulation.' }],
    }, now);
  }
  const pack = Object.values(state.resultsPackagesById).filter((item) => item.esrId === esrId).sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  if (pack) {
    const eventId = `delivery-event-${pack.id}-cancel-${contentionId}-${Date.now()}`;
    next.deliveryEventsById[eventId] = { id: eventId, version: 1, createdAt: now, updatedAt: now, packageId: pack.id, esrId, eventType: 'contention-cancelled', recordedAt: now, actorId: 'user-operations', details: `Simulated contention cancellation recorded locally: ${reason.trim()}`, externalState: 'not-enabled' };
  }
  refreshPackages(next, esrId, now);
  if (esr.contentionIds.every(id => next.contentionsById[id]?.simulatedDisposition === 'cancelled') && pack) {
    next.resultsPackagesById[pack.id] = update(next.resultsPackagesById[pack.id], { simulatedResponse: 'not-sent' }, now);
  }
  addActivity(next, { esrId, entityId: contentionId, entityType: 'contention', action: 'simulated-contention-cancelled', rationale: reason.trim() }, now);
  return refreshWorkItems(next, now);
}

export function pendingReconciliation(state: WorkspaceState, esrId: string) {
  const events = Object.values(state.deliveryEventsById).filter(item => item.esrId === esrId);
  const completed = new Set(events.filter(item => item.eventType === 'reconciliation-task-completed').map(item => item.relatedEventId));
  return events.filter(item => item.eventType === 'reconciliation-task-created' && !completed.has(item.id));
}

export function completeReconciliationTask(state: WorkspaceState, taskId: string, note: string) {
  const task = state.deliveryEventsById[taskId];
  if (!task || !pendingReconciliation(state, task.esrId).some(item => item.id === task.id) || !note?.trim() || note.length > 3000) throw new WorkflowRuleError('Select a pending reconciliation task and add its resolution.');
  const pack = requirePackage(state, task.packageId);
  const now = nowIso();
  const next = copyMaps(state, ['deliveryEventsById', 'activitiesById']);
  const id = `${task.id}-complete`;
  next.deliveryEventsById[id] = { id, version: 1, createdAt: now, updatedAt: now, packageId: pack.id, esrId: pack.esrId, eventType: 'reconciliation-task-completed', relatedEventId: task.id, recordedAt: now, actorId: 'user-operations', details: note.trim(), externalState: 'not-enabled' };
  addActivity(next, { esrId: pack.esrId, entityId: id, entityType: 'delivery-event', action: 'reconciliation-task-completed', rationale: note.trim() }, now);
  return refreshWorkItems(next, now);
}

export function closureBlockers(state: WorkspaceState, esrId: string) {
  const esr = state.esrsById[esrId];
  const contentions = (esr?.contentionIds ?? []).map(id => state.contentionsById[id]);
  return [
    ...(!esr?.aepRecordedAt ? ['A local ESR acknowledgement is required.'] : []),
    ...(!contentions.length || contentions.some(item => !['accepted','cancelled'].includes(item?.simulatedDisposition ?? '')) ? ['Every contention needs a simulated acceptance or cancellation.'] : []),
    ...(Object.values(state.correctionRequestsById).some(item => item.esrId === esrId && item.status !== 'closed') ? ['Resolve open correction tasks.'] : []),
    ...(pendingReconciliation(state, esrId).length ? ['Complete pending reconciliation tasks.'] : []),
    ...(Object.values(state.resultsPackagesById).some(item => item.esrId === esrId && ['awaiting-response','report-fix','new-exam-requested'].includes(item.simulatedResponse ?? '')) ? ['Resolve the pending simulated package response.'] : []),
  ];
}

export function closeESR(state: WorkspaceState, esrId: EntityId, note: string) {
  const esr = state.esrsById[esrId];
  if (!esr) throw new WorkflowRuleError('The selected ESR is no longer available.');
  if (esr.workflowClosedAt) throw new WorkflowRuleError('This ESR has already been closed in the local workflow.');
  const blockers = closureBlockers(state, esrId);
  if (blockers[0]) throw new WorkflowRuleError(blockers[0]);
  if (!note.trim() || note.length > 3000) throw new WorkflowRuleError('Add a local closure note.');
  const now = nowIso();
  const next = copyMaps(state, ['esrsById', 'resultsPackagesById', 'deliveryEventsById', 'activitiesById']);
  const contentions = esr.contentionIds.map(id => state.contentionsById[id]);
  const workflowDisposition = contentions.some(item => item.simulatedDisposition === 'accepted') ? 'completed' : 'cancelled';
  next.esrsById[esrId] = update(esr, { workflowClosedAt: now, workflowDisposition, closureNote: note.trim(), intakeStatus: 'completed' }, now);
  const pack = Object.values(state.resultsPackagesById).filter((item) => item.esrId === esrId).sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  for (const item of Object.values(state.resultsPackagesById).filter(item => item.esrId === esrId)) {
    next.resultsPackagesById[item.id] = update(item, { preparationStatus: 'closed' }, now);
  }
  if (pack) {
    const eventId = `delivery-event-${pack.id}-closed-${Date.now()}`;
    next.deliveryEventsById[eventId] = { id: eventId, version: 1, createdAt: now, updatedAt: now, packageId: pack.id, esrId, eventType: 'esr-closed', recordedAt: now, actorId: 'user-operations', details: `ESR closed in this local simulation: ${note.trim()}`, externalState: 'not-enabled' };
  }
  addActivity(next, { esrId, entityId: esrId, entityType: 'esr', action: 'esr-closed', rationale: `Local workflow closure recorded after all contention dispositions and rework checks passed. ${note.trim()}` }, now);
  return refreshWorkItems(next, now);
}

export function decideInsight(state: WorkspaceState, insightId: EntityId, decision: 'accepted-for-human-review' | 'rejected' | 'dismissed') {
  const insight = state.aiInsightsById[insightId];
  if (!insight) throw new WorkflowRuleError('This rules-based insight is no longer available.');
  const now = nowIso();
  const next = copyMaps(state, ['aiInsightsById', 'activitiesById']);
  next.aiInsightsById[insightId] = update(insight, { decision, decidedBy: 'user-operations', decidedAt: now }, now);
  addActivity(next, { esrId: insight.esrId, entityId: insightId, entityType: 'esr', action: 'assistance-disposition-recorded', rationale: `Rules-based suggestion disposition: ${decision}. No clinical or workflow decision was made.` }, now);
  return refreshWorkItems(next, now);
}

export function commandTime(state: WorkspaceState) { return refreshWorkItems(state, nowIso()); }
