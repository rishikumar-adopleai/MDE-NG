import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CalendarCheck,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardList,
  FileCheck2,
  FileText,
  FlaskConical,
  Inbox,
  Search,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type {
  ClinicalValidationAlert,
  DiagnosticReportedFlag,
  DraftMedicalReview,
  EntityId,
  FieldValues,
  UploadedDbqDocument,
  WorkItem,
} from '../domain/model';
import type { clinicalView } from '../../server/stageViews';
import { documentName, formVersionLabel } from './presentation';
import {
  ActionFeedback,
  dateLabel,
  resultEntityId,
  StageActions,
  StageBlock,
  StageHandoff,
  useStageAction,
  type StageProps,
} from './StageWorkbench';
import { StageHeading } from './StageHeading';
import { RecordHeader } from './RecordHeader';
import { Status } from './shared';
import { HeadingText } from '../HeadingText';

type ClinicalDetail = ReturnType<typeof clinicalView> & {
  stage2Summary?: string;
  stage2Gaps?: { id: string; severity: string; gapGroup?: string; message: string; recommendedAction: string }[];
  appointment?: {
    id: string;
    startsAt?: string;
    endsAt?: string;
    modality?: string;
    status?: string;
    facilityName?: string;
    examinerName?: string;
  } | null;
  uploadedDbqDocument?: UploadedDbqDocument | null;
  validationAlerts?: ClinicalValidationAlert[];
  draftMedicalReview?: DraftMedicalReview | null;
};

interface GroupedClinicalCase {
  esrId: EntityId;
  claimantName: string;
  initials: string;
  claimIdentifier: string;
  priority: string;
  overallMedicalSummary?: string;
  works: WorkItem[];
  signedCount: number;
  alertCount: number;
  totalCount: number;
}

export function EvidenceRecords({ evidence }: Pick<ClinicalDetail, 'evidence'>) {
  return (
    <>
      {evidence.length ? (
        evidence.map(record => (
          <details key={record.id}>
            <summary>{documentName(record.title)}</summary>
            <p className="stage-muted">{documentName(record.sourceReference)}</p>
            <ul className="stage-records">
              {record.sources.map((source, index) => (
                <li key={index}>
                  <b>Page {source.page}</b>
                  <p>{source.text}</p>
                </li>
              ))}
            </ul>
          </details>
        ))
      ) : (
        <p className="triage-inline-note">No source documents are linked to this report.</p>
      )}
    </>
  );
}

function StyledFileDropzone({
  title,
  hint,
  buttonLabel,
  disabled,
  uploadedFile,
  onFileSelected,
}: {
  title: string;
  hint: string;
  buttonLabel: string;
  disabled?: boolean;
  uploadedFile?: UploadedDbqDocument | null;
  onFileSelected: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0];
    if (file && !disabled) onFileSelected(file);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    handleFiles(event.dataTransfer.files);
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    handleFiles(event.target.files);
    event.target.value = '';
  };

  return (
    <div
      className={`intake-dropzone${dragging ? ' dragging' : ''}`}
      style={{ marginBottom: 16 }}
      onDragOver={event => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.txt"
        hidden
        disabled={disabled}
        onChange={handleChange}
      />
      <Upload size={26} style={{ marginBottom: 6 }} />
      <h3 style={{ margin: '4px 0', fontSize: 16 }}>{title}</h3>
      <p style={{ margin: '0 auto 12px', maxWidth: 460 }}>{hint}</p>

      {uploadedFile && (
        <div
          className="intake-gap-summary"
          style={{
            maxWidth: 520,
            margin: '0 auto 12px',
            textAlign: 'left',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <FileCheck2 size={18} style={{ flex: 'none', color: '#14533c' }} />
          <div style={{ minWidth: 0 }}>
            <strong style={{ display: 'block', fontSize: 13.5 }}>{uploadedFile.filename}</strong>
            <span className="stage-muted" style={{ fontSize: 12.5 }}>
              {uploadedFile.pageCount} page(s) parsed
              {uploadedFile.detectedClaimantName ? ` · Claimant detected: ${uploadedFile.detectedClaimantName}` : ''}
            </span>
          </div>
        </div>
      )}

      <button
        type="button"
        className="intake-secondary"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        <Upload size={15} /> {uploadedFile ? 'Replace Uploaded File' : buttonLabel}
      </button>
    </div>
  );
}

function DiagnosticEditor({
  diagnostic,
  onUpdated,
}: {
  diagnostic: ClinicalDetail['diagnostics'][number];
  onUpdated: () => void;
}) {
  const { recordDiagnosticResult, reviewDiagnostic, recordDiagnosticException, uploadDiagnosticDocument } =
    useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const { order, results } = diagnostic;
  const [summary, setSummary] = useState('');
  const [reference, setReference] = useState('');
  const [reportedFlag, setReportedFlag] = useState<DiagnosticReportedFlag | ''>('');
  const [reason, setReason] = useState<'claimant-declined' | 'medically-inadvisable'>('claimant-declined');
  const [exceptionNote, setExceptionNote] = useState('');

  return (
    <details open={!diagnostic.ready}>
      <summary>
        {order.orderType} · {order.exception ? 'Exception recorded' : order.status.replaceAll('-', ' ')}
      </summary>
      {order.exception && (
        <p className="stage-muted">
          {order.exception.reason.replaceAll('-', ' ')} · {order.exception.note}
        </p>
      )}
      {!!results.length && (
        <ul className="stage-records">
          {results.map(result => (
            <li key={result.id}>
              <b>{result.sourceReference}</b>
              <p>{result.summary}</p>
              {result.reportedFlag && (
                <p className={result.reportedFlag === 'critical' ? 'stage-error' : 'stage-muted'}>
                  Source marked {result.reportedFlag} ·{' '}
                  {result.reviewedAt ? 'Clinician review recorded' : 'Clinician review pending'}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {order.status !== 'reviewed' && (
        <>
          <StyledFileDropzone
            title="Upload Supporting Diagnostic Report"
            hint="Drag and drop an X-ray report, laboratory panel, or audiogram (PDF or UTF-8 TXT) to extract findings automatically."
            buttonLabel="Choose Diagnostic Report"
            disabled={pending}
            onFileSelected={file => void execute(() => uploadDiagnosticDocument(order.id, file), onUpdated)}
          />
          <form
            onSubmit={event => {
              event.preventDefault();
              void execute(
                () => recordDiagnosticResult(order.id, summary, reference, reportedFlag || undefined),
                () => {
                  setSummary('');
                  setReference('');
                  setReportedFlag('');
                  onUpdated();
                },
              );
            }}
          >
            <label className="intake-field">
              Result summary
              <textarea value={summary} required maxLength={8000} onChange={event => setSummary(event.target.value)} />
            </label>
            <label className="intake-field">
              Source reference
              <input value={reference} required maxLength={300} onChange={event => setReference(event.target.value)} />
            </label>
            <label className="intake-field">
              Designation recorded by the source
              <select
                value={reportedFlag}
                onChange={event => setReportedFlag(event.target.value as typeof reportedFlag)}
              >
                <option value="">No designation recorded</option>
                <option value="abnormal">Marked abnormal</option>
                <option value="critical">Marked critical</option>
              </select>
            </label>
            <StageActions>
              {results.length > 0 && (
                <button
                  type="button"
                  className="intake-secondary"
                  disabled={pending}
                  onClick={() => void execute(() => reviewDiagnostic(order.id), onUpdated)}
                >
                  Record result review
                </button>
              )}
              <button type="submit" className="intake-secondary" disabled={pending}>
                Save diagnostic summary
              </button>
            </StageActions>
          </form>
          {!results.length && (
            <details>
              <summary>Document an exception</summary>
              <label className="intake-field">
                Reason
                <select value={reason} onChange={event => setReason(event.target.value as typeof reason)}>
                  <option value="claimant-declined">Claimant declined</option>
                  <option value="medically-inadvisable">Medically inadvisable</option>
                </select>
              </label>
              <label className="intake-field">
                Exception explanation
                <textarea
                  rows={2}
                  maxLength={3000}
                  value={exceptionNote}
                  onChange={event => setExceptionNote(event.target.value)}
                />
              </label>
              <StageActions>
                <button
                  type="button"
                  className="intake-secondary"
                  disabled={pending || !exceptionNote.trim()}
                  onClick={() => void execute(() => recordDiagnosticException(order.id, reason, exceptionNote), onUpdated)}
                >
                  Record exception
                </button>
              </StageActions>
            </details>
          )}
        </>
      )}
      <ActionFeedback result={feedback} />
    </details>
  );
}

function ClinicalReport({
  detail,
  onRefresh,
  onSigned,
}: {
  detail: ClinicalDetail;
  onRefresh: () => void;
  onSigned: (entityId?: string) => void;
}) {
  const {
    saveDBQDraft,
    validateDBQ,
    recordExamEncounter,
    resubmitCorrection,
    completeClinicalReview,
    uploadDBQDocument,
    validateAndDraftDBQ,
  } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const { dbq } = detail;
  const [section, setSection] = useState<'report' | 'evidence' | 'diagnostics'>('report');
  const [entryMode, setEntryMode] = useState<'digital' | 'upload'>(
    detail.uploadedDbqDocument ? 'upload' : 'digital',
  );
  const [draft, setDraft] = useState<FieldValues>();
  const [encounterNote, setEncounterNote] = useState('');
  const [attestation, setAttestation] = useState<{ versionId: string; draftRevision: number }>();

  const savedVersion = detail.versions.find(version => version.id === dbq.currentVersionId);
  const currentAttestation =
    attestation &&
      savedVersion &&
      attestation.versionId === savedVersion.id &&
      attestation.draftRevision === savedVersion.version
      ? attestation
      : undefined;
  const values = draft ?? dbq.values;
  const setField = (key: string, value: string) => {
    setDraft({ ...values, [key]: value });
    setAttestation(undefined);
  };
  const diagnosticsReady = detail.diagnostics.every(item => item.ready);
  const requireCorrectionSubmission = detail.correction && detail.correction.status !== 'resubmitted';
  const blockingAlerts = (detail.validationAlerts ?? []).filter(a => a.severity === 'blocking');
  const ready =
    Boolean(savedVersion) &&
    !detail.requiresEncounter &&
    diagnosticsReady &&
    !detail.errors.length &&
    !blockingAlerts.length &&
    !draft &&
    !requireCorrectionSubmission;

  const save = () =>
    void execute(() => saveDBQDraft(dbq.id, values), () => {
      setDraft(undefined);
      onRefresh();
    });

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {detail.appointment && (
        <div className="intake-gap-summary" style={{ margin: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="intake-kicker">
              <CalendarCheck size={15} />
              <span>
                {detail.appointment.status === 'completed'
                  ? 'Completed Examination Encounter'
                  : 'Confirmed Appointment'}{' '}
                · {detail.appointment.examinerName || detail.examiner || 'Assigned Examiner'}
              </span>
            </div>
            {detail.appointment.status === 'completed' && (
              <Status text="Encounter Recorded" tone="green" />
            )}
          </div>
          <p style={{ margin: '6px 0 0' }}>
            {detail.appointment.facilityName ? `${detail.appointment.facilityName} · ` : ''}
            {detail.appointment.modality ? `${detail.appointment.modality.replaceAll('-', ' ')} · ` : ''}
            {detail.appointment.startsAt ? dateLabel(detail.appointment.startsAt) : ''}
          </p>
        </div>
      )}

      {detail.stage2Gaps && detail.stage2Gaps.length > 0 && (
        <StageBlock title="Stage 2 Information to Collect During Examination">
          <ul className="stage-records">
            {detail.stage2Gaps.map(gap => (
              <li key={gap.id}>
                <b>{gap.gapGroup === 'collect-during-exam' ? 'Collect During Exam' : 'Clinical Note'}</b>
                <p>
                  {gap.message} — <span className="stage-muted">{gap.recommendedAction}</span>
                </p>
              </li>
            ))}
          </ul>
        </StageBlock>
      )}

      <nav className="intake-sections" aria-label="Clinical work sections">
        <button type="button" aria-pressed={section === 'report'} onClick={() => setSection('report')}>
          <ClipboardList size={16} />
          Report & Review
        </button>
        <button type="button" aria-pressed={section === 'evidence'} onClick={() => setSection('evidence')}>
          <FileText size={16} />
          Evidence <span>{detail.evidence.length}</span>
        </button>
        <button type="button" aria-pressed={section === 'diagnostics'} onClick={() => setSection('diagnostics')}>
          <FlaskConical size={16} />
          Diagnostics <span>{detail.diagnostics.length}</span>
        </button>
      </nav>

      <div hidden={section !== 'report'}>
        {detail.correction && (
          <StageBlock title="QA correction request">
            <p className="triage-inline-note">{detail.correction.requestComment}</p>
          </StageBlock>
        )}
        {detail.requiresEncounter && detail.canRecordEncounter && (
          <StageBlock
            title="Examination encounter"
            description={detail.examiner ? `Assigned examiner · ${detail.examiner}` : undefined}
          >
            <label className="intake-field">
              Encounter note
              <textarea
                rows={2}
                maxLength={3000}
                value={encounterNote}
                onChange={event => setEncounterNote(event.target.value)}
              />
            </label>
            <StageActions>
              <button
                type="button"
                className="intake-secondary"
                disabled={pending || !encounterNote.trim()}
                onClick={() => void execute(() => recordExamEncounter(dbq.id, encounterNote), onRefresh)}
              >
                Record completed encounter
              </button>
            </StageActions>
          </StageBlock>
        )}
        {!detail.requiresEncounter && (
          <StageBlock
            title="Examination encounter"
            description="The clinical examination encounter has been conducted and completed."
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#047857', fontWeight: 600 }}>
              <CheckCircle2 size={16} /> Completed encounter recorded for this appointment.
            </div>
          </StageBlock>
        )}

        <StageBlock
          title="Disability Benefits Questionnaire"
          description={`${dbq.formType} · ${dbq.catalogReference
            ? `Catalog revision ${dbq.catalogReference.revisionId}`
            : formVersionLabel(dbq.formVersion)
            }${detail.examiner ? ` · ${detail.examiner}` : ''}`}
        >
          <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
            <button
              type="button"
              className={entryMode === 'digital' ? 'intake-primary' : 'intake-secondary'}
              onClick={() => setEntryMode('digital')}
            >
              <ClipboardList size={15} /> Option A: Digital DBQ Form
            </button>
            <button
              type="button"
              className={entryMode === 'upload' ? 'intake-primary' : 'intake-secondary'}
              onClick={() => setEntryMode('upload')}
            >
              <Upload size={15} /> Option B: Upload Completed DBQ
            </button>
          </div>

          {entryMode === 'upload' && (
            <StyledFileDropzone
              title="Upload Completed DBQ Document"
              hint="Drag and drop a synthetically completed DBQ PDF or UTF-8 TXT file. AI will extract findings into the structured fields below and validate them."
              buttonLabel="Choose Completed DBQ File"
              disabled={pending}
              uploadedFile={detail.uploadedDbqDocument}
              onFileSelected={file =>
                void execute(() => uploadDBQDocument(dbq.id, file), () => {
                  setDraft(undefined);
                  onRefresh();
                })
              }
            />
          )}

          <div className="stage-field-grid">
            {detail.fields.map(field => (
              <label className="intake-field" key={field.key}>
                <span>
                  {field.label}
                  {field.required && <span className="stage-required"> *</span>}
                </span>
                {'options' in field && field.options ? (
                  <select
                    value={String(values[field.key] ?? '')}
                    onChange={event => setField(field.key, event.target.value)}
                  >
                    <option value="">Choose an outcome</option>
                    {field.options.map(option => (
                      <option key={option}>{option}</option>
                    ))}
                  </select>
                ) : (
                  <textarea
                    rows={2}
                    maxLength={8000}
                    value={String(values[field.key] ?? '')}
                    onChange={event => setField(field.key, event.target.value)}
                  />
                )}
              </label>
            ))}
          </div>

          <StageActions>
            <button type="button" className="intake-secondary" disabled={pending || !draft} onClick={save}>
              Save draft
            </button>
            <button
              type="button"
              className="intake-secondary"
              disabled={pending || Boolean(draft)}
              onClick={() => void execute(() => validateDBQ(dbq.id), onRefresh)}
            >
              Validate saved report
            </button>
            <button
              type="button"
              className="intake-primary"
              disabled={pending}
              onClick={() =>
                void execute(() => validateAndDraftDBQ(dbq.id, values), () => {
                  setDraft(undefined);
                  onRefresh();
                })
              }
            >
              <Sparkles size={15} /> Validate & Draft Medical Review
            </button>
            {requireCorrectionSubmission && (
              <button
                type="button"
                className="intake-secondary"
                disabled={pending}
                onClick={() =>
                  void execute(() => resubmitCorrection(dbq.id, values), () => {
                    setDraft(undefined);
                    onRefresh();
                  })
                }
              >
                Save correction for sign-off
              </button>
            )}
          </StageActions>

          {detail.versions.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary>Saved versions ({detail.versions.length})</summary>
              <ul className="stage-records">
                {detail.versions.map(version => (
                  <li key={version.id}>
                    <b>Version {version.versionNumber}</b>
                    <p>
                      {dateLabel(version.updatedAt)} · {version.changeSummary}
                    </p>
                    {version.id !== dbq.currentVersionId && (
                      <details>
                        <summary>Read earlier version</summary>
                        {Object.entries(version.values).map(([key, value]) => (
                          <p key={key}>
                            <b>{detail.fields.find(field => field.key === key)?.label ?? key}</b>
                            <br />
                            {String(value ?? '')}
                          </p>
                        ))}
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </StageBlock>

        {detail.validationAlerts && detail.validationAlerts.length > 0 && (
          <StageBlock title="AI & Rules Clinical Validation Alerts">
            <ul className="stage-records">
              {detail.validationAlerts.map(alert => (
                <li key={alert.id}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <AlertTriangle size={15} />
                    <b className={alert.severity === 'blocking' ? 'stage-error' : ''}>
                      [{alert.severity.toUpperCase()}] {alert.message}
                    </b>
                  </div>
                  <p className="stage-muted" style={{ margin: '4px 0 0' }}>
                    Action: {alert.recommendedAction}
                  </p>
                </li>
              ))}
            </ul>
          </StageBlock>
        )}

        {detail.draftMedicalReview && (
          <StageBlock
            title="AI Draft Medical Review (Pending Clinician Approval)"
            description={`Generated by ${detail.draftMedicalReview.modelUsed} · ${detail.draftMedicalReview.promptVersion}`}
          >
            <p style={{ marginTop: 0 }}>{detail.draftMedicalReview.summary}</p>
            {detail.draftMedicalReview.keyFindings.length > 0 && (
              <ul className="stage-records">
                {detail.draftMedicalReview.keyFindings.map((item, idx) => (
                  <li key={idx}>{item}</li>
                ))}
              </ul>
            )}
            {detail.draftMedicalReview.diagnosticSummary && (
              <p className="stage-muted">
                <b>Diagnostics:</b> {detail.draftMedicalReview.diagnosticSummary}
              </p>
            )}
            {detail.draftMedicalReview.citations.length > 0 && (
              <p className="stage-muted" style={{ marginBottom: 0 }}>
                <b>Citations:</b> {detail.draftMedicalReview.citations.join(' · ')}
              </p>
            )}
          </StageBlock>
        )}
      </div>

      <div hidden={section !== 'evidence'}>
        <StageBlock title="Linked evidence">
          <EvidenceRecords evidence={detail.evidence} />
        </StageBlock>
      </div>

      <div hidden={section !== 'diagnostics'}>
        <StageBlock
          title="Diagnostic orders & supporting reports"
          description="Upload supporting X-ray, laboratory, or audiogram reports, or record reviewed summaries."
        >
          {detail.diagnostics.map(item => (
            <DiagnosticEditor key={item.order.id} diagnostic={item} onUpdated={onRefresh} />
          ))}
          {!detail.diagnostics.length && <p className="stage-muted">This exam has no diagnostic orders.</p>}
        </StageBlock>
      </div>

      <div className="stage-action-footer">
        {draft && <p className="stage-muted">Save the current edits before attesting to the report.</p>}
        {feedback?.ok && detail.errors.length > 0 && !draft && (
          <ul className="stage-list">
            {detail.errors.map(error => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        {!diagnosticsReady && <p className="stage-muted">Required diagnostic review is pending.</p>}
        {blockingAlerts.length > 0 && (
          <p className="stage-error">Resolve {blockingAlerts.length} blocking validation alert(s) before sign-off.</p>
        )}
        <ActionFeedback result={feedback} />
        {section === 'report' && (
          <>
            {attestation && !currentAttestation && (
              <p className="stage-muted" role="status">
                The saved report changed. Review it and attest again.
              </p>
            )}
            <label className="stage-attestation">
              <input
                type="checkbox"
                checked={Boolean(currentAttestation)}
                disabled={!ready || pending}
                onChange={event =>
                  setAttestation(
                    event.target.checked && savedVersion
                      ? { versionId: savedVersion.id, draftRevision: savedVersion.version }
                      : undefined,
                  )
                }
              />
              I attest to the accuracy of this saved report revision and medical review.
            </label>
            <StageActions>
              <button
                type="button"
                className="intake-primary"
                disabled={!ready || !currentAttestation || pending}
                onClick={() => {
                  if (ready && currentAttestation) {
                    void execute(
                      () => completeClinicalReview(dbq.id, { ...currentAttestation, confirmed: true }),
                      result => onSigned(resultEntityId(result.value)),
                    );
                  }
                }}
              >
                <CheckCircle2 size={16} /> Record sign-off and send to QA <ArrowRight size={16} />
              </button>
            </StageActions>
          </>
        )}
      </div>
    </div>
  );
}

function StackedDbqCard({
  index,
  work,
  stateRevision,
  defaultOpen,
  onSigned,
}: {
  index: number;
  work: WorkItem;
  stateRevision?: number;
  defaultOpen: boolean;
  onSigned: (entityId?: string) => void;
}) {
  const { state, getWorkItem } = useWorkspace();
  const [open, setOpen] = useState(defaultOpen);
  const [detail, setDetail] = useState<ClinicalDetail>();
  const [error, setError] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);

  const dbq = state?.dbqsById[work.entityId];
  const isSigned = Boolean(dbq?.signedVersionId && dbq.signedVersionId === dbq.currentVersionId);
  const hasAlerts = Boolean(dbq?.validationAlerts?.some(a => a.severity === 'blocking'));

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError('');
    getWorkItem<any>(work.id, controller.signal)
      .then(res => {
        if (!controller.signal.aborted) {
          const resolvedDetail: ClinicalDetail =
            res && typeof res === 'object' && 'detail' in res && res.detail ? res.detail : res;
          setDetail(resolvedDetail);
        }
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Unable to load clinical report.');
        }
      });
    return () => controller.abort();
  }, [open, work.id, stateRevision, refreshTick, getWorkItem]);

  return (
    <article className={`triage-card${isSigned ? ' is-approved' : ''}`}>
      <header
        className="triage-card-header"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen(v => !v)}
      >
        <div className="triage-card-title-group">
          <span className="triage-card-number">{String(index + 1).padStart(2, '0')}</span>
          <div>
            <h3>{work.title}</h3>
            <div className="triage-card-tags">
              <span className="triage-practice-tag">
                {dbq?.catalogReference ? `Catalog rev ${dbq.catalogReference.revisionId}` : 'Standard VA DBQ'}
              </span>
              {dbq?.uploadedDbqDocument && (
                <span className="triage-practice-tag">Uploaded: {dbq.uploadedDbqDocument.filename}</span>
              )}
              {hasAlerts && <span className="stage-error">Action Required</span>}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Status
            text={isSigned ? 'Signed & Sent to QA' : 'Pending Review'}
            tone={isSigned ? 'green' : 'amber'}
          />
          {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </div>
      </header>

      {open && (
        <div style={{ marginTop: 14 }}>
          {error && <p className="stage-error">{error}</p>}
          {!detail && !error && <p className="stage-muted">Loading DBQ workspace…</p>}
          {detail && (
            <ClinicalReport
              detail={detail}
              onRefresh={() => setRefreshTick(t => t + 1)}
              onSigned={onSigned}
            />
          )}
        </div>
      )}
    </article>
  );
}

export function ClinicalPage(props: StageProps) {
  const { state } = useWorkspace();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'pending' | 'alerts' | 'signed'>('all');
  const [selectedEsrId, setSelectedEsrId] = useState<string>('');
  const [handoff, setHandoff] = useState<{ stage: 'Internal QA'; esrId: string; entityId?: string }>();

  const cases = useMemo<GroupedClinicalCase[]>(() => {
    if (!state) return [];
    const byEsr = new Map<EntityId, GroupedClinicalCase>();
    const clinicalWorks = Object.values(state.workItemsById).filter(
      w => w.stage === 'Clinical Evaluation' && w.status !== 'cancelled',
    );
    for (const work of clinicalWorks) {
      const esr = state.esrsById[work.esrId];
      if (!esr) continue;
      const vet = state.veteransById[esr.veteranId];
      const dbq = state.dbqsById[work.entityId];
      const isSigned = Boolean(dbq?.signedVersionId && dbq.signedVersionId === dbq.currentVersionId);
      const hasAlerts = Boolean(dbq?.validationAlerts?.some(a => a.severity === 'blocking'));
      let entry = byEsr.get(work.esrId);
      if (!entry) {
        const name = vet?.displayName || state.clinical?.casesByEsrId?.[work.esrId]?.claimantName || 'Veteran';
        const initials = name
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .map((p: string) => p[0]?.toUpperCase() || '')
          .join('');
        entry = {
          esrId: work.esrId,
          claimantName: name,
          initials: initials || 'VT',
          claimIdentifier: esr.sourceReference || vet?.claimIdentifier || work.esrId,
          priority: esr.priority || 'routine',
          overallMedicalSummary: state.clinical?.casesByEsrId?.[work.esrId]?.overallMedicalSummary,
          works: [],
          signedCount: 0,
          alertCount: 0,
          totalCount: 0,
        };
        byEsr.set(work.esrId, entry);
      }
      entry.works.push(work);
      entry.totalCount += 1;
      if (isSigned) entry.signedCount += 1;
      if (hasAlerts) entry.alertCount += 1;
    }
    return Array.from(byEsr.values());
  }, [state]);

  const visibleCases = useMemo(() => {
    const q = search.trim().toLowerCase();
    return cases.filter(c => {
      if (filter === 'pending' && c.signedCount >= c.totalCount) return false;
      if (filter === 'alerts' && c.alertCount === 0) return false;
      if (filter === 'signed' && c.signedCount < c.totalCount) return false;
      if (!q) return true;
      return (
        c.claimantName.toLowerCase().includes(q) ||
        c.claimIdentifier.toLowerCase().includes(q) ||
        c.works.some(w => w.title.toLowerCase().includes(q))
      );
    });
  }, [cases, filter, search]);

  const activeCase = useMemo(
    () => {
      return (
        visibleCases.find(c => c.esrId === (selectedEsrId || props.selectedId)) ??
        visibleCases[0] ??
        cases[0]
      );
    },
    [visibleCases, cases, props.selectedId, selectedEsrId],
  );

  const filterButtons = [
    { key: 'all', label: 'All cases', count: cases.length },
    { key: 'pending', label: 'Pending', count: cases.filter(c => c.signedCount < c.totalCount).length },
    { key: 'alerts', label: 'Alerts', count: cases.filter(c => c.alertCount > 0).length },
    { key: 'signed', label: 'Signed', count: cases.filter(c => c.signedCount >= c.totalCount).length },
  ] as const;

  return (
    <section className="intake-page queue-workbench">
      <StageHeading
        stage="Clinical Evaluation"
        description="Review Stage 1–3 context, complete or upload DBQs, validate findings, and approve the Medical Review for Internal QA."
      />

      {handoff && (
        <StageHandoff
          stage={handoff.stage}
          esrId={handoff.esrId}
          entityId={handoff.entityId}
          onNavigate={props.onNavigate}
        >
          Signed report sent to Internal QA
        </StageHandoff>
      )}

      <div className="intake-board">
        <aside className="intake-queue-card" aria-label="Clinical queue">
          <div className="intake-queue-heading">
            <h2>Clinical queue</h2>
            <span>
              {cases.length} case{cases.length === 1 ? '' : 's'}
            </span>
          </div>

          <div className="intake-queue-controls is-open">
            <div className="intake-queue-toolbar">
              <label className="intake-search">
                <Search size={15} aria-hidden="true" />
                <input
                  placeholder="Search claimant, ESR, or DBQ"
                  value={search}
                  onChange={e => {
                    setSearch(e.target.value);
                  }}
                />
                {search && (
                  <button
                    type="button"
                    className="intake-icon-button"
                    aria-label="Clear clinical search"
                    onClick={() => setSearch('')}
                  >
                    <X size={15} />
                  </button>
                )}
              </label>
              <div className="intake-queue-filters" role="group" aria-label="Clinical queue filters">
                {filterButtons.map(f => (
                  <button
                    key={f.key}
                    type="button"
                    aria-pressed={filter === f.key}
                    onClick={() => {
                      setFilter(f.key);
                    }}
                  >
                    {f.label} <span>{f.count}</span>
                  </button>
                ))}
              </div>
            </div>

            {visibleCases.length ? (
              <ul className="intake-queue-list">
                {visibleCases.map(c => (
                  <li key={c.esrId}>
                    <button
                      type="button"
                      className="intake-queue-row"
                      aria-pressed={activeCase?.esrId === c.esrId}
                      onClick={() => {
                        setSelectedEsrId(c.esrId);
                      }}
                    >
                      <span className="intake-queue-row-main">
                        <strong>{c.claimantName}</strong>
                        <small>
                          {c.claimIdentifier} · {c.totalCount} DBQ{c.totalCount === 1 ? '' : 's'}
                        </small>
                      </span>
                      <Status
                        text={`${c.signedCount}/${c.totalCount} Signed`}
                        tone={c.signedCount >= c.totalCount ? 'green' : c.alertCount > 0 ? 'amber' : 'slate'}
                      />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="intake-queue-empty">
                <p>No clinical cases match this filter.</p>
                <button
                  type="button"
                  className="intake-text-action"
                  onClick={() => {
                    setFilter('all');
                    setSearch('');
                  }}
                >
                  Reset filters
                </button>
              </div>
            )}
          </div>
        </aside>

        {activeCase ? (
          <section
            className="intake-case-card triage-case-workspace queue-detail"
            key={activeCase.esrId}
            aria-label={`Clinical evaluation for ${activeCase.claimantName}`}
            tabIndex={0}
          >
            <RecordHeader name={activeCase.claimantName} initials={activeCase.initials}
              metadata={activeCase.claimIdentifier}
              summary={`Priority: ${activeCase.priority}`}
              actions={
                <Status
                  text={`${activeCase.signedCount}/${activeCase.totalCount} DBQs Signed`}
                  tone={activeCase.signedCount >= activeCase.totalCount ? 'green' : 'amber'}
                />
              } />

            <div className="triage-kpi-strip">
              <div className="triage-kpi-box">
                <span>Required DBQs</span>
                <strong>{activeCase.totalCount}</strong>
              </div>
              <div className="triage-kpi-box">
                <span>Signed & Sent to QA</span>
                <strong>
                  {activeCase.signedCount} / {activeCase.totalCount}
                </strong>
              </div>
              <div className="triage-kpi-box">
                <span>Pending Review</span>
                <strong>{Math.max(0, activeCase.totalCount - activeCase.signedCount)}</strong>
              </div>
            </div>

            {activeCase.overallMedicalSummary && (
              <div className="intake-gap-summary" style={{ margin: 0 }}>
                <div className="intake-kicker">
                  <FileText size={15} />
                  <span>Stage 2 Overall Medical History Summary</span>
                </div>
                <p>{activeCase.overallMedicalSummary}</p>
              </div>
            )}

            <section className="triage-cards-section" aria-label="Required Examinations and DBQs">
              <div className="intake-kicker" style={{ marginBottom: 10 }}>
                <ClipboardList size={15} />
                <span>
                  <HeadingText>{`Required Examinations & DBQs (${activeCase.works.length})`}</HeadingText>
                </span>
              </div>
              <div className="triage-cards-stack">
                {activeCase.works.map((w, idx) => (
                  <StackedDbqCard
                    key={w.id}
                    index={idx}
                    work={w}
                    stateRevision={state?.revision}
                    defaultOpen={idx === 0}
                    onSigned={entityId => setHandoff({ stage: 'Internal QA', esrId: activeCase.esrId, entityId })}
                  />
                ))}
              </div>
            </section>
          </section>
        ) : (
          <section className="intake-case-empty">
            <Inbox size={24} />
            <h2><HeadingText>{visibleCases.length ? 'No case selected' : 'No clinical cases to evaluate'}</HeadingText></h2>
            <p>
              {visibleCases.length
                ? 'Select a patient case from the clinical queue to review DBQs and complete medical documentation.'
                : 'Confirmed appointments appear here once scheduled in Stage 3.'}
            </p>
          </section>
        )}
      </div>
    </section>
  );
}
