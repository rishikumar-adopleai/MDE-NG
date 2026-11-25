import { useState } from 'react';
import { ArrowLeft, ArrowRight, ClipboardList, FileText, FlaskConical } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { DiagnosticReportedFlag, FieldValues } from '../domain/model';
import type { clinicalView } from '../../server/stageViews';
import { documentName, formVersionLabel } from './presentation';
import { ActionFeedback, dateLabel, resultEntityId, StageActions, StageBlock, StageHandoff, StageWorkbench, useStageAction, type StageProps } from './StageWorkbench';

type ClinicalDetail = ReturnType<typeof clinicalView>;

export function EvidenceRecords({ evidence }: Pick<ClinicalDetail,'evidence'>) {
  return <>{evidence.length ? evidence.map(record => <details key={record.id}>
    <summary>{documentName(record.title)}</summary><p className="stage-muted">{documentName(record.sourceReference)}</p>
    <ul className="stage-records">{record.sources.map((source, index) => <li key={index}><b>Page {source.page}</b><p>{source.text}</p></li>)}</ul>
  </details>) : <p className="triage-inline-note">No source documents are linked to this report.</p>}</>;
}

function DiagnosticEditor({ diagnostic }: { diagnostic: ClinicalDetail['diagnostics'][number] }) {
  const { recordDiagnosticResult, reviewDiagnostic, recordDiagnosticException } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const { order, results } = diagnostic;
  const [summary, setSummary] = useState('');
  const [reference, setReference] = useState('');
  const [reportedFlag, setReportedFlag] = useState<DiagnosticReportedFlag | ''>('');
  const [reason, setReason] = useState<'claimant-declined' | 'medically-inadvisable'>('claimant-declined');
  const [exceptionNote, setExceptionNote] = useState('');
  return <details open={!diagnostic.ready}>
    <summary>{order.orderType} · {order.exception ? 'Exception recorded' : order.status.replaceAll('-', ' ')}</summary>
    {order.exception && <p className="stage-muted">{order.exception.reason.replaceAll('-', ' ')} · {order.exception.note}</p>}
    {!!results.length && <ul className="stage-records">{results.map(result => <li key={result.id}>
      <b>{result.sourceReference}</b><p>{result.summary}</p>
      {result.reportedFlag && <p className={result.reportedFlag === 'critical' ? 'stage-error' : 'stage-muted'}>
        Source marked {result.reportedFlag} · {result.reviewedAt ? 'Clinician review recorded' : 'Clinician review pending'}
      </p>}
    </li>)}</ul>}
    {order.status !== 'reviewed' && <>
      <form onSubmit={event => { event.preventDefault(); void execute(() => recordDiagnosticResult(order.id, summary, reference, reportedFlag || undefined), () => { setSummary(''); setReference(''); setReportedFlag(''); }); }}>
        <label className="intake-field">Result summary<textarea value={summary} required maxLength={8000} onChange={event => setSummary(event.target.value)} /></label>
        <label className="intake-field">Source reference<input value={reference} required maxLength={300} onChange={event => setReference(event.target.value)} /></label>
        <label className="intake-field">Designation recorded by the source<select value={reportedFlag} onChange={event => setReportedFlag(event.target.value as typeof reportedFlag)}>
          <option value="">No designation recorded</option><option value="abnormal">Marked abnormal</option><option value="critical">Marked critical</option>
        </select></label>
        <StageActions>{results.length > 0 && <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => reviewDiagnostic(order.id))}>Record result review</button>}
          <button type="submit" className="intake-secondary" disabled={pending}>Save diagnostic summary</button></StageActions>
      </form>
      {!results.length && <details><summary>Document an exception</summary>
        <label className="intake-field">Reason<select value={reason} onChange={event => setReason(event.target.value as typeof reason)}><option value="claimant-declined">Claimant declined</option><option value="medically-inadvisable">Medically inadvisable</option></select></label>
        <label className="intake-field">Exception explanation<textarea rows={2} maxLength={3000} value={exceptionNote} onChange={event => setExceptionNote(event.target.value)} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !exceptionNote.trim()} onClick={() => void execute(() => recordDiagnosticException(order.id, reason, exceptionNote))}>Record exception</button></StageActions>
      </details>}
    </>}
    <ActionFeedback result={feedback} />
  </details>;
}

function ClinicalReport({ detail, onSigned, onReturned }: {
  detail: ClinicalDetail;
  onSigned: (entityId?: string) => void;
  onReturned: (esrId: string) => void;
}) {
  const { saveDBQDraft, validateDBQ, recordExamEncounter, resubmitCorrection, completeClinicalReview, returnClinicalToScheduling } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const { dbq } = detail;
  const [section, setSection] = useState<'report' | 'evidence' | 'diagnostics'>('report');
  const [draft, setDraft] = useState<FieldValues>();
  const [encounterNote, setEncounterNote] = useState('');
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnReason, setReturnReason] = useState('');
  const [attestation, setAttestation] = useState<{ versionId: string; draftRevision: number }>();
  const savedVersion = detail.versions.find(version => version.id === dbq.currentVersionId);
  const currentAttestation = attestation && savedVersion
    && attestation.versionId === savedVersion.id && attestation.draftRevision === savedVersion.version
    ? attestation : undefined;
  const values = draft ?? dbq.values;
  const setField = (key: string, value: string) => { setDraft({ ...values, [key]: value }); setAttestation(undefined); };
  const diagnosticsReady = detail.diagnostics.every(item => item.ready);
  const requireCorrectionSubmission = detail.correction && detail.correction.status !== 'resubmitted';
  const ready = Boolean(savedVersion) && !detail.requiresEncounter && diagnosticsReady && !detail.errors.length && !draft && !requireCorrectionSubmission;
  const save = () => void execute(() => saveDBQDraft(dbq.id, values), () => setDraft(undefined));

  return <>
    <nav className="intake-sections" aria-label="Clinical work sections">
      <button type="button" aria-pressed={section === 'report'} onClick={() => setSection('report')}><ClipboardList size={16} />Report</button>
      <button type="button" aria-pressed={section === 'evidence'} onClick={() => setSection('evidence')}><FileText size={16} />Evidence <span>{detail.evidence.length}</span></button>
      <button type="button" aria-pressed={section === 'diagnostics'} onClick={() => setSection('diagnostics')}><FlaskConical size={16} />Diagnostics <span>{detail.diagnostics.length}</span></button>
    </nav>
    <div hidden={section !== 'report'}>
      {detail.correction && <StageBlock title="QA correction request"><p className="triage-inline-note">{detail.correction.requestComment}</p></StageBlock>}
      {detail.requiresEncounter && detail.canRecordEncounter && <StageBlock title="Examination encounter" description={detail.examiner ? `Assigned examiner · ${detail.examiner}` : undefined}>
        <label className="intake-field">Encounter note<textarea rows={2} maxLength={3000} value={encounterNote} onChange={event => setEncounterNote(event.target.value)} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !encounterNote.trim()} onClick={() => void execute(() => recordExamEncounter(dbq.id, encounterNote))}>Record completed encounter</button></StageActions>
      </StageBlock>}
      <StageBlock title="Disability Benefits Questionnaire" description={`${dbq.formType} · ${dbq.catalogReference ? `Catalog revision ${dbq.catalogReference.revisionId}` : formVersionLabel(dbq.formVersion)}${detail.examiner ? ` · ${detail.examiner}` : ''}`}>
        <div className="stage-field-grid">
          {detail.fields.map(field => <label className="intake-field" key={field.key}><span>{field.label}{field.required && <span className="stage-required"> *</span>}</span>
            {'options' in field && field.options ? <select value={String(values[field.key] ?? '')} onChange={event => setField(field.key, event.target.value)}>
              <option value="">Choose an outcome</option>{field.options.map(option => <option key={option}>{option}</option>)}
            </select> : <textarea rows={2} maxLength={8000} value={String(values[field.key] ?? '')} onChange={event => setField(field.key, event.target.value)} />}
          </label>)}
        </div>
        <StageActions>
          <button type="button" className="intake-secondary" disabled={pending || !draft} onClick={save}>Save draft</button>
          <button type="button" className="intake-secondary" disabled={pending || Boolean(draft)} onClick={() => void execute(() => validateDBQ(dbq.id))}>Validate saved report</button>
          {requireCorrectionSubmission && <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => resubmitCorrection(dbq.id, values), () => setDraft(undefined))}>Save correction for sign-off</button>}
        </StageActions>
        {detail.versions.length > 0 && <details><summary>Saved versions</summary>
          <ul className="stage-records">{detail.versions.map(version => <li key={version.id}><b>Version {version.versionNumber}</b><p>{dateLabel(version.updatedAt)} · {version.changeSummary}</p>
            {version.id !== dbq.currentVersionId && <details><summary>Read earlier version</summary>{Object.entries(version.values).map(([key,value]) => <p key={key}><b>{detail.fields.find(field => field.key === key)?.label ?? key}</b><br />{String(value ?? '')}</p>)}</details>}
          </li>)}</ul>
        </details>}
      </StageBlock>
    </div>
    <div hidden={section !== 'evidence'}><StageBlock title="Linked evidence"><EvidenceRecords evidence={detail.evidence} /></StageBlock></div>
    <div hidden={section !== 'diagnostics'}><StageBlock title="Diagnostic orders" description="Record reviewed summaries and source references for the reports in this examination.">
      {detail.diagnostics.map(item => <DiagnosticEditor key={item.order.id} diagnostic={item} />)}{!detail.diagnostics.length && <p className="stage-muted">This exam has no diagnostic orders.</p>}
    </StageBlock></div>
    <div className="stage-action-footer">
      {draft && <p className="stage-muted">Save the current edits before attesting to the report.</p>}
      {feedback?.ok && detail.errors.length > 0 && !draft && <ul className="stage-list">{detail.errors.map(error => <li key={error}>{error}</li>)}</ul>}
      {!diagnosticsReady && <p className="stage-muted">Required diagnostic review is pending.</p>}
      <ActionFeedback result={feedback} />
      {section === 'report' && <>
        {attestation && !currentAttestation && <p className="stage-muted" role="status">The saved report changed. Review it and attest again.</p>}
        <label className="stage-attestation"><input type="checkbox" checked={Boolean(currentAttestation)} disabled={!ready || pending}
          onChange={event => setAttestation(event.target.checked && savedVersion
            ? { versionId: savedVersion.id, draftRevision: savedVersion.version } : undefined)} />
          I attest to the accuracy of this saved report revision.</label>
        <StageActions>
          <button type="button" className="intake-text-action" disabled={pending} onClick={() => setReturnOpen(v => !v)}>
            <ArrowLeft size={15} /> {returnOpen ? 'Cancel return' : 'Send back to Stage 3 (Rework)'}
          </button>
          <button type="button" className="intake-primary" disabled={!ready || !currentAttestation || pending}
          onClick={() => {
            if (ready && currentAttestation) {
              void execute(() => completeClinicalReview(dbq.id, { ...currentAttestation, confirmed: true }),
                result => onSigned(resultEntityId(result.value)));
            }
          }}>Record sign-off and send to QA <ArrowRight size={16} /></button>
        </StageActions>
        {returnOpen && (
          <div className="sched-coordination-box" style={{ marginTop: 10 }}>
            <label className="intake-field">
              Reason for returning to Stage 3 (Scheduling)
              <input
                type="text"
                value={returnReason}
                maxLength={500}
                onChange={e => setReturnReason(e.target.value)}
                placeholder="e.g. Reschedule examiner, wrong modality/facility, or missing diagnostic capability…"
                disabled={pending}
              />
            </label>
            <StageActions>
              <button
                type="button"
                className="intake-secondary"
                disabled={pending}
                onClick={() => void execute(
                  () => returnClinicalToScheduling(detail.examId || dbq.id, returnReason.trim() || 'Returned from Stage 4 for Stage 3 scheduling rework.'),
                  () => { setReturnOpen(false); setReturnReason(''); onReturned(dbq.esrId); },
                )}
              >
                <ArrowLeft size={15} /> Confirm Return to Stage 3
              </button>
            </StageActions>
          </div>
        )}
      </>}
    </div>
  </>;
}

export function ClinicalPage(props: StageProps) {
  const [handoff, setHandoff] = useState<{ stage: 'Internal QA' | 'Scheduling'; esrId: string; entityId?: string }>();
  return <StageWorkbench {...props} stage="Clinical Evaluation" title="Clinical work queue" description="Review the examination, complete the report and release the signed version to Internal QA."
    handoff={handoff && <StageHandoff stage={handoff.stage} esrId={handoff.esrId} entityId={handoff.entityId} onNavigate={props.onNavigate}>
      {handoff.stage === 'Scheduling' ? 'Returned to Stage 3 (Scheduling) for rework' : 'Signed report sent to Internal QA'}
    </StageHandoff>}
    render={(detail: ClinicalDetail) => (
      <ClinicalReport
        detail={detail}
        onSigned={entityId => setHandoff({ stage: 'Internal QA', esrId: detail.dbq.esrId, entityId })}
        onReturned={esrId => setHandoff({ stage: 'Scheduling', esrId })}
      />
    )} />;
}
