import assert from 'node:assert/strict';
import test from 'node:test';
import { buildEvidenceBrief } from '../src/stages/evidenceBriefModel.ts';
import { createSeedWorkspace } from '../src/data/initialization/seedWorkspace.ts';
import { getCaseMetrics } from '../src/domain/selectors.ts';
import {
  recordSimulatedPackageResponse, simulatePackageTransmission, examinerEligibility, getTriageMapping, holdAppointment, prepareResultsPackage,
  resubmitCorrection, reviewContention, reviewIntakeException, saveDBQDraft, validateResultsPackage,
  appointmentConfirmation, closeESR, completeClinicalReview, completeReconciliationTask, createReconciliationTask,
  dbqForm, recordAppointmentLogistics, recordExamEncounter, recordDiagnosticException, recordOutreach, confirmAppointment,
} from '../src/services/workflowService.ts';

const seed = () => createSeedWorkspace();
const evidence = { title: 'EOB.pdf', category: 'insurance-financial', classificationStatus: 'indexed', pageCount: 2, unreadablePageCount: 0 };

test('document summary preserves clinical qualifiers, decimals, and all remaining points', () => {
  const sentences = ['This is not a bill.', 'Two entries are marked paid.', 'The listed amount is $25.00.', 'Dr. Green found no evidence of injury.', 'Remark code PDC is listed.'];
  const brief = buildEvidenceBrief({ ...evidence, processing: { provider: 'ai', summary: sentences.join(' '), sources: [{ page: 2, text: 'Original page' }] } });
  assert.deepEqual([brief.paragraph, ...brief.points, ...brief.more], sentences);
  assert.deepEqual(brief.sources, [{ page: 2, text: 'Original page' }]);
});

test('local form labels are not a narrative and keyed facts retain their page', () => {
  const raw = 'Name Sex Provider Plan Claim Status Service Paid Due Forms '.repeat(10);
  const sources = [{ page: 1, text: raw }, { page: 2, text: 'This is not a bill\nRemark code: PDC' }];
  const brief = buildEvidenceBrief({ ...evidence, processing: { provider: 'local', summary: raw, sources } });
  assert.match(brief.paragraph, /insurer/);
  assert.deepEqual(brief.points, ['“This is not a bill” (p. 2)', '“Remark code: PDC” (p. 2)']);
  assert.deepEqual(brief.sources, sources);
  assert.equal(brief.generated, false);
});

test('pending, empty and unreadable records are distinguished', () => {
  const unreadable = buildEvidenceBrief({ ...evidence, unreadablePageCount: 1, classificationStatus: 'needs-human-review' });
  assert.match(unreadable.issues.join(' '), /1 page could not be read/);
  assert.match(unreadable.issues.join(' '), /No source excerpt/);
  const pending = buildEvidenceBrief({ ...evidence, classificationStatus: 'queued', processing: { provider: 'ai', summary: 'Old result' } });
  assert.match(pending.paragraph, /awaiting processing/);
  assert.deepEqual(pending.issues, []);
});

test('section and contention reviews do not borrow another report or contention’s excerpts', () => {
  const parent = { ...evidence, sections: [{ title: 'A' }, { title: 'B' }], processing: { provider: 'ai', summary: 'Whole file.', sources: [{ page: 1, text: 'First' }, { page: 2, text: 'Second' }] } };
  const section = { ...evidence, sectionId: 'B', title: 'Second report', startPage: 2, endPage: 2, summary: 'Only the second report.' };
  const brief = buildEvidenceBrief(section, parent);
  assert.equal(brief.paragraph, section.summary);
  assert.deepEqual(brief.sources, [{ page: 2, text: 'Second' }]);
  const doc = { ...parent, syntheticExcerpts: [{ contentionId: 'other', pageNumber: 1, text: 'Other contention' }] };
  assert.deepEqual(buildEvidenceBrief(doc, undefined, 'current').sources, []);
  assert.match(buildEvidenceBrief(doc, undefined, 'current').paragraph, /No excerpt is linked/);
  assert.deepEqual(buildEvidenceBrief(doc, undefined, 'other').sources, [{ page: 1, text: 'Other contention' }]);
});
const valuesFor = (dbq) => dbq.formType.toLowerCase().includes('opinion')
  ? { ...dbq.values, opinionQuestion: 'Is the condition related to the documented service history?', rationale: 'Revised clinician-authored rationale with the reviewed source references.' }
  : { ...dbq.values, history: 'History reviewed and updated.', findings: 'Clinician findings recorded.', rationale: 'Revised clinician-authored rationale with the reviewed source references.' };

test('seed creates a connected multi-ESR workspace with lifecycle variation', () => {
  const state = seed();
  assert.equal(Object.keys(state.esrsById).length, 150);
  assert.equal(Object.keys(state.contentionsById).length, 300);
  assert.ok(Object.keys(state.examinationsById).length > 80);
  assert.ok(Object.keys(state.appointmentsById).length > 40);
  assert.ok(Object.keys(state.dbqsById).length > Object.keys(state.examinationsById).length);
  assert.ok(Object.keys(state.aiInsightsById).length >= 900);
  for (const esr of Object.values(state.esrsById)) {
    assert.ok(state.veteransById[esr.veteranId]);
    for (const id of esr.contentionIds) assert.equal(state.contentionsById[id]?.esrId, esr.id);
    for (const id of esr.evidenceDocumentIds) assert.equal(state.evidenceDocumentsById[id]?.esrId, esr.id);
  }
  assert.ok(new Set(Object.values(state.esrsById).map((item) => item.intakeStatus)).size > 1);
  assert.ok(getCaseMetrics(state).esrs === 150);
});

test('intake exception disposition only changes its owning ESR', () => {
  const state = seed();
  const issue = Object.values(state.intakeExceptionsById).find((item) => item.status === 'open');
  assert.ok(issue);
  const otherESR = Object.values(state.esrsById).find((item) => item.id !== issue.esrId);
  const otherStatus = otherESR.intakeStatus;
  const next = reviewIntakeException(state, issue.id, 'resolved', 'Source was reviewed; unreadable pages are documented for human handling.');
  assert.equal(next.intakeExceptionsById[issue.id].status, 'resolved');
  assert.equal(next.esrsById[otherESR.id].intakeStatus, otherStatus);
  assert.ok(Object.values(next.activitiesById).some((item) => item.entityId === issue.id && item.action === 'intake-exception-resolved'));
});

test('triage approval creates only the selected contention requirement', () => {
  const state = seed();
  const selected = Object.values(state.contentionsById).find((item) => item.status === 'awaiting-review' && state.esrsById[item.esrId].intakeStatus === 'ready-for-triage');
  const other = Object.values(state.contentionsById).find((item) => item.esrId === selected.esrId && item.id !== selected.id);
  const otherStatus = other.status;
  const mapping = getTriageMapping(selected.title);
  const next = reviewContention(state, selected.id, 'Evidence and examination scope reviewed by staff.', 'approved-for-exam', [mapping.dbq], mapping.modality[0]);
  assert.equal(next.contentionsById[selected.id].status, 'approved-for-exam');
  assert.equal(next.contentionsById[other.id].status, otherStatus);
  const requirement = next.examinationRequirementsById[next.contentionsById[selected.id].examinationRequirementIds.at(-1)];
  assert.equal(requirement.status, 'approved');
  assert.ok(Object.values(next.workItemsById).some((item) => item.stage === 'Scheduling' && item.esrId === selected.esrId));
});

test('appointment slot eligibility prevents examiner double booking', () => {
  const state = seed();
  const requirements = Object.values(state.examinationRequirementsById).filter((item) => item.status === 'approved');
  let choice;
  for (const requirement of requirements) {
    for (const examiner of Object.values(state.examinersById)) {
      const slot = examiner.availableSlots.find((item) => !examinerEligibility(state, requirement.id, examiner.id, item.modality, item));
      if (slot) { choice = { requirement, examiner, slot }; break; }
    }
    if (choice) break;
  }
  assert.ok(choice, 'expected one eligible provider slot in the connected seed');
  const held = holdAppointment(state, choice.requirement.id, choice.examiner.id, choice.slot);
  assert.ok(held.appointmentId);
  assert.equal(held.state.appointmentsById[held.appointmentId].status, 'held');
  assert.throws(() => holdAppointment(held.state, choice.requirement.id, choice.examiner.id, choice.slot), /already has an appointment/);
});

test('DBQ drafts remain isolated and QA correction preserves prior report versions', () => {
  const state = seed();
  const dbqs = Object.values(state.dbqsById);
  const first = dbqs.find((item) => item.status === 'in-progress') ?? dbqs[0];
  const second = dbqs.find((item) => item.id !== first.id);
  const draft = saveDBQDraft(state, first.id, { ...first.values, history: 'Only this report draft was updated.' });
  assert.equal(draft.dbqsById[first.id].values.history, 'Only this report draft was updated.');
  assert.deepEqual(draft.dbqsById[second.id].values, second.values);
  assert.ok(Object.values(draft.activitiesById).some((item) => item.entityId === first.id && item.action === 'dbq-draft-saved'));

  const correction = Object.values(state.correctionRequestsById).find((item) => item.status === 'open');
  assert.ok(correction);
  const correctionDBQ = state.dbqsById[correction.dbqId];
  const priorVersionId = correctionDBQ.currentVersionId;
  const revised = resubmitCorrection(state, correction.dbqId, valuesFor(correctionDBQ));
  assert.notEqual(revised.dbqsById[correction.dbqId].currentVersionId, priorVersionId);
  assert.ok(revised.dbqsById[correction.dbqId].versionIds.includes(priorVersionId));
  assert.equal(revised.qaReviewsById[correction.qaReviewId].status, 'correction-requested');
  assert.notEqual(revised.dbqsById[correction.dbqId].signedVersionId, revised.dbqsById[correction.dbqId].currentVersionId);
  assert.ok(!Object.values(revised.workItemsById).some(item => item.entityId === correction.qaReviewId));
  assert.equal(revised.correctionRequestsById[correction.id].status, 'resubmitted');
});

test('package preparation requires approved reports and never marks external confirmation', () => {
  const state = seed();
  const blocked = Object.values(state.resultsPackagesById).find((item) => item.validationStatus === 'blocked');
  assert.ok(blocked);
  assert.throws(() => prepareResultsPackage(state, blocked.id), /not QA approved|not clinician review|checks/);

  const ready = Object.values(state.resultsPackagesById).find((item) => item.validationStatus === 'passed');
  assert.ok(ready);
  const validated = validateResultsPackage(state, ready.id);
  assert.equal(validated.resultsPackagesById[ready.id].validationStatus, 'passed');
  const prepared = prepareResultsPackage(validated, ready.id);
  assert.equal(prepared.resultsPackagesById[ready.id].preparationStatus, 'prepared-locally');
  assert.equal(prepared.resultsPackagesById[ready.id].externalState, 'not-enabled');
  assert.ok(Object.values(prepared.deliveryEventsById).filter((item) => item.packageId === ready.id).every((item) => item.externalState === 'not-enabled'));
});


test('simulated VA report fixes enter QA and new exams enter Scheduling', () => {
  const state = seed();
  const pack = Object.values(state.resultsPackagesById).find(item =>
    item.preparationStatus === 'prepared-locally' && item.validationStatus === 'passed');
  assert.ok(pack);
  const report = state.dbqsById[pack.dbqIds[0]];
  const contention = state.contentionsById[report.contentionIds[0]];
  const pending = simulatePackageTransmission(state, pack.id);

  const fix = recordSimulatedPackageResponse(pending, pack.id, 'report-fix', 'Synthetic report correction.', report.id);
  const review = Object.values(fix.qaReviewsById).find(item => item.dbqId === report.id);
  assert.equal(review.status, 'queued');
  assert.ok(Object.values(fix.workItemsById).some(item => item.stage === 'Internal QA' && item.entityId === review.id));
  assert.ok(!Object.values(fix.workItemsById).some(item => item.stage === 'Clinical Evaluation' && item.entityId === report.id));

  const reexam = recordSimulatedPackageResponse(pending, pack.id, 'new-exam-requested', 'Synthetic repeat examination.', contention.id);
  const requirementId = reexam.contentionsById[contention.id].examinationRequirementIds.at(-1);
  const requirement = reexam.examinationRequirementsById[requirementId];
  assert.equal(requirement.status, 'approved');
  assert.deepEqual(requirement.contentionIds, [contention.id]);
  assert.ok(Object.values(reexam.workItemsById).some(item => item.stage === 'Scheduling' && item.entityId === requirementId));
});

function freshSchedulingScope() {
  const state = seed();
  for (const work of Object.values(state.workItemsById)) {
    if (work.stage !== 'Scheduling' || !['examination-requirement', 'examination'].includes(work.entityType)) continue;
    const exam = state.examinationsById[work.entityId];
    if (exam?.appointmentIds.length || exam?.diagnosticOrderIds.length) continue;
    const requirement = state.examinationRequirementsById[work.entityId];
    if (!requirement || requirement.requestedDiagnostics.length || requirement.requestedDbqTypes.length !== 1) continue;
    for (const provider of Object.values(state.examinersById)) {
      const slot = provider.availableSlots.find(item => !examinerEligibility(state, work.entityId, provider.id, item.modality, item));
      if (slot) return { state, work, provider, slot };
    }
  }
  // Isolate an approved plan with a fresh scope from the existing multi-case fixture.
  const contention = Object.values(state.contentionsById).find(item => item.status === 'awaiting-review' &&
    state.esrsById[item.esrId].intakeStatus === 'ready-for-triage' && /knee/i.test(item.title));
  const mapping = getTriageMapping(contention.title);
  const next = reviewContention(state, contention.id, 'Synthetic scope verified.', 'approved-for-exam', [mapping.dbq], 'in-person');
  const id = next.contentionsById[contention.id].examinationRequirementIds.at(-1);
  const work = Object.values(next.workItemsById).find(item => item.entityId === id);
  for (const provider of Object.values(next.examinersById)) {
    const slot = provider.availableSlots.find(item => !examinerEligibility(next, work.entityId, provider.id, item.modality, item));
    if (slot) return { state: next, work, provider, slot };
  }
  assert.fail('expected a new scheduling scope and available examiner');
}

const localLogistics = { specialTransportRequired: false, specialTransportStatus: 'not-required', consentStatus: 'simulated-recorded', vaPreapprovalStatus: 'not-required', note: 'Local logistics and consent reviewed.' };

test('reserve scheduling requires three valid attempts and preserves local notice evidence', () => {
  const { state, work, provider, slot } = freshSchedulingScope();
  let { state: next, appointmentId } = holdAppointment(state, work.entityId, provider.id, slot);
  const claimant = next.veteransById[next.esrsById[work.esrId].veteranId];
  claimant.reserveSchedulingAllowed = true;
  claimant.communicationOptIns = ['email'];
  claimant.representativeReference = 'SYN-REP-10';
  next.appointmentsById[appointmentId].outreachWindowOpenedAt = '2000-01-01T00:00:00Z';
  next = recordAppointmentLogistics(next, appointmentId, localLogistics);
  const log = (channel, attemptedAt, voicemail) => recordOutreach(next, work.esrId, appointmentId, channel, 'no-response', 'Contact attempted locally.', { attemptedAt, voicemail });
  assert.throws(() => log('phone', '2000-01-01T10:00:00Z'), /voicemail/i);
  assert.throws(() => log('sms', '2000-01-01T10:00:00Z'), /opt-in/i);
  next = log('phone', '2000-01-01T10:00:00Z', 'left');
  assert.throws(() => log('phone', '2000-01-01T11:00:00Z', 'left'), /different channels/i);
  next = log('email', '2000-01-01T15:00:00Z');
  assert.throws(() => confirmAppointment(next, appointmentId), /three attempts/i);
  next = log('phone', '2000-01-02T10:00:00Z', 'unavailable');
  assert.equal(appointmentConfirmation(next, next.appointmentsById[appointmentId]).method, 'reserve');
  next = confirmAppointment(next, appointmentId);
  const appointment = next.appointmentsById[appointmentId];
  assert.equal(appointment.schedulingMethod, 'reserve');
  assert.equal(appointment.localNotification.representativeReference, 'SYN-REP-10');
  const notices = Object.values(next.communicationsById).filter(entry => entry.appointmentId === appointmentId && entry.channel === 'mail');
  assert.deepEqual(notices.map(entry => entry.recipient).sort(), ['claimant', 'representative']);
});

test('sign-off checks the stored draft revision in addition to the form version ID', () => {
  const { state, work, provider, slot } = freshSchedulingScope();
  let { state: next, appointmentId } = holdAppointment(state, work.entityId, provider.id, slot);
  next = recordOutreach(next, work.esrId, appointmentId, 'phone', 'answered', 'Reached synthetic claimant.');
  next = recordAppointmentLogistics(next, appointmentId, localLogistics);
  next = confirmAppointment(next, appointmentId);
  const exam = next.examinationsById[next.appointmentsById[appointmentId].examinationId];
  const dbq = next.dbqsById[exam.requiredDbqIds[0]];
  const values = Object.fromEntries(dbqForm(dbq).filter(item => item.required).map(item => [item.key, item.options?.[0] ?? 'Synthetic response.']));
  next = saveDBQDraft(next, dbq.id, values);
  const draftRevision = next.dbqVersionsById[dbq.currentVersionId].version;
  assert.throws(() => completeClinicalReview(next, dbq.id, { versionId: dbq.currentVersionId, confirmed: true, draftRevision }), /completed encounter/i);
  next = recordExamEncounter(next, dbq.id, 'Synthetic encounter completed.');
  for (const orderId of exam.diagnosticOrderIds) {
    next = recordDiagnosticException(next, orderId, 'claimant-declined', 'Diagnostic exception recorded for the fixture.');
  }
  next = saveDBQDraft(next, dbq.id, { ...values, rationale: 'Updated after encounter.' });
  assert.throws(() => completeClinicalReview(next, dbq.id, { versionId: dbq.currentVersionId, confirmed: true, draftRevision }), /persisted report version/i);
  const currentVersion = next.dbqVersionsById[dbq.currentVersionId];
  assert.throws(() => completeClinicalReview(next, dbq.id, { versionId: currentVersion.id, confirmed: true }), /persisted report version/i);
  next = completeClinicalReview(next, dbq.id, { versionId: currentVersion.id, confirmed: true, draftRevision: currentVersion.version });
  assert.equal(next.dbqsById[dbq.id].signedVersionId, currentVersion.id);
});

test('closure waits for local task resolution and retains the submitted exact report revisions', () => {
  let state = seed();
  const pack = Object.values(state.resultsPackagesById).find(item => item.preparationStatus === 'prepared-locally' && item.validationStatus === 'passed');
  state = simulatePackageTransmission(state, pack.id);
  const submitted = state.resultsPackagesById[pack.id].submissions[0].manifest;
  assert.throws(() => closeESR(state, pack.esrId, 'Premature closure'), /contention|pending/i);
  state = recordSimulatedPackageResponse(state, pack.id, 'accepted', 'Synthetic acceptance.');
  state = createReconciliationTask(state, pack.id, 'Check the response and dispositions.');
  const task = Object.values(state.deliveryEventsById).find(item => item.packageId === pack.id && item.eventType === 'reconciliation-task-created');
  assert.throws(() => closeESR(state, pack.esrId, 'Premature closure'), /reconciliation/i);
  state = completeReconciliationTask(state, task.id, 'Compared the recorded manifest.');
  state = closeESR(state, pack.esrId, 'All local dispositions reconciled.');
  assert.deepEqual(state.resultsPackagesById[pack.id].submissions[0].manifest, submitted);
  assert.equal(state.resultsPackagesById[pack.id].preparationStatus, 'closed');
  assert.equal(state.esrsById[pack.esrId].workflowDisposition, 'completed');
});
