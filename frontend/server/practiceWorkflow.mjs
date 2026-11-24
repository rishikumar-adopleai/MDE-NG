import {
  addQAFinding, approveQAReview, completeClinicalReview, confirmAppointment, ensureResultsPackage, examinerEligibility,
  holdAppointment, prepareResultsPackage, recordAppointmentLogistics, recordExamEncounter, recordOutreach, requestCorrection,
  runQAChecklist, saveDBQDraft, simulatePackageTransmission, validateResultsPackage,
} from '../src/services/workflowService.ts';

const facilityId = 'practice-remote-examination';
const examinerId = 'practice-examination-clinician';
const day = 86_400_000;
const hour = 3_600_000;

export function refreshPracticeSlots(state) {
  const examiner = state.examinersById[examinerId];
  if (!examiner?.practiceOnly) return;
  const midnight = Math.floor(Date.now() / day) * day;
  examiner.availableSlots = Array.from({ length: 56 }, (_, index) => {
    const starts = midnight + Math.floor(index / 2) * day + (14 + (index % 2) * 3) * hour;
    return { startsAt: new Date(starts).toISOString(), endsAt: new Date(starts + hour).toISOString(),
      facilityId, modality: 'tele-c-and-p' };
  });
}

export function preparePracticeWorkflow(state, targets) {
  const now = new Date().toISOString();
  const base = { version: 1, createdAt: now, updatedAt: now, status: 'active' };
  const services = ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'];
  const scopes = targets.map(({ requirementId }) => state.examinationRequirementsById[requirementId]);
  const unique = values => [...new Set(values)];
  state.facilitiesById[facilityId] = { ...base, id: facilityId, name: 'Remote Examination Clinic', city: '', stateCode: '',
    postalCode: '', latitude: 0, longitude: 0, accessibilityFeatures: [], serviceGroups: services };
  state.examinersById[examinerId] = { ...base, id: examinerId, displayName: 'Practice examination clinician', practiceOnly: true,
    specialties: unique(scopes.flatMap(scope => scope.requiredSpecialties)), credentials: unique(scopes.flatMap(scope => scope.requiredCredentials)),
    supportedExamTypes: unique(scopes.map(scope => scope.examinationType)), training: [{ course: 'Practice examination workflow', status: 'current' }],
    facilityIds: [facilityId], availableSlots: [], permittedModalities: ['tele-c-and-p'], eligible: true, restrictionReasons: [], serviceGroups: services };
  refreshPracticeSlots(state);

  for (const { stage, variant, requirementId } of targets) {
    if (stage === 'Scheduling' && variant === 0) continue;
    const requirement = state.examinationRequirementsById[requirementId];
    let appointment = Object.values(state.appointmentsById).find(entry => entry.esrId === requirement.esrId);
    if (!appointment) {
      const slot = state.examinersById[examinerId].availableSlots.find(candidate =>
        !examinerEligibility(state, requirementId, examinerId, candidate.modality, candidate));
      if (!slot) throw Error('The practice examination has no eligible slot.');
      const held = holdAppointment(state, requirementId, examinerId, slot);
      state = held.state;
      appointment = state.appointmentsById[held.appointmentId];
    }
    const appointmentId = appointment.id;
    if (appointment.status === 'held') {
      if (stage !== 'Scheduling' || variant === 2) {
        state = recordOutreach(state, requirement.esrId, appointmentId, 'phone', 'answered', 'Practice contact completed and slot accepted.');
      }
      if (stage !== 'Scheduling' || variant === 3) {
        state = recordAppointmentLogistics(state, appointmentId, { specialTransportRequired: false, specialTransportStatus: 'not-required',
          consentStatus: 'not-required', vaPreapprovalStatus: 'not-required', note: 'Remote examination. Travel and transport are not applicable.' });
      }
      if (stage !== 'Scheduling') state = confirmAppointment(state, appointmentId);
    }
    if (stage === 'Scheduling') continue;
    if (stage === 'Clinical Evaluation' && variant === 0) continue;

    const dbq = state.dbqsById[state.examinationRequirementsById[requirementId].dbqIds[0]];
    const booked = state.appointmentsById[appointmentId];
    if (booked.status === 'confirmed-locally') {
      const priorStart = booked.startsAt;
      booked.startsAt = new Date(Date.now() - day - hour).toISOString();
      booked.endsAt = new Date(Date.now() - day).toISOString();
      for (const notice of Object.values(state.communicationsById)) {
        if (notice.appointmentId === appointmentId && notice.channel === 'mail') {
          notice.summary = notice.summary.replace(priorStart, booked.startsAt);
        }
      }
      state = recordExamEncounter(state, dbq.id, 'Practice encounter documented for review of the examination workflow.');
      state = saveDBQDraft(state, dbq.id, {
        history: 'Reviewed the practice request and documented the condition referred for examination.',
        findings: 'The practice packet contains no examination measurements.',
        rationale: 'The packet establishes the requested examination scope. Clinical conclusions require an examination and supporting evidence.',
        recordsReviewed: 'Examination request review note linked to this contention.',
        diagnosisOutcome: 'Could not be established', diagnosisDetails: 'No clinical measurements or findings are available in this practice packet.',
      });
      if (stage === 'Clinical Evaluation' && variant < 3) continue;
      const version = state.dbqVersionsById[state.dbqsById[dbq.id].currentVersionId];
      state = completeClinicalReview(state, dbq.id, {versionId: version.id, confirmed: true, draftRevision: version.version}, 'practice-clinician');
    }
    const review = Object.values(state.qaReviewsById).find(entry => entry.dbqId === dbq.id);
    if (stage === 'Internal QA' && variant === 0) continue;
    if (review.status !== 'approved') state = runQAChecklist(state, review.id).state;
    if (stage === 'Clinical Evaluation' || stage === 'Internal QA') {
      if (stage === 'Clinical Evaluation' || variant >= 2) {
        const finding = addQAFinding(state, review.id, { title: 'Clarify the source reference', sectionReference: 'Records reviewed',
          issueCategory: 'evidence-consistency', severity: variant === 3 && stage === 'Internal QA' ? 'advisory' : 'blocking',
          comment: 'Identify the source section that establishes the requested examination scope.' });
        state = stage === 'Clinical Evaluation' ? requestCorrection(finding.state, finding.findingId, 'Add the precise source section before resubmission.') : finding.state;
      }
      continue;
    }
    if (review.status !== 'approved') state = approveQAReview(state, review.id, 'Practice report reviewed for consistency with the supplied case material.', 'practice-reviewer');
    state = ensureResultsPackage(state, requirement.esrId);
    const pack = Object.values(state.resultsPackagesById).find(entry => entry.esrId === requirement.esrId);
    if (variant >= 1 && pack.preparationStatus === 'draft') state = validateResultsPackage(state, pack.id);
    if (variant >= 2 && state.resultsPackagesById[pack.id].preparationStatus === 'ready-for-review') state = prepareResultsPackage(state, pack.id);
    const prepared = state.resultsPackagesById[pack.id];
    if (variant === 3 && prepared.preparationStatus === 'prepared-locally' && (!prepared.simulatedResponse || prepared.simulatedResponse === 'not-sent')) {
      state = simulatePackageTransmission(state, pack.id);
    }
  }
  return state;
}
