import {
  WORKSPACE_SCHEMA_VERSION,
  stageKeys,
  type AIInsight,
  type ActivityEvent,
  type Appointment,
  type Contention,
  type DBQ,
  type DBQVersion,
  type DiagnosticOrder,
  type DiagnosticResult,
  type ESR,
  type EvidenceDocument,
  type Examiner,
  type Examination,
  type ExaminationRequirement,
  type Facility,
  type IntakeException,
  type QAReview,
  type QAFinding,
  type ResultsPackage,
  type Veteran,
  type WorkspaceState,
  type WorkItem,
} from '../../domain/model.ts';
import { refreshWorkItems } from '../../domain/selectors.ts';

const baseTime = Date.now();
const iso = (minutes: number) => new Date(baseTime + minutes * 60_000).toISOString();
const names = [
  ['Avery', 'Bennett'], ['Morgan', 'Brooks'], ['Riley', 'Campbell'], ['Jordan', 'Carter'],
  ['Taylor', 'Chen'], ['Casey', 'Coleman'], ['Reese', 'Davis'], ['Cameron', 'Diaz'],
  ['Parker', 'Edwards'], ['Quinn', 'Ellis'], ['Jamie', 'Foster'], ['Drew', 'Garcia'],
  ['Rowan', 'Green'], ['Skyler', 'Harris'], ['Devon', 'Hayes'], ['Alex', 'Hughes'],
  ['Kendall', 'Jackson'], ['Emerson', 'James'], ['Logan', 'Kelly'], ['Finley', 'Kim'],
  ['Micah', 'Lewis'], ['Sage', 'Martin'], ['Elliot', 'Miller'], ['Harper', 'Moore'],
  ['Noah', 'Nelson'], ['Ari', 'Ortiz'], ['Robin', 'Parker'], ['Shawn', 'Patel'],
  ['Jules', 'Perez'], ['Sam', 'Powell'], ['Blair', 'Reed'], ['Remy', 'Rivera'],
  ['Luca', 'Roberts'], ['Milan', 'Sanders'], ['Noel', 'Scott'], ['Arden', 'Smith'],
  ['Terry', 'Turner'], ['Ellis', 'Ward'], ['Frankie', 'Washington'], ['Ash', 'Young'],
] as const;
const serviceGroups = ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'] as const;
const conditions = [
  { title: 'Knee condition', code: '5260', specialty: 'Orthopedics', exam: 'Knee and Lower Leg', dbq: 'Knee and Lower Leg Conditions DBQ' },
  { title: 'Hearing loss', code: '6100', specialty: 'Audiology', exam: 'Hearing Loss and Tinnitus', dbq: 'Hearing Loss and Tinnitus DBQ' },
  { title: 'Back condition', code: '5237', specialty: 'Orthopedics', exam: 'Back (Thoracolumbar Spine)', dbq: 'Back Conditions DBQ' },
  { title: 'Mental health condition', code: '9411', specialty: 'Psychology', exam: 'Mental Disorders', dbq: 'Mental Disorders DBQ' },
  { title: 'Migraine condition', code: '8100', specialty: 'Neurology', exam: 'Headaches', dbq: 'Headaches DBQ' },
  { title: 'Skin condition', code: '7806', specialty: 'Dermatology', exam: 'Skin Diseases', dbq: 'Skin Diseases DBQ' },
  { title: 'Peripheral nerve condition', code: '8520', specialty: 'Neurology', exam: 'Peripheral Nerves', dbq: 'Peripheral Nerves DBQ' },
  { title: 'Shoulder condition', code: '5201', specialty: 'Orthopedics', exam: 'Shoulder and Arm', dbq: 'Shoulder and Arm Conditions DBQ' },
] as const;

const facilities: Facility[] = [
  ['fac-cle', 'Lakefront Examination Center', 'Cleveland', 'OH', 41.4993, -81.6944, ['step-free access', 'accessible parking']],
  ['fac-col', 'Central Ohio Clinic', 'Columbus', 'OH', 39.9612, -82.9988, ['step-free access', 'hearing loop']],
  ['fac-pit', 'Three Rivers Medical Pavilion', 'Pittsburgh', 'PA', 40.4406, -79.9959, ['step-free access', 'accessible parking']],
  ['fac-det', 'Great Lakes Health Center', 'Detroit', 'MI', 42.3314, -83.0458, ['step-free access', 'hearing loop']],
  ['fac-ind', 'Circle City Examination Center', 'Indianapolis', 'IN', 39.7684, -86.1581, ['step-free access', 'accessible parking']],
  ['fac-chi', 'Midwest Specialty Clinic', 'Chicago', 'IL', 41.8781, -87.6298, ['step-free access', 'quiet room']],
].map(([id, name, city, stateCode, latitude, longitude, accessibilityFeatures], index) => ({
  id: String(id), version: 1, createdAt: iso(0), updatedAt: iso(0), name: String(name), city: String(city), stateCode: String(stateCode),
  postalCode: ['44114', '43215', '15222', '48226', '46204', '60601'][index], latitude: Number(latitude), longitude: Number(longitude),
  accessibilityFeatures: accessibilityFeatures as string[], serviceGroups: [...serviceGroups], status: 'active',
}));

const examiners: Examiner[] = [
  ['exam-001', 'Dr. Maya Collins', ['Orthopedics', 'Sports medicine'], ['MD', 'Board certified'], 'fac-cle', ['in-person', 'tele-c-and-p'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], true, []],
  ['exam-002', 'Dr. Adrian Brooks', ['Audiology'], ['AuD', 'State licensed'], 'fac-col', ['in-person', 'tele-c-and-p'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], true, []],
  ['exam-003', 'Dr. Priya Shah', ['Psychology', 'Mental health'], ['PhD', 'Licensed psychologist'], 'fac-pit', ['in-person', 'tele-c-and-p', 'ace-records-review'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], true, []],
  ['exam-004', 'Dr. Leon Grant', ['Neurology'], ['MD', 'Board certified'], 'fac-det', ['in-person', 'tele-c-and-p', 'ace-records-review'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], true, []],
  ['exam-005', 'Dr. Elena Ruiz', ['Dermatology'], ['MD', 'Board certified'], 'fac-ind', ['in-person', 'tele-c-and-p'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], true, []],
  ['exam-006', 'Dr. Owen Price', ['Orthopedics'], ['DO', 'Board certification pending'], 'fac-chi', ['in-person'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], false, ['Required specialty training is pending']],
  ['exam-007', 'Dr. Sofia Bennett', ['Orthopedics', 'Neurology'], ['MD', 'Board certified'], 'fac-col', ['in-person', 'tele-c-and-p', 'ace-records-review'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], true, []],
  ['exam-008', 'Dr. Marcus Lee', ['Audiology', 'Psychology'], ['AuD', 'State licensed'], 'fac-cle', ['in-person'], ['Army', 'Navy', 'Air Force', 'Marine Corps', 'Coast Guard', 'Space Force'], false, ['Audiology scope verification required']],
].map(([id, displayName, specialties, credentials, facilityId, permittedModalities, groups, eligible, restrictionReasons], index) => ({
  id: String(id), version: 1, createdAt: iso(0), updatedAt: iso(0), displayName: String(displayName), specialties: specialties as string[], credentials: credentials as string[],
  training: [{ course: 'MDE examination standards', status: index === 5 ? 'pending' : 'current' }, { course: 'Accessibility and accommodation', status: 'current' }],
  facilityIds: [String(facilityId)], supportedExamTypes: index === 1 ? ['Hearing Loss and Tinnitus'] : index === 2 ? ['Mental Disorders'] : index === 3 ? ['Headaches', 'Peripheral Nerves'] : index === 4 ? ['Skin Diseases'] : ['Knee and Lower Leg', 'Back (Thoracolumbar Spine)', 'Shoulder and Arm', 'Peripheral Nerves', 'Headaches'],
  availableSlots: Array.from({ length: 12 }, (_, slotIndex) => {
    const day = 1 + (slotIndex % 6) + Math.floor(slotIndex / 6) * 7;
    const hour = [9, 11, 14][(slotIndex + index) % 3];
    const start = 24 * 60 * day + hour * 60 + index * 7;
    return { startsAt: iso(start), endsAt: iso(start + 60), facilityId: String(facilityId), modality: (permittedModalities as string[])[slotIndex % (permittedModalities as string[]).length] as Examiner['permittedModalities'][number] };
  }),
  permittedModalities: permittedModalities as Examiner['permittedModalities'], eligible: Boolean(eligible), restrictionReasons: restrictionReasons as string[], serviceGroups: groups as Examiner['serviceGroups'], status: 'active',
}));

const empty = <T extends { id: string }>() => ({} as Record<string, T>);

export function createSeedWorkspace(): WorkspaceState {
  const state: WorkspaceState = {
    workspaceId: 'mde-ng-local-workspace', schemaVersion: WORKSPACE_SCHEMA_VERSION, revision: 0, schedulingPolicy: { maxTravelDistanceMiles: 500 }, initializedAt: iso(0), updatedAt: iso(0),
    veteransById: empty<Veteran>(), esrsById: empty<ESR>(), contentionsById: empty<Contention>(), examinationRequirementsById: empty<ExaminationRequirement>(),
    examinationsById: empty<Examination>(), appointmentsById: empty<Appointment>(), examinersById: empty<Examiner>(), facilitiesById: empty<Facility>(),
    dbqsById: empty<DBQ>(), dbqVersionsById: empty<DBQVersion>(), diagnosticOrdersById: empty<DiagnosticOrder>(), diagnosticResultsById: empty<DiagnosticResult>(),
    evidenceDocumentsById: empty<EvidenceDocument>(), intakeExceptionsById: empty<IntakeException>(), communicationsById: empty(), outreachAttemptsById: empty(),
    qaReviewsById: empty<QAReview>(), qaFindingsById: empty<QAFinding>(), correctionRequestsById: empty(), resultsPackagesById: empty<ResultsPackage>(),
    deliveryEventsById: empty(), aiInsightsById: empty(), workItemsById: empty(), activitiesById: empty(),
  };

  facilities.forEach((item) => { state.facilitiesById[item.id] = item; });
  examiners.forEach((item) => { state.examinersById[item.id] = item; });

  for (let i = 1; i <= 150; i += 1) {
    const suffix = String(i).padStart(5, '0');
    const esrId = `esr-2026-${suffix}`;
    const veteranId = `vet-2026-${suffix}`;
    const [first, last] = names[(i * 13) % names.length];
    const middle = ['A.', 'J.', 'M.', 'R.', 'T.'][i % 5];
    const facility = facilities[i % facilities.length];
    const receivedAt = iso(-Math.floor(i * 103));
    const daysOld = Math.floor(i / 10) + 3;
    const lifecycle = i <= 10 ? 'intake' : i <= 25 ? 'processing' : i <= 40 ? 'triage-queue' : i <= 60 ? 'triage' : i <= 78 ? 'scheduling' : i <= 99 ? 'clinical' : i <= 119 ? 'qa-correction' : i <= 139 ? 'delivery' : 'reconciliation';
    const contentionCount = 1 + ((i * 5) % 3);
    const contentionIds = Array.from({ length: contentionCount }, (_, n) => `cnt-${suffix}-${n + 1}`);
    const evidenceCount = 2 + (i % 4);
    const evidenceDocumentIds = Array.from({ length: evidenceCount }, (_, n) => `doc-${suffix}-${n + 1}`);
    const priority = i % 19 === 0 ? 'urgent' : i % 5 === 0 ? 'elevated' : 'routine';
    const intakeStatus = lifecycle === 'intake' ? (i % 2 === 0 ? 'requires-attention' : 'new') : lifecycle === 'processing' ? 'processing' : 'ready-for-triage';
    const esr: ESR = {
      id: esrId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, veteranId, receivedAt,
      dueAt: iso(-Math.floor(i * 95) + 21 * 1440), contentionIds, evidenceDocumentIds, priority, intakeStatus,
      assignedTeamId: i % 6 === 0 ? undefined : `team-${(i % 4) + 1}`, externalState: 'not-enabled', sourceReference: `VA-REQ-${20260000 + i}`,
      receiptStatus: 'received', receiptReference: `LOCAL-ESR-${suffix}`, aepRecordedAt: iso(-Math.floor(i * 103) + 1),
    };
    state.esrsById[esrId] = esr;
    state.veteransById[veteranId] = {
      id: veteranId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, displayName: `${last}, ${first} ${middle}`,
      initials: `${first[0]}${last[0]}`, claimIdentifier: `CLM-${String(47000 + i).padStart(5, '0')}-${String((i * 37) % 10000).padStart(4, '0')}`,
      serviceGroup: serviceGroups[i % serviceGroups.length], homeFacilityId: facility.id, city: facility.city, stateCode: facility.stateCode,
      accessibilityNeeds: i % 13 === 0 ? ['step-free access'] : i % 29 === 0 ? ['hearing loop'] : [],
    };

    evidenceDocumentIds.forEach((documentId, index) => {
      const pageCount = 18 + ((i * 11 + index * 23) % 155);
      const unreadablePageCount = lifecycle === 'intake' && index === 0 && i % 3 !== 0 ? 2 + (i % 5) : 0;
      const classificationStatus = unreadablePageCount > 0 ? 'needs-human-review' : lifecycle === 'processing' && index === evidenceCount - 1 ? 'processing' : 'indexed';
      const doc: EvidenceDocument = {
        id: documentId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, title: ['Service treatment record', 'Hospital record', 'Laboratory results', 'Prior examination', 'Imaging report', 'VA request'][index % 6],
        category: ['service-treatment', 'hospital', 'laboratory', 'prior-examination', 'imaging', 'va-request'][index % 6] as EvidenceDocument['category'],
        sourceReference: `${['STR', 'HOSP', 'LAB', 'PRIOR', 'IMG', 'VA-REQ'][index % 6]}-${String(i * 10 + index + 1).padStart(4, '0')}.pdf · pp. ${1 + index * 3}-${3 + index * 3}`,
        pageCount, classifiedPageCount: classificationStatus === 'indexed' ? pageCount - unreadablePageCount : Math.max(0, Math.floor(pageCount * 0.75)), unreadablePageCount,
        classificationStatus, contentionIds: contentionIds.slice(index % contentionCount, Math.min(contentionCount, (index % contentionCount) + 2)),
        quality: {
          fileIntegrity: 'passed',
          sha256: `sha256-${documentId}`,
          readabilityScore: unreadablePageCount > 0 ? 80 : 100,
          qualityStatus: unreadablePageCount > 0 ? 'needs-review' : 'passed',
          readablePages: classificationStatus === 'indexed' ? pageCount - unreadablePageCount : Math.max(0, Math.floor(pageCount * 0.75)),
          totalExpectedPages: pageCount,
          claimantMatch: 'aligned',
          ocrApplied: index % 3 === 0,
          ocrConfidence: 96,
        },
        ...(index % 6 === 2 ? {
          extractedData: {
            labPanels: [
              {
                panelName: 'Complete Blood Count (CBC) with Differential',
                collectionDate: '2024-03-14',
                specimen: 'Whole Blood',
                orderingProvider: 'VA Medical Center Outpatient Clinic',
                results: [
                  { name: 'White Blood Cell Count (WBC)', value: '7.4', units: '10*3/uL', referenceRange: '4.0 - 11.0', flag: 'normal' },
                  { name: 'Red Blood Cell Count (RBC)', value: '4.85', units: '10*6/uL', referenceRange: '4.30 - 5.90', flag: 'normal' },
                  { name: 'Hemoglobin (Hgb)', value: '14.8', units: 'g/dL', referenceRange: '13.5 - 17.5', flag: 'normal' },
                  { name: 'Hematocrit (Hct)', value: '44.2', units: '%', referenceRange: '38.8 - 50.0', flag: 'normal' },
                  { name: 'Platelet Count', value: '235', units: '10*3/uL', referenceRange: '150 - 450', flag: 'normal' },
                  { name: 'Fasting Blood Glucose', value: '108', units: 'mg/dL', referenceRange: '70 - 99', flag: 'abnormal' },
                  { name: 'Serum Creatinine', value: '1.02', units: 'mg/dL', referenceRange: '0.70 - 1.30', flag: 'normal' },
                  { name: 'Estimated GFR (eGFR)', value: '86', units: 'mL/min', referenceRange: '> 60', flag: 'normal' },
                ],
              },
            ],
          },
        } : {}),
      };
      state.evidenceDocumentsById[documentId] = doc;
      if (unreadablePageCount > 0) {
        const exceptionId = `issue-${documentId}`;
        state.intakeExceptionsById[exceptionId] = {
          id: exceptionId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, documentId, kind: 'unreadable-pages',
          title: 'Unreadable pages', detail: `${unreadablePageCount} pages need staff review before intake validation.`, sourceReference: doc.sourceReference, status: 'open',
        };
      }
    });

    const missingIndicator = lifecycle === 'intake' && i % 5 === 0;
    if (missingIndicator) {
      const issueId = `issue-missing-${suffix}`;
      state.intakeExceptionsById[issueId] = {
        id: issueId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, kind: 'missing-document',
        title: 'Missing-document indicator', detail: 'A referenced record may be absent. Confirm the request and document the follow-up.', sourceReference: esr.sourceReference, status: 'open',
      };
    }
    if (lifecycle === 'intake' && i % 4 === 0) {
      const issueId = `issue-duplicate-${suffix}`;
      state.intakeExceptionsById[issueId] = {
        id: issueId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, kind: 'possible-duplicate',
        title: 'Possible duplicate document', detail: 'Compare the linked source references and record whether they represent the same document.',
        sourceReference: state.evidenceDocumentsById[evidenceDocumentIds[0]]?.sourceReference, status: 'open',
      };
    }

    const selectedConditions = Array.from({ length: contentionCount }, (_, n) => conditions[(i * 3 + n * 2) % conditions.length]);
    const records = selectedConditions.map((condition, n) => {
      const contentionId = contentionIds[n];
      let status: Contention['status'] = 'awaiting-review';
      if (lifecycle === 'triage') status = n === 0 ? 'approved-for-exam' : n === 1 && i % 2 === 0 ? 'clarification-required' : 'in-review';
      if (['scheduling', 'clinical', 'qa-correction', 'delivery', 'reconciliation'].includes(lifecycle)) status = n === contentionCount - 1 && i % 3 === 0 ? 'clarification-required' : 'approved-for-exam';
      if (lifecycle === 'delivery' || lifecycle === 'reconciliation') status = 'approved-for-exam';
      const relatedDocs = evidenceDocumentIds.filter((_, docIndex) => (docIndex + n) % 2 === 0 || docIndex === 0);
      const contention: Contention = {
        id: contentionId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, title: condition.title, simulatedDisposition: 'pending',
        diagnosticCode: condition.code, evidenceDocumentIds: relatedDocs, examinationRequirementIds: [], status,
        clarificationReason: status === 'clarification-required' ? 'Confirm the evidence reference and examination scope.' : undefined,
        reviewerRationale: status === 'approved-for-exam' ? 'Records support examination planning; final scope confirmed by authorized reviewer.' : undefined,
      };
      state.contentionsById[contentionId] = contention;

      const hasRequirement = status === 'approved-for-exam' && lifecycle !== 'triage-queue';
      if (!hasRequirement) return { contention, condition, requirementId: undefined as string | undefined, examinationId: undefined as string | undefined };
      const requirementId = `req-${suffix}-${n + 1}`;
      const examinationId = `exam-${suffix}-${n + 1}`;
      const modalityOptions: ExaminationRequirement['permittedModalities'] = condition.specialty === 'Audiology' ? ['in-person', 'tele-c-and-p'] : ['in-person', 'tele-c-and-p', 'ace-records-review'];
      const requirementStatus: ExaminationRequirement['status'] = ['triage', 'scheduling', 'clinical', 'qa-correction', 'delivery', 'reconciliation'].includes(lifecycle) ? 'approved' : 'awaiting-review';
      const requirement: ExaminationRequirement = {
        id: requirementId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, contentionIds: [contentionId],
        title: `${condition.exam} examination`, examinationType: condition.exam, requiredSpecialties: [condition.specialty], requiredCredentials: condition.specialty === 'Audiology' ? ['AuD', 'State licensed'] : condition.specialty === 'Psychology' ? ['Licensed psychologist'] : ['Board certified'], permittedModalities: modalityOptions,
        selectedModality: lifecycle === 'clinical' || lifecycle === 'qa-correction' || lifecycle === 'delivery' || lifecycle === 'reconciliation' ? modalityOptions[0] : undefined,
        requestedDbqTypes: [condition.dbq, ...(((i + n) % 4 === 0) ? ['Medical Opinion DBQ'] : [])],
        requestedDiagnostics: condition.specialty === 'Orthopedics' ? ['Imaging report review'] : condition.specialty === 'Audiology' ? ['Audiology test'] : condition.specialty === 'Neurology' ? ['Diagnostic study review'] : [],
        dbqIds: [], diagnosticOrderIds: [], status: requirementStatus, reviewerRationale: contention.reviewerRationale,
      };
      state.examinationRequirementsById[requirementId] = requirement;
      contention.examinationRequirementIds.push(requirementId);

      const requirementHasExam = requirementStatus === 'approved';
      if (!requirementHasExam) return { contention, condition, requirementId, examinationId: undefined as string | undefined };
      const needsExaminer = ['clinical', 'qa-correction', 'delivery', 'reconciliation'].includes(lifecycle) || (lifecycle === 'scheduling' && (n + i) % 2 === 0);
      const examinerId = needsExaminer ? `exam-${String(((i + n) % 6) + 1).padStart(3, '0')}` : undefined;
      const hasAppointment = Boolean(examinerId);
      const dbqCount = (i + n) % 4 === 0 ? 2 : 1;
      const requiredDbqIds = Array.from({ length: dbqCount }, (_, dbqIndex) => `dbq-${suffix}-${n + 1}-${dbqIndex + 1}`);
      const diagnosticOrderId = `diag-order-${suffix}-${n + 1}`;
      const examStatus: Examination['status'] = lifecycle === 'scheduling' ? (hasAppointment ? 'scheduled' : 'awaiting-scheduling') : lifecycle === 'clinical' ? 'awaiting-clinical-review' : lifecycle === 'qa-correction' ? 'correction-required' : 'ready-for-qa';
      const examination: Examination = {
        id: examinationId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, requirementIds: [requirementId], contentionIds: [contentionId],
        requiredDbqIds, examinerId, appointmentIds: [], diagnosticOrderIds: [diagnosticOrderId], status: examStatus, assignedAt: examinerId ? iso(-3000 + i * 4) : undefined,
      };
      state.examinationsById[examinationId] = examination;

      const historicalApproval = lifecycle === 'delivery' || lifecycle === 'reconciliation';
      const diagnosticOrder: DiagnosticOrder = {
        id: diagnosticOrderId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, examinationId, requirementId,
        orderType: condition.specialty === 'Audiology' ? 'Audiology test' : 'Diagnostic study', status: historicalApproval ? 'reviewed' : (i + n) % 3 === 0 ? 'result-available' : 'ordered', required: (i + n) % 4 !== 0,
        sourceReference: `DIAG-${suffix}-${n + 1}`, resultIds: [],
      };
      state.diagnosticOrdersById[diagnosticOrderId] = diagnosticOrder;
      if (['result-available','reviewed'].includes(diagnosticOrder.status)) {
        const resultId = `diag-result-${suffix}-${n + 1}`;
        const result: DiagnosticResult = {
          id: resultId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, orderId: diagnosticOrderId, examinationId,
          summary: historicalApproval ? 'Synthetic diagnostic summary reviewed by the examiner.' : 'Result is available for clinician review.',
          sourceReference: `${diagnosticOrder.sourceReference} · result page 1`, reviewedAt: historicalApproval ? iso(-100+i) : undefined,
          reviewedBy: historicalApproval ? (examinerId ?? 'user-clinician') : undefined,
        };
        state.diagnosticResultsById[resultId] = result;
        diagnosticOrder.resultIds.push(resultId);
      }
      requirement.diagnosticOrderIds.push(diagnosticOrderId);

      if (hasAppointment && examinerId) {
        const appointmentId = `appt-${suffix}-${n + 1}`;
        const startAt = iso(60 * (24 + ((i + n) % 14)) + (i % 8) * 60);
        const examiner = state.examinersById[examinerId];
        const facilityId = examiner.facilityIds[0];
        const appointment: Appointment = {
          id: appointmentId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, examinationId, examinerId, facilityId,
          startsAt: startAt, endsAt: iso(60 * (24 + ((i + n) % 14)) + (i % 8) * 60 + 60), modality: requirement.permittedModalities[0],
          status: historicalApproval ? 'completed' : 'confirmed-locally', accessibilityNeeds: [], confirmationNote: 'Appointment confirmation recorded; no external notice sent.',
          localLogistics: { travelDistanceMiles: 0, travelLimitMiles: 500, specialTransportRequired: false, specialTransportStatus: 'not-required', consentStatus: 'simulated-recorded', vaPreapprovalStatus: 'simulated-recorded', note: 'Seeded local scheduling checks for this synthetic appointment.', recordedAt: iso(-119 + i) },
          history: [{ status: historicalApproval ? 'completed' : 'confirmed-locally', at: iso(-120 + i), actorId: 'user-operations', note: 'Seeded appointment record.' }],
        };
        state.appointmentsById[appointmentId] = appointment;
        examination.appointmentIds.push(appointmentId);
      }

      requiredDbqIds.forEach((dbqId, dbqIndex) => {
        const versionId = `dbqver-${suffix}-${n + 1}-${dbqIndex + 1}-1`;
        const dbqValues: DBQ['values'] = lifecycle === 'qa-correction' || lifecycle === 'delivery' || lifecycle === 'reconciliation'
          ? { history: `${condition.title} history reviewed.`, findings: 'Examination findings recorded for clinician review.', rationale: dbqIndex === 0 ? 'Clinical rationale requires additional detail.' : 'Evidence references reviewed and considered.', impact: dbqIndex === 0 ? 'Current functional impact documented.' : '' }
          : lifecycle === 'clinical'
            ? { history: dbqIndex === 0 ? 'History reviewed with the claimant.' : '', findings: '', rationale: '', impact: '' }
            : {};
        const reviewedLocally = lifecycle === 'qa-correction' || lifecycle === 'delivery' || lifecycle === 'reconciliation';
        const approved = lifecycle === 'delivery' || lifecycle === 'reconciliation';
        const awaitingQA = i === 100 && n === 0 && dbqIndex === 0;
        const dbqStatus: DBQ['status'] = approved ? 'approved-for-package' : awaitingQA ? 'ready-for-qa' : lifecycle === 'qa-correction' ? 'correction-required' : lifecycle === 'clinical' ? 'in-progress' : 'draft';
        const dbq: DBQ = {
          id: dbqId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, examinationId, esrId, contentionIds: [contentionId], formType: dbqIndex === 0 ? condition.dbq : 'Medical Opinion DBQ',
          formVersion: dbqIndex === 0 ? 'v25.1' : 'v24.3', values: dbqValues, versionIds: [versionId], currentVersionId: versionId, status: dbqStatus,
          validationErrors: [], localReviewState: reviewedLocally ? 'review-recorded' : 'not-ready', reviewerId: reviewedLocally ? 'user-clinician' : undefined, reviewedAt: reviewedLocally ? iso(-100 + i) : undefined,
          signedVersionId: reviewedLocally ? versionId : undefined,
        };
        state.dbqsById[dbqId] = dbq;
        state.examinationRequirementsById[requirementId].dbqIds.push(dbqId);
        const version: DBQVersion = {
          id: versionId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, dbqId, versionNumber: 1, formVersion: dbq.formVersion, values: { ...dbqValues },
          authoredBy: examinerId ?? 'user-clinician', changeSummary: 'Initial documentation draft.', createdFor: 'draft',
        };
        state.dbqVersionsById[versionId] = version;

        if (['qa-correction', 'delivery', 'reconciliation'].includes(lifecycle)) {
          const qaReviewId = `qa-${dbqId}`;
          const findingId = `finding-${dbqId}-rationale`;
          const correctedOrApproved = lifecycle === 'delivery' || lifecycle === 'reconciliation';
          const finding: QAFinding = {
            id: findingId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, qaReviewId, dbqId, sectionReference: 'Clinical rationale',
            issueCategory: 'incomplete-rationale', severity: 'blocking', title: correctedOrApproved ? 'Clinical rationale verified' : 'Expand the clinical rationale', supportingEvidenceIds: relatedDocs,
            reviewerId: 'user-qa', status: correctedOrApproved ? 'resolved' : 'open', comments: correctedOrApproved ? ['Revised rationale reviewed against cited records.'] : [],
            resolutionHistory: correctedOrApproved ? [{ at: iso(-80 + i), actorId: 'user-qa', status: 'resolved', comment: 'Reviewed revised documentation.' }] : [],
          };
          const qaReview: QAReview = {
            id: qaReviewId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, dbqId, versionId, reviewerId: 'user-qa', findingIds: [findingId],
            status: correctedOrApproved ? 'approved' : awaitingQA ? 'queued' : 'correction-requested', reviewerComment: correctedOrApproved ? 'Review completed.' : 'Please add rationale detail linked to the reviewed evidence.', approvedAt: correctedOrApproved ? iso(-60 + i) : undefined,
          };
          state.qaReviewsById[qaReviewId] = qaReview;
          state.qaFindingsById[findingId] = finding;
          if (lifecycle === 'qa-correction' && !awaitingQA) {
            dbq.status = 'correction-required';
            const correctionId = `correction-${dbqId}`;
            state.correctionRequestsById[correctionId] = {
              id: correctionId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, dbqId, qaReviewId, findingIds: [findingId], assignedTo: examinerId ?? 'user-clinician', requestedBy: 'user-qa', status: 'open',
              requestComment: 'Expand the rationale and reference the evidence considered.', history: [{ at: iso(-60 + i), status: 'open', note: 'Correction requested by QA.', actorId: 'user-qa' }],
            };
          }
        }
      });
      return { contention, condition, requirementId, examinationId };
    });

    if (['qa-correction', 'delivery', 'reconciliation'].includes(lifecycle)) {
      const dbqIds = records.flatMap((record) => record.examinationId ? state.examinationsById[record.examinationId].requiredDbqIds : []);
      if (dbqIds.length > 0) {
        const packageId = `pkg-${suffix}`;
        const resultsPackage: ResultsPackage = {
          id: packageId, version: 1, createdAt: receivedAt, updatedAt: receivedAt, esrId, dbqIds,
          diagnosticResultIds: Object.values(state.diagnosticResultsById).filter((result) => dbqIds.some((id) => state.dbqsById[id].examinationId === result.examinationId)).map((result) => result.id),
          qaApprovalIds: dbqIds.flatMap((dbqId) => Object.values(state.qaReviewsById).filter((review) => review.dbqId === dbqId && review.status === 'approved').map((review) => review.id)),
          validationStatus: dbqIds.every((id) => state.dbqsById[id].status === 'approved-for-package') ? 'passed' : 'blocked',
          preparationStatus: dbqIds.every((id) => state.dbqsById[id].status === 'approved-for-package') ? (lifecycle === 'reconciliation' ? 'prepared-locally' : 'ready-for-review') : 'blocked',
          externalState: 'not-enabled', blockingReasons: dbqIds.filter((id) => state.dbqsById[id].status !== 'approved-for-package').map((id) => `${state.dbqsById[id].formType} is not QA approved`), versionNumber: 1, simulatedResponse: 'not-sent',
        };
        state.resultsPackagesById[packageId] = resultsPackage;
      }
    }
  }

  Object.values(state.evidenceDocumentsById).forEach((document) => {
    document.syntheticExcerpts = document.contentionIds.slice(0, 3).map((contentionId, index) => {
      const contention = state.contentionsById[contentionId];
      return {
        id: `excerpt-${document.id}-${contentionId}`,
        pageNumber: Math.min(document.pageCount, 1 + index * 2),
        contentionId,
        text: `Synthetic training excerpt: this ${document.category.replaceAll('-', ' ')} record is linked to the ${contention?.title ?? 'selected'} contention for human review. It does not contain a clinical conclusion.`,
      };
    });
  });

  for (let i = 1; i <= 150; i += 1) {
    const esrId = `esr-2026-${String(i).padStart(5, '0')}`;
    const esr = state.esrsById[esrId];
    const openIssues = Object.values(state.intakeExceptionsById).filter((item) => item.esrId === esrId && ['open', 'escalated'].includes(item.status)).length;
    const caseContentions = esr.contentionIds.map((id) => state.contentionsById[id]).filter(Boolean);
    const caseRequirements = Object.values(state.examinationRequirementsById).filter((item) => item.esrId === esrId);
    const caseExams = Object.values(state.examinationsById).filter((item) => item.esrId === esrId);
    const caseDbqs = Object.values(state.dbqsById).filter((item) => item.esrId === esrId);
    const caseQA = Object.values(state.qaReviewsById).filter((item) => item.esrId === esrId);
    const casePackages = Object.values(state.resultsPackagesById).filter((item) => item.esrId === esrId);
    const summaries: Record<(typeof stageKeys)[number], { title: string; explanation: string; entityId: string; rules: string[] }> = {
      Intake: { title: `${openIssues} intake exception${openIssues === 1 ? '' : 's'} require disposition`, explanation: `Structured manifest rules found ${openIssues} open intake exception(s) for this ESR.`, entityId: esrId, rules: ['Count open or escalated IntakeException records', 'Check EvidenceDocument classification status'] },
      'Medical Triage': { title: `${caseContentions.filter((item) => item.status === 'awaiting-review').length} contentions await review`, explanation: 'Contention status and linked EvidenceDocument references are summarized for staff review.', entityId: esr.contentionIds[0] ?? esrId, rules: ['Count contentions with awaiting-review status', 'Use only linked evidence references'] },
      Scheduling: { title: `${caseRequirements.filter((item) => item.status === 'approved').length} approved examination requirements`, explanation: 'Approved ExaminationRequirement records are available for eligible-provider matching.', entityId: caseRequirements[0]?.id ?? esrId, rules: ['Read permitted modality list', 'Require active examiner and current training', 'Check examiner time-slot conflicts'] },
      'Clinical Evaluation': { title: `${caseDbqs.filter((item) => item.status !== 'approved-for-package').length} DBQs remain in clinical workflow`, explanation: 'DBQ status and validation errors indicate documentation work that may remain.', entityId: caseDbqs[0]?.id ?? esrId, rules: ['Read DBQ validationErrors', 'Check required diagnostic status', 'Do not infer clinical findings'] },
      'Internal QA': { title: `${caseQA.filter((item) => item.status !== 'approved').length} QA reviews need disposition`, explanation: 'QAReview and QAFinding statuses are counted without making reviewer decisions.', entityId: caseQA[0]?.id ?? esrId, rules: ['Count non-approved QAReview records', 'Keep blocking findings separate from advisory findings'] },
      'VA Delivery & Review': { title: casePackages.length ? `${casePackages.length} package record${casePackages.length === 1 ? '' : 's'} linked` : 'No results package exists', explanation: 'Package readiness is computed from DBQ approval and validation state. External connection remains disabled.', entityId: casePackages[0]?.id ?? esrId, rules: ['Check linked DBQ QA status', 'Never infer external transmission or receipt'] },
    };
    stageKeys.forEach((stage) => {
      const insight: AIInsight = {
        id: `insight-${stage.toLowerCase().replace(/[^a-z]+/g, '-')}-${esrId}`, version: 1, createdAt: esr.receivedAt, updatedAt: esr.receivedAt,
        esrId, entityId: summaries[stage].entityId, stage, title: summaries[stage].title, explanation: summaries[stage].explanation,
        evidenceDocumentIds: esr.evidenceDocumentIds.slice(0, 3), rules: summaries[stage].rules, provenance: 'deterministic-rules', decision: 'pending',
      };
      state.aiInsightsById[insight.id] = insight;
    });
  }

  for (let i = 0; i < 150; i += 1) {
    const esrId = `esr-2026-${String(i + 1).padStart(5, '0')}`;
    const esr = state.esrsById[esrId];
    const items = Object.values(state.intakeExceptionsById).filter((item) => item.esrId === esrId).slice(0, 2);
    const baseAction: ActivityEvent = {
      id: `activity-${esrId}-received`, esrId, entityId: esrId, entityType: 'esr', action: 'esr-received', newStatus: esr.intakeStatus,
      actorId: 'system-intake', at: esr.receivedAt, rationale: 'ESR persisted to the workspace. External acknowledgement is not enabled.',
    };
    state.activitiesById[baseAction.id] = baseAction;
    const aepEvent: ActivityEvent = {
      id: `activity-${esrId}-aep`, esrId, entityId: esrId, entityType: 'esr', action: 'intake-aep-recorded',
      previousStatus: 'received', newStatus: 'acknowledged-locally', actorId: 'system-intake', at: esr.aepRecordedAt ?? esr.receivedAt,
      rationale: 'Synthetic AEP receipt acknowledgement recorded locally before document processing. No external acknowledgement was sent.',
    };
    state.activitiesById[aepEvent.id] = aepEvent;
    items.forEach((item, index) => {
      const event: ActivityEvent = { id: `activity-${item.id}`, esrId, entityId: item.id, entityType: 'evidence-document', action: 'intake-exception-reviewed', newStatus: item.status, actorId: 'user-operations', at: iso(-30 + index), rationale: item.detail };
      state.activitiesById[event.id] = event;
    });
  }
  return refreshWorkItems(state, iso(0));
}
