export const WORKSPACE_SCHEMA_VERSION = 2;

export const stageKeys = [
  'Intake',
  'Medical Triage',
  'Scheduling',
  'Clinical Evaluation',
  'Internal QA',
  'VA Delivery & Review',
] as const;

export type StageKey = (typeof stageKeys)[number];
export type EntityId = string;
export type Priority = 'routine' | 'elevated' | 'urgent';
export type ServiceGroup = 'Army' | 'Navy' | 'Air Force' | 'Marine Corps' | 'Coast Guard' | 'Space Force';
export type Modality = 'in-person' | 'tele-c-and-p' | 'ace-records-review';
export type ExternalConnectionState = 'not-enabled' | 'not-submitted' | 'awaiting-external-verification' | 'externally-confirmed';
export type PersistedRecordStatus = 'active' | 'inactive' | 'archived';
export type WorkItemStatus = 'queued' | 'in-progress' | 'blocked' | 'completed' | 'cancelled';
export type WorkItemEntityType =
  | 'esr'
  | 'contention'
  | 'examination-requirement'
  | 'examination'
  | 'appointment'
  | 'dbq'
  | 'qa-review'
  | 'correction-request'
  | 'results-package';

export type IntakeStatus = 'new' | 'acknowledgement-pending' | 'processing' | 'requires-attention' | 'ready-for-triage' | 'completed';
export type ContentionStatus = 'awaiting-review' | 'in-review' | 'clarification-required' | 'approved-for-exam' | 'completed';
export type SimulatedContentionDisposition = 'pending' | 'accepted' | 'cancelled' | 'new-exam-requested';
export type ExaminationRequirementStatus = 'proposed' | 'awaiting-review' | 'approved' | 'clarification-required' | 'completed';
export type ExaminationStatus = 'awaiting-scheduling' | 'scheduled' | 'in-progress' | 'awaiting-clinical-review' | 'correction-required' | 'ready-for-qa' | 'completed' | 'cancelled';
export type AppointmentStatus = 'held' | 'confirmed-locally' | 'reschedule-requested' | 'cancelled' | 'completed' | 'no-show';
export type DbqStatus = 'draft' | 'in-progress' | 'awaiting-clinician-review' | 'correction-required' | 'ready-for-qa' | 'in-qa' | 'approved-for-package';
export type DiagnosticStatus = 'ordered' | 'scheduled' | 'result-available' | 'reviewed' | 'cancelled';
export type QaReviewStatus = 'queued' | 'in-review' | 'correction-requested' | 'approved' | 'returned';
export type QaFindingSeverity = 'blocking' | 'review' | 'advisory';
export type QaFindingStatus = 'open' | 'correction-requested' | 'resolved' | 'accepted-with-rationale';
export type CorrectionRequestStatus = 'open' | 'in-progress' | 'resubmitted' | 'closed';
export type ResultsPackageStatus = 'draft' | 'validating' | 'ready-for-review' | 'prepared-locally' | 'blocked' | 'closed';
export type SimulatedDeliveryResponse = 'not-sent' | 'awaiting-response' | 'accepted' | 'report-fix' | 'new-exam-requested';
export type DeliveryEventType = 'package-created' | 'validation-completed' | 'authorized-review-recorded' | 'external-action-blocked' | 'reconciliation-task-created' | 'reconciliation-task-completed' | 'staff-reconciliation-recorded' | 'simulated-transmission' | 'simulated-response' | 'contention-cancelled' | 'esr-closed';
export type AiInsightDecision = 'pending' | 'accepted-for-human-review' | 'rejected' | 'dismissed';
export type ActivityAction =
  | 'esr-received'
  | 'exam-encounter-recorded'
  | 'reconciliation-task-completed'
  | 'diagnostic-exception-recorded'
  | 'intake-aep-recorded'
  | 'scheduling-policy-updated'
  | 'appointment-logistics-recorded'
  | 'intake-document-reviewed'
  | 'intake-exception-reviewed'
  | 'intake-exception-resolved'
  | 'intake-exception-escalated'
  | 'intake-validated'
  | 'triage-contention-reviewed'
  | 'triage-contention-approved'
  | 'triage-contention-clarified'
  | 'triage-clarification-answered'
  | 'triage-plan-draft-saved'
  | 'triage-plan-approved'
  | 'exam-requirement-created'
  | 'examiner-assigned'
  | 'appointment-confirmed-locally'
  | 'appointment-rescheduled'
  | 'appointment-cancelled'
  | 'outreach-recorded'
  | 'diagnostic-reviewed'
  | 'diagnostic-result-recorded'
  | 'dbq-draft-saved'
  | 'dbq-validated'
  | 'clinical-review-completed'
  | 'qa-checks-completed'
  | 'qa-review-opened'
  | 'qa-finding-resolved'
  | 'correction-requested'
  | 'correction-resubmitted'
  | 'qa-review-approved'
  | 'results-package-prepared'
  | 'package-validation-completed'
  | 'delivery-review-recorded'
  | 'simulated-response-recorded'
  | 'simulated-contention-cancelled'
  | 'esr-closed'
  | 'reconciliation-task-created'
  | 'assistance-disposition-recorded';

export type FieldValue = string | number | boolean | null;
export type FieldValues = Record<string, FieldValue>;
export type EntityMap<T extends { id: EntityId }> = Record<EntityId, T>;

export interface VersionedEntity {
  id: EntityId;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface Veteran extends VersionedEntity {
  displayName: string;
  initials: string;
  claimIdentifier: string;
  serviceGroup: ServiceGroup | '';
  homeFacilityId: EntityId;
  city: string;
  stateCode: string;
  accessibilityNeeds: string[];
  communicationOptIns?: ('sms' | 'email')[];
  reserveSchedulingAllowed?: boolean;
  examinerPreference?: 'female' | 'male';
  representativeReference?: string;
}

export interface ESR extends VersionedEntity {
  practiceScenario?: { cohort: string; requestReference: string; sourceFile: string; name?: string; exercise?: string };
  veteranId: EntityId;
  receivedAt: string;
  dueAt: string;
  contentionIds: EntityId[];
  evidenceDocumentIds: EntityId[];
  priority: Priority;
  intakeStatus: IntakeStatus;
  assignedTeamId?: EntityId;
  externalState: ExternalConnectionState;
  sourceReference: string;
  receiptStatus?: 'not-received' | 'received';
  receiptReference?: string;
  aepRecordedAt?: string;
  workflowClosedAt?: string;
  workflowDisposition?: 'completed' | 'cancelled';
  closureNote?: string;
  schedulingProgram?: 'IDES' | 'BDD';
}

export interface Contention extends VersionedEntity {
  esrId: EntityId;
  title: string;
  diagnosticCode?: string;
  evidenceDocumentIds: EntityId[];
  examinationRequirementIds: EntityId[];
  status: ContentionStatus;
  clarificationReason?: string | null;
  clarificationResponse?: string | null;
  reviewerRationale?: string;
  simulatedDisposition?: SimulatedContentionDisposition;
  simulatedDispositionNote?: string;
}

export interface ExaminationRequirement extends VersionedEntity {
  esrId: EntityId;
  contentionIds: EntityId[];
  title: string;
  examinationType: string;
  requiredSpecialties: string[];
  requiredCredentials: string[];
  permittedModalities: Modality[];
  requestedDbqTypes: string[];
  dbqCatalogReferences?: Record<string, { documentId: number; revisionId: number; formName: string }>;
  requestedDiagnostics: string[];
  selectedModality?: Modality;
  dbqIds: EntityId[];
  diagnosticOrderIds: EntityId[];
  status: ExaminationRequirementStatus;
  reviewerRationale?: string;
}

export interface Examination extends VersionedEntity {
  esrId: EntityId;
  requirementIds: EntityId[];
  contentionIds: EntityId[];
  requiredDbqIds: EntityId[];
  examinerId?: EntityId;
  appointmentIds: EntityId[];
  diagnosticOrderIds: EntityId[];
  status: ExaminationStatus;
  assignedAt?: string;
}

export interface Appointment extends VersionedEntity {
  esrId: EntityId;
  examinationId: EntityId;
  examinerId: EntityId;
  facilityId: EntityId;
  startsAt: string;
  endsAt: string;
  modality: Modality;
  status: AppointmentStatus;
  accessibilityNeeds: string[];
  confirmationNote?: string;
  history: AppointmentHistoryEntry[];
  localLogistics?: AppointmentLogistics;
  outreachWindowOpenedAt?: string;
  schedulingMethod?: 'contact-confirmed' | 'reserve';
  localNotification?: { recordedAt: string; reference: string; representativeReference?: string };
}

export interface AppointmentLogistics {
  travelDistanceMiles: number;
  travelLimitMiles: number;
  specialTransportRequired: boolean;
  specialTransportStatus: 'pending' | 'simulated-arranged' | 'not-required';
  consentStatus: 'pending' | 'simulated-recorded' | 'not-required';
  vaPreapprovalStatus: 'pending' | 'simulated-recorded' | 'not-required';
  note: string;
  recordedAt?: string;
}

export interface AppointmentHistoryEntry {
  status: AppointmentStatus;
  at: string;
  actorId: EntityId;
  note: string;
}

export interface Examiner extends VersionedEntity {
  practiceOnly?: boolean;
  displayName: string;
  sex?: 'female' | 'male';
  specialties: string[];
  credentials: string[];
  training: { course: string; status: 'current' | 'expired' | 'pending'; expiresAt?: string }[];
  facilityIds: EntityId[];
  availableSlots: { startsAt: string; endsAt: string; facilityId: EntityId; modality: Modality }[];
  supportedExamTypes: string[];
  permittedModalities: Modality[];
  eligible: boolean;
  restrictionReasons: string[];
  serviceGroups: ServiceGroup[];
  status: PersistedRecordStatus;
}

export interface Facility extends VersionedEntity {
  name: string;
  city: string;
  stateCode: string;
  postalCode: string;
  latitude: number;
  longitude: number;
  accessibilityFeatures: string[];
  serviceGroups: ServiceGroup[];
  status: PersistedRecordStatus;
}

export interface UploadedDbqDocument {
  filename: string;
  uploadedAt: string;
  uploadedBy: string;
  pageCount: number;
  detectedClaimantName?: string;
}

export interface ClinicalValidationAlert {
  id: string;
  severity: 'blocking' | 'warning';
  category: string;
  message: string;
  recommendedAction: string;
}

export interface DraftMedicalReview {
  summary: string;
  keyFindings: string[];
  diagnosticSummary: string;
  citations: string[];
  generatedAt: string;
  modelUsed: string;
  promptVersion: string;
  status: 'pending-approval' | 'approved';
}

export interface ClinicalCaseView {
  esrId: EntityId;
  claimantName: string;
  claimIdentifier: string;
  priority: ESR['priority'];
  overallMedicalSummary?: string;
  dbqWorkIds: EntityId[];
  dbqIds: EntityId[];
  signedCount: number;
  totalCount: number;
}

export interface ClinicalWorkspaceView {
  queueIds: EntityId[];
  casesByEsrId: Record<EntityId, ClinicalCaseView>;
}

export interface DBQ extends VersionedEntity {
  examinationId: EntityId;
  esrId: EntityId;
  contentionIds: EntityId[];
  formType: string;
  formVersion: string;
  catalogReference?: { documentId: number; revisionId: number; formName: string };
  values: FieldValues;
  versionIds: EntityId[];
  currentVersionId: EntityId;
  status: DbqStatus;
  validationErrors: string[];
  localReviewState: 'not-ready' | 'ready-for-review' | 'review-recorded';
  reviewerId?: EntityId;
  reviewedAt?: string;
  signedVersionId?: EntityId;
  localAttestation?: { versionId: EntityId; staffActorId: EntityId; examinerId: EntityId; examinerName: string; credentials: string[]; specialty: string[]; at: string };
  uploadedDbqDocument?: UploadedDbqDocument;
  validationAlerts?: ClinicalValidationAlert[];
  draftMedicalReview?: DraftMedicalReview;
}

export interface DBQVersion extends VersionedEntity {
  dbqId: EntityId;
  versionNumber: number;
  formVersion: string;
  values: FieldValues;
  authoredBy: EntityId;
  changeSummary: string;
  createdFor: 'draft' | 'correction-resubmission' | 'clinical-review';
}

export interface DiagnosticOrder extends VersionedEntity {
  esrId: EntityId;
  examinationId: EntityId;
  requirementId: EntityId;
  orderType: string;
  status: DiagnosticStatus;
  required: boolean;
  sourceReference?: string;
  exception?: { reason: 'claimant-declined' | 'medically-inadvisable'; note: string; at: string };
  resultIds: EntityId[];
}

export type DiagnosticReportedFlag = 'abnormal' | 'critical';

export interface DiagnosticResult extends VersionedEntity {
  esrId: EntityId;
  orderId: EntityId;
  examinationId: EntityId;
  summary: string;
  sourceReference: string;
  reportedFlag?: DiagnosticReportedFlag;
  reviewedBy?: EntityId;
  containsRestrictedRawMaterial?: boolean;
  reviewedAt?: string;
}

export type EvidenceCategory = 'service-treatment' | 'va-medical-records' | 'private-medical-records' | 'hospital'
  | 'laboratory' | 'prior-examination' | 'imaging' | 'va-request' | 'examination-requests' | 'other'
  | 'claims-and-applications' | 'military-personnel' | 'diagnostic-tests' | 'hospital-surgical'
  | 'medication-prescription' | 'medical-opinions' | 'health-assessments' | 'authorization-consent'
  | 'lay-witness' | 'representation' | 'decisions-appeals' | 'insurance-financial' | 'administrative-correspondence';

export interface EvidenceDocument extends VersionedEntity {
  esrId: EntityId;
  title: string;
  category: EvidenceCategory;
  sourceReference: string;
  sourceHash?: string;
  administrativeOnly?: boolean;
  pageCount: number;
  classifiedPageCount: number;
  unreadablePageCount: number;
  classificationStatus: 'queued' | 'processing' | 'indexed' | 'needs-human-review' | 'excluded';
  contentionIds: EntityId[];
  syntheticExcerpts?: EvidenceExcerpt[];
  sections?: {
    sectionId: string;
    title: string;
    category: string;
    startPage: number;
    endPage: number;
    pageCount: number;
    summary: string;
  }[];
  quality?: {
    fileIntegrity: string;
    sha256: string;
    readabilityScore: number;
    qualityStatus: string;
    readablePages?: number;
    totalExpectedPages?: number;
    claimantMatch?: string;
    ocrApplied?: boolean;
    ocrConfidence?: number;
  };
  extractedData?: {
    labPanels?: {
      panelName: string;
      collectionDate?: string;
      specimen?: string;
      orderingProvider?: string;
      results: {
        name: string;
        value: string;
        units: string;
        referenceRange: string;
        flag: 'normal' | 'abnormal' | 'critical';
      }[];
    }[];
  };
  processing?: {
    provider: 'local' | 'ai';
    status: 'queued' | 'indexed' | 'needs-review';
    aiUnavailable?: boolean;
    finishedAt?: string;
    summary?: string;
    error?: string;
    retryable?: boolean;
    sources?: { page: number; text: string }[];
  };
}

export interface EvidenceExcerpt {
  id: EntityId;
  pageNumber: number;
  contentionId: EntityId;
  text: string;
}

export interface IntakeException extends VersionedEntity {
  esrId: EntityId;
  documentId?: EntityId;
  kind: 'unreadable-pages' | 'duplicate-document' | 'possible-duplicate' | 'missing-document' | 'metadata-incomplete';
  title: string;
  detail: string;
  sourceReference?: string;
  status: 'open' | 'reviewed' | 'resolved' | 'escalated';
  resolution?: string;
}

export interface Communication extends VersionedEntity {
  esrId: EntityId;
  appointmentId?: EntityId;
  channel: 'phone' | 'sms' | 'email' | 'mail' | 'secure-message';
  direction: 'outbound' | 'inbound';
  recipient: 'claimant' | 'representative' | 'examiner' | 'internal-team';
  summary: string;
  outcome: 'pending' | 'delivered' | 'answered' | 'no-response' | 'returned';
  actorId: EntityId;
}

export interface OutreachAttempt extends VersionedEntity {
  esrId: EntityId;
  appointmentId?: EntityId;
  attemptNumber: number;
  channel: Communication['channel'];
  attemptedAt: string;
  voicemail?: 'left' | 'unavailable';
  outcome: Communication['outcome'];
  note: string;
  actorId: EntityId;
}

export interface QAReview extends VersionedEntity {
  esrId: EntityId;
  dbqId: EntityId;
  versionId: EntityId;
  reviewerId: EntityId;
  findingIds: EntityId[];
  status: QaReviewStatus;
  reviewerComment: string;
  approvedAt?: string;
  checks?: QAValidationCheck[];
}

export interface QAValidationCheck {
  key: 'required-fields' | 'clinician-signoff' | 'evidence-linkage' | 'form-version' | 'diagnostics';
  label: string;
  status: 'passed' | 'failed';
  severity: QaFindingSeverity;
  detail: string;
  versionId: EntityId;
  evaluatedAt: string;
}

export interface QAFinding extends VersionedEntity {
  esrId: EntityId;
  qaReviewId: EntityId;
  dbqId: EntityId;
  sectionReference: string;
  issueCategory: 'missing-required-field' | 'incomplete-rationale' | 'form-version' | 'signature-readiness' | 'evidence-consistency' | 'attachment';
  severity: QaFindingSeverity;
  title: string;
  supportingEvidenceIds: EntityId[];
  reviewerId: EntityId;
  status: QaFindingStatus;
  comments: string[];
  resolutionHistory: QaResolutionEntry[];
  origin?: 'deterministic' | 'reviewer';
  checkKey?: QAValidationCheck['key'];
  versionId?: EntityId;
}

export interface QaResolutionEntry {
  at: string;
  actorId: EntityId;
  status: QaFindingStatus;
  comment: string;
}

export interface CorrectionRequest extends VersionedEntity {
  esrId: EntityId;
  dbqId: EntityId;
  qaReviewId: EntityId;
  findingIds: EntityId[];
  assignedTo: EntityId;
  requestedBy: EntityId;
  status: CorrectionRequestStatus;
  requestComment: string;
  history: { at: string; status: CorrectionRequestStatus; note: string; actorId: EntityId }[];
}

export interface ResultsPackage extends VersionedEntity {
  esrId: EntityId;
  dbqIds: EntityId[];
  diagnosticResultIds: EntityId[];
  qaApprovalIds: EntityId[];
  validationStatus: 'not-validated' | 'passed' | 'blocked';
  preparationStatus: ResultsPackageStatus;
  externalState: ExternalConnectionState;
  blockingReasons: string[];
  versionNumber: number;
  simulatedResponse?: SimulatedDeliveryResponse;
  simulatedResponseNote?: string;
  preparedManifest?: PackageManifest;
  submissions?: PackageSubmission[];
}

export interface PackageManifest {
  contentions: EntityId[];
  reports: { dbqId: EntityId; revisionId: EntityId; approvalId: EntityId }[];
  diagnosticResultIds: EntityId[];
}

export interface PackageSubmission {
  sequence: number;
  manifest: PackageManifest;
  openedAt: string;
  response?: { kind: Exclude<SimulatedDeliveryResponse, 'not-sent' | 'awaiting-response'>; details: string; targetId?: EntityId; at: string };
}

export interface DeliveryEvent extends VersionedEntity {
  packageId: EntityId;
  esrId: EntityId;
  eventType: DeliveryEventType;
  relatedEventId?: EntityId;
  recordedAt: string;
  actorId: EntityId;
  details: string;
  externalState: ExternalConnectionState;
}

export type AIReviewPoint = { text: string; source_ids: string[] };
export type AIStageReview = {
  overview: AIReviewPoint;
  review_points: AIReviewPoint[];
  sources: { id: string; label: string; excerpt: string }[];
  revision: number;
};
export type DocumentReviewDecision = 'accepted-for-human-review' | 'rejected';

export interface AIInsight extends VersionedEntity {
  esrId: EntityId;
  entityId: EntityId;
  stage: StageKey;
  title: string;
  explanation: string;
  evidenceDocumentIds: EntityId[];
  rules: string[];
  provenance: 'deterministic-rules' | 'ai-review';
  documentReview?: AIStageReview & { reviewId: string; format?: 'checklist' };
  decision: AiInsightDecision;
  decidedBy?: EntityId;
  decidedAt?: string;
}

export interface WorkItem extends VersionedEntity {
  esrId: EntityId;
  entityId: EntityId;
  entityType: WorkItemEntityType;
  stage: StageKey;
  title: string;
  status: WorkItemStatus;
  priority: Priority;
  assignedTo?: EntityId;
  blockingReasons: string[];
}

export interface ActivityEvent {
  id: EntityId;
  esrId: EntityId;
  entityId: EntityId;
  entityType: WorkItemEntityType | 'evidence-document' | 'diagnostic-order' | 'diagnostic-result' | 'qa-finding' | 'delivery-event';
  action: ActivityAction;
  previousStatus?: string;
  newStatus?: string;
  actorId: EntityId;
  at: string;
  relatedWorkItemId?: EntityId;
  rationale?: string;
}

export interface WorkspaceState {
  workspaceId: string;
  schemaVersion: number;
  revision: number;
  schedulingPolicy?: { maxTravelDistanceMiles: number };
  initializedAt: string;
  updatedAt: string;
  veteransById: EntityMap<Veteran>;
  esrsById: EntityMap<ESR>;
  contentionsById: EntityMap<Contention>;
  examinationRequirementsById: EntityMap<ExaminationRequirement>;
  examinationsById: EntityMap<Examination>;
  appointmentsById: EntityMap<Appointment>;
  examinersById: EntityMap<Examiner>;
  facilitiesById: EntityMap<Facility>;
  dbqsById: EntityMap<DBQ>;
  dbqVersionsById: EntityMap<DBQVersion>;
  diagnosticOrdersById: EntityMap<DiagnosticOrder>;
  diagnosticResultsById: EntityMap<DiagnosticResult>;
  evidenceDocumentsById: EntityMap<EvidenceDocument>;
  intakeExceptionsById: EntityMap<IntakeException>;
  communicationsById: EntityMap<Communication>;
  outreachAttemptsById: EntityMap<OutreachAttempt>;
  qaReviewsById: EntityMap<QAReview>;
  qaFindingsById: EntityMap<QAFinding>;
  correctionRequestsById: EntityMap<CorrectionRequest>;
  resultsPackagesById: EntityMap<ResultsPackage>;
  deliveryEventsById: EntityMap<DeliveryEvent>;
  aiInsightsById: EntityMap<AIInsight>;
  workItemsById: EntityMap<WorkItem>;
  activitiesById: EntityMap<ActivityEvent>;
}

export interface LogicalDocument {
  id: string;
  parentDocumentId: EntityId;
  sectionId: string;
  title: string;
  category: EvidenceCategory;
  startPage: number;
  endPage: number;
  pageCount: number;
  sourceReference: string;
  parentFileName: string;
  classificationStatus: 'indexed' | 'needs-human-review';
  confidence: number;
  readabilityScore: number;
  ocrApplied: boolean;
  ocrConfidence: number;
  isDuplicate: boolean;
  duplicateOf?: string | null;
  claimantMatch: 'aligned' | 'mismatch';
  wrongPatient: boolean;
  actionRequired: boolean;
  issues: string[];
  extractedData?: {
    labPanels?: {
      panelName: string;
      collectionDate?: string;
      specimen?: string;
      orderingProvider?: string;
      results: {
        name: string;
        value: string;
        units: string;
        referenceRange: string;
        flag: 'normal' | 'abnormal' | 'critical';
      }[];
    }[];
  };
  summary?: string;
  administrativeOnly?: boolean;
}

export interface AiOrganizerCategory {
  key: string;
  label: string;
  docCount: number;
  pageCount: number;
  readablePages: number;
  documentIds: EntityId[];
}

export interface AiOrganizerSummary {
  categories: AiOrganizerCategory[];
  medicalDocuments: EntityId[];
  metadataDocuments: EntityId[];
  logicalDocuments?: LogicalDocument[];
  logicalMetadataDocuments?: LogicalDocument[];
  totalPhysicalFiles?: number;
  totalPhysicalMedicalFiles?: number;
  totalLogicalDocs?: number;
  totalMedicalDocs: number;
  totalMetadataDocs: number;
  totalMedicalPages: number;
  readableMedicalPages: number;
  qualityScore: number;
  duplicateCount: number;
  duplicateDocumentIds: EntityId[];
  unreadableCount: number;
  corruptedCount: number;
  mismatchCount: number;
  actionRequiredCount?: number;
  processingComplete: boolean;
}

export interface IntakeWorkspaceView {
  queueIds: EntityId[];
  viewsByEsrId: Record<EntityId, {
    documentIds: EntityId[];
    exceptionIds: EntityId[];
    activityIds: EntityId[];
    openExceptions: number;
    escalatedExceptions: number;
    pageCount: number;
    readablePages: number;
    unreadablePages: number;
    progress: number;
    manifestStatus?: 'verified' | 'unverified' | 'discrepancy';
    manifestSummary?: string;
    expectedCount?: number | null;
    receivedCount?: number;
    missingDocuments?: { id: string; title: string; expectedCategory: string; required?: boolean }[];
    categorizedCounts?: Record<string, number>;
    aiOrganizer?: AiOrganizerSummary;
    vaInstructions?: VaInstructionsSummary;
    qualityRate?: number;
    receiptReady: boolean;
    canAttach: boolean;
    canProcessLegacy: boolean;
    canValidate: boolean;
    needsReadableEvidence: boolean;
    blocker: string | null;
    queueLabel: string;
    steps: number;
  }>;
}

export interface VaInstructionsSummary {
  specialInstructions: string;
  serviceBranch: string;
  claimantName: string;
  claimIdentifier: string;
  benefitProgram: string;
  priorityHandling: string;
  requestedDbqs: string[];
  contentionCount: number;
  contentions: { title: string; status: string; dbqs?: string[] }[];
  expedited: boolean;
  isSimulated?: boolean;
}

export interface EsrInspectionResult {
  claimantName: string;
  esrReference: string;
  serviceBranch: string;
  benefitProgram: string;
  city: string;
  stateCode: string;
  postalCode?: string;
  travelLimitMiles?: number | null;
  contentions: { title: string; classification?: string; forms?: string[] }[];
  specialInstructions: string;
  fileType: string;
  fileSize: number;
  fileCount?: number;
  examManagementRequest?: boolean;
  pageCount?: number;
  isDuplicate: boolean;
  existingEsrId?: string | null;
  duplicateReason?: string | null;
  isSimulated?: boolean;
}

export interface TriagePlan {
  examCode: string;
  modality: Modality | '';
  dbqs: string[];
  diagnostics: string[];
  rationale: string;
  scopeChangeAuthorized?: boolean;
  remandAuthorizationReference?: string;
  unverifiedRuleAcknowledged?: boolean;
}

export interface TriageMatchedEvidence {
  documentId: EntityId;
  documentTitle: string;
  category: string;
  page: number;
  clinicalDate: string;
  matchedTerms: string[];
  excerpt: string;
}

export interface TriageMetric {
  value: string;
  label: string;
  date: string;
  source: string;
}

export interface TriageMedicalCode {
  system: string;
  code: string;
  display: string;
  bodyPart?: string;
  laterality?: string;
  examCode?: string;
  kind?: string;
}

export interface TriageEvidenceGap {
  id: string;
  severity: 'critical' | 'warning' | 'info';
  gapGroup?: 'blocking-issue' | 'clinical-review-requirement' | 'collect-during-exam';
  category: string;
  message: string;
  recommendedAction: string;
}

export interface TriageContentionDossier {
  contentionId: EntityId;
  title: string;
  aiSummary: string;
  citations: string[];
  summaryProvider: string;
  searchTermsUsed: string[];
  usedFallback: boolean;
  matchedEvidence: TriageMatchedEvidence[];
  extractedMetrics: TriageMetric[];
  medicalCodes: TriageMedicalCode[];
  factsCount: number;
  negatedCount: number;
  examSuggestion: {
    examCode?: string;
    vaRequestedDbqs?: string[];
    canonicalVaDbqs?: string[];
    suggestedDbqs?: string[];
    dbqMatchSource?: string;
    secondaryRecommendations?: string[];
    secondaryDbqs?: string[];
    specialty?: string;
    credentials?: string[];
    qualificationVerification?: string;
    ruleSourceReference?: string;
    isDynamicUnmapped?: boolean;
    ruleVerificationStatus?: 'verified' | 'unverified-requires-review';
    unverifiedReasons?: string[];
  };
  rulesEval: {
    evidenceGaps?: TriageEvidenceGap[];
    suggestedDiagnostics?: string[];
    vaSpecifiedPathway?: string;
    permittedModalities?: string[];
    modalityEligibilityReasons?: Record<string, string>;
    aceEligible?: boolean;
    aceEligibilityReason?: string;
    ruleVerificationStatus?: 'verified' | 'unverified-requires-review';
    proposedPathway?: string;
    proposedModality?: string;
    pathwaySummary?: string;
    pathwayReason?: string;
    ruleReference?: string;
    requiresConfirmation?: boolean;
    aceCandidate?: boolean;
  };
}

export interface TriageRowView {
  contentionId: EntityId;
  esrId: EntityId;
  priority: ESR['priority'];
  documentIds: EntityId[];
  readableEvidence: boolean;
  requestedDbqs?: string[];
  awaitingResponse: boolean;
  aceAuthorized?: boolean;
  aceAuthorizationReference?: string;
  triageStaleReason?: string;
  pathwaysByExam: Record<string, Modality[]>;
  initialPlan: TriagePlan;
  specialInstructions?: string;
  serviceBranch?: string;
  isSimulated?: boolean;
  dossier?: TriageContentionDossier;
}

export interface TriageCaseContention {
  index: number;
  contentionId: EntityId;
  title: string;
  status: ContentionStatus;
  requestedDbqs: string[];
  aceAuthorized: boolean;
  aceAuthorizationReference?: string;
  approvedRequirement?: ExaminationRequirement | null;
  row?: TriageRowView | null;
  dossier?: TriageContentionDossier;
}

export interface TriageCaseView {
  esrId: EntityId;
  claimantName: string;
  initials: string;
  claimIdentifier: string;
  serviceBranch: string;
  benefitProgram: string;
  priority: ESR['priority'];
  specialInstructions: string;
  isSimulated: boolean;
  processing: {
    status: 'queued' | 'processing' | 'completed' | 'failed';
    queuedAt?: string;
    startedAt?: string;
    completedAt?: string;
    failedAt?: string;
    error?: string;
    retryable?: boolean;
    factCount?: number;
    aiProvider?: string;
    ruleVersion?: string;
  };
  caseOverview: string;
  caseCitations: string[];
  aiProvider: string;
  distressAlerts: {
    esrId: EntityId;
    documentId: EntityId;
    documentTitle: string;
    pageNumber: number;
    excerpt: string;
    reason: string;
  }[];
  programFlags: {
    ruleVersion?: string;
    effectiveDate?: string;
    isBddIdes?: boolean;
    isBvaRemand?: boolean;
    isTera?: boolean;
    isInternational?: boolean;
    programNotices?: { rule: string; severity: string; text: string }[];
  };
  requestedCount: number;
  approvedCount: number;
  pendingCount: number;
  clarificationCount: number;
  contentions: TriageCaseContention[];
}

export interface TriageWorkspaceView {
  rows: TriageRowView[];
  queueIds?: EntityId[];
  casesByEsrId?: Record<EntityId, TriageCaseView>;
  ruleVersion?: string;
  examChoices: {
    code: string;
    title: string;
    specialty: string;
    credentials: string[];
    primaryDbq: string;
    dbqs: string[];
    modalities?: Modality[];
    diagnostics: string[];
  }[];
}

export interface SchedulingFacilityMatch {
  facilityId: string;
  facilityName: string;
  facilityType: string;
  city: string;
  stateCode: string;
  postalCode: string;
  distanceMiles: number | null;
  distancePrecision?: 'exact' | 'approximate-zip' | 'unknown';
  validDoctorCount?: number;
  availableSlotCount?: number;
  withinTravelLimit: boolean;
  accessibilityFeatures: string[];
  diagnosticCapabilities: string[];
}

export interface SchedulingSlotMatch {
  slotId: string;
  examinerId: string;
  facilityId: string;
  startsAt: string;
  endsAt: string;
  localStartsAt: string;
  localEndsAt: string;
  timeZone: string;
  durationMinutes: number;
  modality: Modality;
}

export interface SchedulingDoctorMatch {
  examinerId: string;
  doctorName: string;
  primarySpecialty: string;
  specialties: string[];
  credentials: string[];
  licenseNumber: string;
  licenseState: string;
  licenseExpiryDate: string;
  licenseStatus: string;
  authorizedDbqTypes: string[];
  slots: SchedulingSlotMatch[];
}

export interface SchedulingExcludedDoctor {
  examinerId: string;
  doctorName: string;
  primarySpecialty: string;
  licenseStatus: string;
  licenseExpiryDate: string;
  reason: string;
}

export interface SchedulingMatchResponse {
  esrId: EntityId;
  modality: Modality;
  travelLimitMiles?: number | null;
  locationPrecision?: 'exact' | 'approximate-zip' | 'unknown';
  locationVerificationRequired?: boolean;
  facilities: SchedulingFacilityMatch[];
  selectedFacilityId: string;
  validDoctors: SchedulingDoctorMatch[];
  excludedDoctors: SchedulingExcludedDoctor[];
  selectedDoctorId: string;
  slots: SchedulingSlotMatch[];
  schedulingException?: {
    code: string;
    message: string;
    reasons: string[];
  } | null;
}

export interface CoordinatedPackageItem {
  requirementId: EntityId;
  examinationType: string;
  specialty: string;
  modality: Modality;
  examinerId: string;
  examinerName: string;
  licenseExpiryDate: string;
  licenseStatus: string;
  slotId: string;
  startsAt: string;
  endsAt: string;
  durationMinutes: number;
}

export interface CoordinatedPackage {
  packageId: string;
  coverageType: 'full' | 'partial' | 'single' | 'separate';
  badge: string;
  facilityId: string;
  facilityName: string;
  city: string;
  stateCode: string;
  postalCode?: string;
  distanceMiles?: number | null;
  distancePrecision?: string;
  withinTravelLimit: boolean;
  date: string;
  visitStartsAt: string;
  visitEndsAt: string;
  totalIdleWaitMinutes?: number;
  items: CoordinatedPackageItem[];
  unmatchedRequirements?: { requirementId: EntityId; examinationType: string; reason: string }[];
  explanation: string;
  contractualNotice: string;
}

export interface CoordinatedMatchResponse {
  esrId: EntityId;
  totalPendingCount: number;
  packages: CoordinatedPackage[];
  bestPackageId: string;
  message?: string;
}

export interface SchedulingCompatibleGroup {
  groupId: string;
  modality: Modality;
  specialtyGroup: string;
  requirementIds: EntityId[];
  titles: string[];
  isBundled: boolean;
  reason: string;
}

export interface SchedulingCaseRequirement extends ExaminationRequirement {
  workItemId: EntityId;
  workStatus: string;
  appointment?: Appointment & {
    examinerName?: string;
    examinerLicenseExpiry?: string;
    facilityName?: string;
    slotId?: string;
    outreachLog?: { id: string; at: string; actorId: string; channel: string; outcome: string; note: string }[];
  } | null;
}

export interface SchedulingCaseView {
  esrId: EntityId;
  claimantName: string;
  initials: string;
  claimIdentifier: string;
  city: string;
  stateCode: string;
  postalCode: string;
  phone: string;
  email: string;
  priority: ESR['priority'];
  dueAt: string;
  specialInstructions: string;
  travelLimitMiles?: number | null;
  requirements: SchedulingCaseRequirement[];
  compatibleGroups: SchedulingCompatibleGroup[];
  appointments: SchedulingCaseRequirement['appointment'][];
  approvedCount: number;
  scheduledCount: number;
  pendingCount: number;
}

export interface SchedulingWorkspaceView {
  queueIds: EntityId[];
  casesByEsrId: Record<EntityId, SchedulingCaseView>;
}
