import './delivery-sections.css';
import { StageHeading } from './StageHeading';
import { RecordHeader } from './RecordHeader';
import { HeadingText } from '../HeadingText';
import { useState } from 'react';
import { Activity, AlertTriangle, ArrowRight, BarChart3, CheckCircle2, FileText, Inbox, ListChecks, Loader2, RefreshCw, Send } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { ReworkAnalyticsRecord, StageKey } from '../domain/model';
import {
  computeEsrCompletionStatus,
  computeReconciliationFindings,
} from '../services/workflowService';
import type { deliveryView } from '../../server/stageViews';
import { formatStatus, formatWorkspaceCopy } from './presentation';
import { ActionFeedback, dateLabel, StageActions, StageBlock, StageHandoff, StageWorkbench, useStageAction, type StageProps } from './StageWorkbench';
import { Status } from './shared';

type DeliveryDetail = ReturnType<typeof deliveryView>;
type Response = 'accepted' | 'report-fix' | 'new-exam-requested';
type Stage6Section = 'ready' | 'submitted' | 'inbox' | 'in-progress' | 'reconcile';
const labelFor = (value: string) => formatWorkspaceCopy(formatStatus(value));

function LocalResponse({ detail, onRework }: { detail: DeliveryDetail; onRework: (stage: StageKey, entityId?: string) => void }) {
  const { recordSimulatedPackageResponse } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [response, setResponse] = useState<Response>('accepted');
  const [target, setTarget] = useState('');
  const [note, setNote] = useState('');
  const options = response === 'report-fix' ? detail.reportTargets.map(item => ({ id: item.id, title: item.formType })) :
    response === 'new-exam-requested' ? detail.contentionTargets.map(item => ({ id: item.id, title: item.title })) : [];
  const save = () => void execute(() => recordSimulatedPackageResponse(detail.pack.id, response, note, target || undefined), result => {
    setNote('');
    setTarget('');
    const value = result.value;
    const id = value && typeof value === 'object' && 'entityId' in value ? String(value.entityId) : undefined;
    if (response === 'report-fix') onRework('Internal QA', id);
    if (response === 'new-exam-requested') onRework('Scheduling', id);
  });
  return <StageBlock title="Record an internal review response" description="The response applies to the latest revision opened for delivery review.">
    <div className="stage-field-grid">
      <label className="intake-field">Response<select value={response} onChange={event => { setResponse(event.target.value as Response); setTarget(''); }}>
        <option value="accepted">Accepted</option><option value="report-fix">Report fix requested (Internal QA)</option><option value="new-exam-requested">New examination requested</option>
      </select></label>
      {response !== 'accepted' && <label className="intake-field">{response === 'report-fix' ? 'Affected report' : 'Affected contention'}<select value={target} onChange={event => setTarget(event.target.value)}>
        <option value="">Select the affected record</option>{options.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}
      </select></label>}
    </div>
    <label className="intake-field">Response details<textarea rows={2} maxLength={3000} value={note} onChange={event => setNote(event.target.value)} /></label>
    <ActionFeedback result={feedback} pending={pending} pendingText="Saving internal review response…" />
    <StageActions>
      <button type="button" className="intake-primary" disabled={pending || !note.trim() || (response !== 'accepted' && !target)} onClick={save}>
        {pending ? <><Loader2 size={15} className="delivery-spin" /> Saving response…</> : <>Record response <ArrowRight size={16} /></>}
      </button>
    </StageActions>
  </StageBlock>;
}

function Reconciliation({ detail, onRework, onClosed }: { detail: DeliveryDetail; onRework: (stage: StageKey, entityId?: string) => void; onClosed: () => void }) {
  const { state, recordSimulatedContentionCancellation, createReconciliationTask, completeReconciliationTask, recordDeliveryReview, closeESR } = useWorkspace();
  const cancelAction = useStageAction();
  const taskAction = useStageAction();
  const staffAction = useStageAction();
  const closeAction = useStageAction();
  const [target, setTarget] = useState('');
  const [cancelNote, setCancelNote] = useState('');
  const [staffNote, setStaffNote] = useState('');
  const [taskNote, setTaskNote] = useState('');
  const [resolution, setResolution] = useState<Record<string, string>>({});
  const [completingTaskId, setCompletingTaskId] = useState<string>();
  const [closure, setClosure] = useState('');
  const { pack } = detail;
  const cancellable = detail.contentions.filter(item => !['accepted', 'cancelled', 'rap-delivered'].includes(item.simulatedDisposition ?? 'pending'));
  const findings = state ? computeReconciliationFindings(state, pack.esrId) : [];
  const staffNotes = detail.timeline.filter(event => event.eventType === 'staff-reconciliation-recorded');
  const completedTasks = detail.timeline.filter(event => event.eventType === 'reconciliation-task-completed');

  return <>
    {detail.actions.respond && (
      <>
        <div className="triage-stale-banner" role="alert">
          <AlertTriangle size={16} />
          <div><strong>Pending Internal Review Response:</strong> Record the internal review decision below to unlock ESR closure.</div>
        </div>
        <LocalResponse detail={detail} onRework={onRework} />
      </>
    )}
    {findings.length > 0 && (
      <StageBlock title="Reconciliation findings & discrepancies">
        <ul className="stage-records">
          {findings.map(f => (
            <li key={f.id}>
              <div className="stage-card-header">
                <b>{labelFor(f.kind)}</b>
                <Status text={f.severity === 'blocking' ? 'Blocking' : 'Warning'} tone={f.severity === 'blocking' ? 'red' : 'amber'} />
              </div>
              <p>{f.detail}</p>
            </li>
          ))}
        </ul>
      </StageBlock>
    )}
    <StageBlock title="Contention dispositions">
      <ul className="stage-records">{detail.contentions.map(item => <li key={item.id}><div className="stage-card-header"><b>{item.title}</b><Status text={labelFor(item.simulatedDisposition ?? 'pending')} tone={['accepted', 'rap-delivered'].includes(item.simulatedDisposition ?? '') ? 'green' : 'slate'} /></div>
        {item.simulatedDispositionNote && <p>{item.simulatedDispositionNote}</p>}
      </li>)}</ul>
      {detail.actions.cancel && cancellable.length > 0 && <details><summary>Record contention cancellation</summary>
        <label className="intake-field">Contention<select value={target} onChange={event => setTarget(event.target.value)}><option value="">Select a contention</option>{cancellable.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <label className="intake-field">Cancellation reason<textarea rows={2} maxLength={3000} value={cancelNote} onChange={event => setCancelNote(event.target.value)} /></label>
        <ActionFeedback result={cancelAction.feedback} pending={cancelAction.pending} pendingText="Recording contention cancellation…" />
        <StageActions>
          <button type="button" className="intake-secondary" disabled={cancelAction.pending || !target || !cancelNote.trim()} onClick={() => void cancelAction.execute(() => recordSimulatedContentionCancellation(pack.esrId, target, cancelNote), () => { setTarget(''); setCancelNote(''); })}>
            {cancelAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Recording cancellation…</> : 'Record cancellation'}
          </button>
        </StageActions>
      </details>}
    </StageBlock>
    <StageBlock title="Reconciliation tasks">
      {detail.tasks.length ? <ul className="stage-records">{detail.tasks.map(task => <li key={task.id}><b>{task.details}</b>
        <label className="intake-field">Resolution<textarea rows={2} maxLength={3000} value={resolution[task.id] ?? ''} onChange={event => setResolution({ ...resolution, [task.id]: event.target.value })} /></label>
        <StageActions>
          <button
            type="button"
            className="intake-secondary"
            disabled={taskAction.pending || !resolution[task.id]?.trim()}
            onClick={() => {
              setCompletingTaskId(task.id);
              void taskAction.execute(() => completeReconciliationTask(task.id, resolution[task.id]), () => setCompletingTaskId(undefined));
            }}
          >
            {taskAction.pending && completingTaskId === task.id ? <><Loader2 size={14} className="delivery-spin" /> Completing task…</> : 'Complete task'}
          </button>
        </StageActions>
      </li>)}</ul> : <p className="stage-muted">No reconciliation tasks are open.</p>}
      <ActionFeedback result={taskAction.feedback} pending={taskAction.pending} pendingText={completingTaskId ? 'Completing reconciliation task…' : 'Adding reconciliation task…'} />
      <details><summary>Add reconciliation task</summary>
        <label className="intake-field">Follow-up details<textarea rows={2} maxLength={3000} value={taskNote} onChange={event => setTaskNote(event.target.value)} /></label>
        <StageActions>
          <button type="button" className="intake-secondary" disabled={taskAction.pending || !taskNote.trim()} onClick={() => { setCompletingTaskId(undefined); void taskAction.execute(() => createReconciliationTask(pack.id, taskNote), () => setTaskNote('')); }}>
            {taskAction.pending && !completingTaskId ? <><Loader2 size={14} className="delivery-spin" /> Adding task…</> : 'Add task'}
          </button>
        </StageActions>
      </details>
      {completedTasks.length > 0 && (
        <details><summary>Completed reconciliation tasks ({completedTasks.length})</summary>
          <ul className="stage-records">{completedTasks.map(ev => <li key={ev.id}><div className="stage-card-header"><b>Task resolved</b><small>{dateLabel(ev.recordedAt)}</small></div><p>{formatWorkspaceCopy(ev.details)}</p></li>)}</ul>
        </details>
      )}
    </StageBlock>
    <StageBlock title={`Staff review notes (${staffNotes.length})`} description="Saved review notes remain attached to this package and appear in the Activity timeline.">
      {staffNotes.length > 0 ? (
        <ul className="stage-records">
          {staffNotes.map(ev => (
            <li key={ev.id}>
              <div className="stage-card-header">
                <b>Staff review note</b>
                <small>{dateLabel(ev.recordedAt)}</small>
              </div>
              <p>{formatWorkspaceCopy(ev.details)}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="stage-muted">No staff review notes saved yet.</p>
      )}
      <details open={staffNotes.length === 0}><summary>Record staff review note</summary>
        <label className="intake-field">Staff note<textarea rows={2} maxLength={3000} value={staffNote} onChange={event => setStaffNote(event.target.value)} /></label>
        <ActionFeedback result={staffAction.feedback} pending={staffAction.pending} pendingText="Saving staff review note…" />
        <StageActions>
          <button type="button" className="intake-secondary" disabled={staffAction.pending || !staffNote.trim()} onClick={() => void staffAction.execute(() => recordDeliveryReview(pack.id, staffNote), () => setStaffNote(''))}>
            {staffAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Saving review note…</> : 'Save review note'}
          </button>
        </StageActions>
      </details>
    </StageBlock>
    <div className="stage-action-footer">
      {detail.closureBlockers.length ? <ul className="stage-list">{detail.closureBlockers.map(reason => <li key={reason}>{formatWorkspaceCopy(reason)}</li>)}</ul> :
        <label className="intake-field">Closure note<textarea rows={2} maxLength={3000} value={closure} onChange={event => setClosure(event.target.value)} /></label>}
      <ActionFeedback result={closeAction.feedback} pending={closeAction.pending} pendingText="Closing ESR and locking records…" />
      <StageActions>
        <button type="button" className="intake-primary" disabled={closeAction.pending || Boolean(detail.closureBlockers.length) || !closure.trim()} onClick={() => void closeAction.execute(() => closeESR(pack.esrId, closure), onClosed)}>
          {closeAction.pending ? <><Loader2 size={15} className="delivery-spin" /> Closing ESR…</> : <>Close ESR <ArrowRight size={16} /></>}
        </button>
      </StageActions>
    </div>
  </>;
}

function PackagePanel({ detail, onRework, onClosed }: { detail: DeliveryDetail; onRework: (stage: StageKey, entityId?: string) => void; onClosed: () => void }) {
  const {
    ensureResultsPackage, validateResultsPackage, prepareResultsPackage, simulatePackageTransmission,
    uploadContentionDocuments, sendContentionRap, createLinkedReworkESR,
  } = useWorkspace();
  const deliveryAction = useStageAction();
  const legacyAction = useStageAction();
  const reworkAction = useStageAction();
  const [pendingLabel, setPendingLabel] = useState('Processing request…');
  const [section, setSection] = useState<'package' | 'reconcile' | 'activity'>('package');
  const [selectedConts, setSelectedConts] = useState<string[]>([]);
  const [reworkConts, setReworkConts] = useState<string[]>([]);
  const [rwCat, setRwCat] = useState<'insufficient' | 'clarification' | 'both'>('insufficient');
  const [rwNote, setRwNote] = useState('');
  const { pack, actions } = detail;
  const isVirtual = pack.id.startsWith('virtual-pkg-');
  const deliveries = pack.contentionDeliveries ?? {};
  const toggleCont = (cid: string) => setSelectedConts(prev => prev.includes(cid) ? prev.filter(x => x !== cid) : [...prev, cid]);
  const toggleReworkCont = (cid: string) => setReworkConts(prev => prev.includes(cid) ? prev.filter(x => x !== cid) : [...prev, cid]);
  const deliverableIds = detail.deliverableContentionIds ?? [];
  const activeSelected = selectedConts.filter(cid => {
    const rec = deliveries[cid];
    return deliverableIds.includes(cid) && !(rec?.rapTransmissionStatus === 'sent' && rec?.receiptConfirmationStatus === 'confirmed');
  });
  const unconfirmedIds = deliverableIds.filter(cid => {
    const rec = deliveries[cid];
    return !(rec?.rapTransmissionStatus === 'sent' && rec?.receiptConfirmationStatus === 'confirmed');
  });
  const hasUnconfirmedDeliverable = unconfirmedIds.length > 0;
  const needsUploadIds = activeSelected.filter(cid => deliveries[cid]?.documentUploadStatus !== 'uploaded');
  const readyForRapIds = activeSelected.filter(cid => deliveries[cid]?.documentUploadStatus === 'uploaded');
  const hasConfirmedRap = Object.values(deliveries).some(
    d => d.rapTransmissionStatus === 'sent' && d.receiptConfirmationStatus === 'confirmed',
  );
  const esrCompleted = detail.esrCompletionStatus === 'completed';

  return <>
    <nav className="delivery-section-buttons" aria-label="Delivery work sections">
      <button type="button" aria-pressed={section === 'package'} onClick={() => setSection('package')}><FileText size={16} />Contention Delivery &amp; Package</button>
      <button type="button" aria-pressed={section === 'reconcile'} onClick={() => setSection('reconcile')}><ListChecks size={16} />Reconciliation</button>
      <button type="button" aria-pressed={section === 'activity'} onClick={() => setSection('activity')}><Activity size={16} />Activity</button>
    </nav>
    {section === 'package' && <>
      <StageBlock
        title={`Contention-Level RAP Readiness · Overall ESR: ${labelFor(detail.esrCompletionStatus ?? 'pending')}`}
        description="Step 1: Upload signed DBQs & reviewed diagnostics. Step 2: Send RAP to VA. Step 3: Reconcile and close the ESR. (External DAS/VBMS upload is disabled in demo.)"
      >
        {pack.isStale && (
          <div className="triage-stale-banner" role="alert" style={{ marginBottom: 10 }}>
            <AlertTriangle size={16} />
            <div><strong>Package Readiness Stale:</strong> {pack.staleReason}</div>
          </div>
        )}
        <ul className="stage-records">
          {detail.contentions.map(c => {
            const elig = detail.contentionEligibility?.[c.id];
            const rec = deliveries[c.id];
            const isConfirmed = rec?.rapTransmissionStatus === 'sent' && rec?.receiptConfirmationStatus === 'confirmed';
            const canSelect = Boolean(elig?.eligible) && !isConfirmed;
            const checked = isConfirmed || activeSelected.includes(c.id);
            return (
              <li key={c.id}>
                <div className="stage-card-header">
                  <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: canSelect ? 'pointer' : 'default' }}>
                    <input type="checkbox" disabled={!canSelect} checked={checked} onChange={() => toggleCont(c.id)} style={{ width: 'auto' }} />
                    <b>{c.title}</b>
                  </label>
                  <Status
                    text={
                      isConfirmed
                        ? 'RAP Confirmed'
                        : rec?.rapTransmissionStatus === 'failed'
                          ? 'RAP Failed (Retry Ready)'
                          : rec?.documentUploadStatus === 'uploaded'
                            ? 'Docs Uploaded (Ready for RAP)'
                            : rec?.documentUploadStatus === 'failed'
                              ? 'Upload Failed'
                              : elig?.cancelled
                                ? 'Cancelled'
                                : elig?.eligible
                                  ? 'Eligible for Upload'
                                  : 'Pending Exam/QA'
                    }
                    tone={
                      rec?.rapTransmissionStatus === 'sent' || elig?.eligible
                        ? 'green'
                        : rec?.documentUploadStatus === 'failed' || rec?.rapTransmissionStatus === 'failed'
                          ? 'red'
                          : 'amber'
                    }
                  />
                </div>
                {rec && (
                  <p className="stage-muted" style={{ fontSize: 12 }}>
                    Upload: <b>{rec.documentUploadStatus}</b> ({rec.uploadAttempts}x, {rec.uploadedDocumentKeys.length} locked doc key(s)) ·
                    RAP: <b>{rec.rapTransmissionStatus}</b> ({rec.rapAttempts}x) ·
                    Receipt: <b>{rec.receiptConfirmationStatus}</b> ·
                    VA Outcome: <b>{rec.vaReviewOutcome}</b> ·
                    Reconciliation: <b>{rec.reconciliationStatus}</b>
                    {rec.lastError ? ` · Error: ${rec.lastError}` : ''}
                  </p>
                )}
                {elig && !elig.eligible && !elig.cancelled && elig.issues.length > 0 && (
                  <p className="stage-muted" style={{ fontSize: 12 }}>{elig.issues.join(' ')}</p>
                )}
              </li>
            );
          })}
        </ul>
        <ActionFeedback result={deliveryAction.feedback} pending={deliveryAction.pending} pendingText={pendingLabel} />
        {!isVirtual && deliverableIds.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {hasUnconfirmedDeliverable && needsUploadIds.length > 0 && (
              <p className="stage-muted" style={{ fontSize: 12, margin: '0 0 6px' }}>
                Step 1 required first: upload documents before sending RAP.
              </p>
            )}
            {!hasUnconfirmedDeliverable && hasConfirmedRap && (
              <p className="stage-feedback" role="status" style={{ margin: '0 0 8px' }}>
                All eligible contentions are uploaded and RAP confirmed.
              </p>
            )}
            <StageActions>
              {hasUnconfirmedDeliverable && (
                <>
                  <button
                    type="button"
                    className={readyForRapIds.length > 0 && needsUploadIds.length === 0 ? 'intake-secondary' : 'intake-primary'}
                    disabled={deliveryAction.pending || needsUploadIds.length === 0}
                    onClick={() => {
                      setPendingLabel('Uploading signed DBQs and diagnostics…');
                      void deliveryAction.execute(() => uploadContentionDocuments(pack.id, needsUploadIds, false));
                    }}
                  >
                    {deliveryAction.pending && pendingLabel.startsWith('Uploading')
                      ? <><Loader2 size={14} className="delivery-spin" /> Uploading Docs…</>
                      : <><FileText size={14} /> Step 1 · Upload Docs ({needsUploadIds.length})</>}
                  </button>
                  <button
                    type="button"
                    className={readyForRapIds.length > 0 && needsUploadIds.length === 0 ? 'intake-primary' : 'intake-secondary'}
                    disabled={deliveryAction.pending || readyForRapIds.length === 0}
                    onClick={() => {
                      setPendingLabel('Sending RAP and confirming delivery…');
                      void deliveryAction.execute(() => sendContentionRap(pack.id, readyForRapIds, false), () => {
                        setSelectedConts(prev => prev.filter(id => !readyForRapIds.includes(id)));
                      });
                    }}
                  >
                    {deliveryAction.pending && pendingLabel.startsWith('Sending')
                      ? <><Loader2 size={14} className="delivery-spin" /> Sending RAP…</>
                      : <><Send size={14} /> Step 2 · Send / Retry RAP ({readyForRapIds.length})</>}
                  </button>
                </>
              )}
              {esrCompleted && (
                <button type="button" className="intake-primary" onClick={() => setSection('reconcile')}>
                  Step 3 · Continue to Reconciliation &amp; Close ESR <ArrowRight size={15} />
                </button>
              )}
              {!esrCompleted && hasConfirmedRap && (
                <button type="button" className="intake-secondary" onClick={() => setSection('reconcile')}>
                  View Reconciliation &amp; Dispositions <ArrowRight size={15} />
                </button>
              )}
            </StageActions>
          </div>
        )}
        {isVirtual && (
          <StageActions>
            <button
              type="button"
              className="intake-primary"
              disabled={deliveryAction.pending}
              onClick={() => {
                setPendingLabel('Opening results package…');
                void deliveryAction.execute(() => ensureResultsPackage(pack.esrId));
              }}
            >
              {deliveryAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Opening Package…</> : 'Open Results Package'}
            </button>
          </StageActions>
        )}
      </StageBlock>

      <StageBlock
        title={`Included Reports & Package · Revision ${pack.versionNumber}`}
        description="QA-approved DBQs and reviewed diagnostics included in this ESR."
      >
        <div className="stage-card-header">
          <Status
            text={esrCompleted && hasConfirmedRap ? 'Delivered via Contention RAP' : labelFor(pack.preparationStatus)}
            tone={esrCompleted && hasConfirmedRap ? 'green' : pack.validationStatus === 'blocked' ? 'amber' : 'slate'}
          />
          {(pack.simulatedResponse || hasConfirmedRap) && (
            <Status
              text={pack.simulatedResponse ? labelFor(pack.simulatedResponse) : 'RAP Confirmed'}
              tone={pack.simulatedResponse === 'accepted' || hasConfirmedRap ? 'green' : 'slate'}
            />
          )}
        </div>
        {detail.blockers.length > 0 && <ul className="stage-list">{detail.blockers.map(issue => <li key={issue}>{formatWorkspaceCopy(issue)}</li>)}</ul>}
        {!!detail.reports.length && <ul className="stage-records">{detail.reports.map(report => <li key={report.id}><div className="stage-card-header"><b>{report.formType}</b><small>{labelFor(report.status)}</small></div></li>)}</ul>}
        {!!detail.diagnostics.length && <details><summary><HeadingText>Reviewed diagnostic references</HeadingText></summary><ul className="stage-list">{detail.diagnostics.map(item => <li key={item.id}>{item.reference}</li>)}</ul></details>}
        {!hasConfirmedRap && (
          <details open={Boolean(actions.respond || pack.simulatedResponse === 'awaiting-response')} style={{ marginTop: 10 }}>
            <summary style={{ fontSize: 12.5, cursor: 'pointer' }}>Full-package validation &amp; internal review (legacy flow)</summary>
            <div style={{ marginTop: 10 }}>
              <ActionFeedback result={legacyAction.feedback} pending={legacyAction.pending} pendingText="Processing full-package action…" />
              <StageActions>
                {actions.validate && <button type="button" className="intake-secondary" disabled={legacyAction.pending} onClick={() => void legacyAction.execute(() => validateResultsPackage(pack.id))}>{legacyAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Validating…</> : 'Validate package'}</button>}
                {actions.prepare && <button type="button" className="intake-secondary" disabled={legacyAction.pending} onClick={() => void legacyAction.execute(() => prepareResultsPackage(pack.id))}>{legacyAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Preparing…</> : 'Prepare revision'}</button>}
                {actions.simulate && <button type="button" className="intake-primary" disabled={legacyAction.pending} onClick={() => void legacyAction.execute(() => simulatePackageTransmission(pack.id))}>{legacyAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Starting review…</> : <>Start internal delivery review <ArrowRight size={16} /></>}</button>}
                {pack.simulatedResponse === 'accepted' && <button type="button" className="intake-primary" onClick={() => setSection('reconcile')}>Continue to reconciliation <ArrowRight size={16} /></button>}
              </StageActions>
            </div>
          </details>
        )}
      </StageBlock>

      {actions.respond && <LocalResponse detail={detail} onRework={onRework} />}

      {detail.contentions.length > 0 && (
        <StageBlock title="VA Rework Simulator (Post-Delivery)" description="Use only after delivery if VA rejects a report and issues a linked Rework ESR. Original ESR completion history remains preserved.">
          <details open={Boolean(reworkConts.length || rwNote.trim())}>
            <summary style={{ fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>Issue Linked VA Rework ESR (Workflow B)</summary>
            <div style={{ marginTop: 10 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 8 }}>
                {detail.contentions.map(c => (
                  <label key={c.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
                    <input type="checkbox" checked={reworkConts.includes(c.id)} onChange={() => toggleReworkCont(c.id)} style={{ width: 'auto' }} />
                    {c.title}
                  </label>
                ))}
              </div>
              <div className="stage-field-grid">
                <label className="intake-field">Official Rework Category
                  <select value={rwCat} onChange={e => setRwCat(e.target.value as 'insufficient' | 'clarification' | 'both')}>
                    <option value="insufficient">Insufficient</option>
                    <option value="clarification">Clarification</option>
                    <option value="both">Both</option>
                  </select>
                </label>
                <label className="intake-field">VA Special Instructions
                  <textarea rows={2} maxLength={3000} value={rwNote} onChange={e => setRwNote(e.target.value)} placeholder="Describe the VA insufficiency or clarification instructions..." />
                </label>
              </div>
              <ActionFeedback result={reworkAction.feedback} pending={reworkAction.pending} pendingText="Creating linked Rework ESR…" />
              <StageActions>
                <button
                  type="button"
                  className="intake-secondary"
                  disabled={reworkAction.pending || reworkConts.length === 0 || !rwNote.trim()}
                  onClick={() => void reworkAction.execute(() => createLinkedReworkESR({
                    originalEsrId: pack.esrId,
                    affectedContentionIds: reworkConts,
                    reworkCategory: rwCat,
                    vaSpecialInstructions: rwNote,
                    previousPackageId: isVirtual ? undefined : pack.id,
                  }), () => { setRwNote(''); setReworkConts([]); })}
                >
                  {reworkAction.pending ? <><Loader2 size={14} className="delivery-spin" /> Creating Rework ESR…</> : `Create Linked Rework ESR (${reworkConts.length} contention(s))`}
                </button>
              </StageActions>
            </div>
          </details>
        </StageBlock>
      )}
    </>}
    {section === 'reconcile' && <Reconciliation detail={detail} onRework={onRework} onClosed={onClosed} />}
    {section === 'activity' && <StageBlock title="Package revisions and events">
      {pack.submissions?.slice().reverse().map(submission => <details key={submission.sequence}><summary>Review revision {submission.sequence} · {submission.response ? labelFor(submission.response.kind) : submission.rapTransmissionStatus === 'sent' ? 'RAP Confirmed (Immutable)' : 'Awaiting review response'}</summary>
        <p className="stage-muted">{dateLabel(submission.openedAt)} · {submission.manifest.reports.length} reports · {submission.manifest.contentions.length} contentions</p>
        {submission.response && <p className="stage-muted">{submission.response.details}</p>}
      </details>)}
      {detail.timeline.length ? <ol className="stage-records">{detail.timeline.map(event => <li key={event.id}><div className="stage-card-header"><b>{event.eventType === 'simulated-transmission' ? 'Internal delivery review opened' : labelFor(event.eventType)}</b><small>{dateLabel(event.recordedAt)}</small></div><p>{formatWorkspaceCopy(event.details)}</p></li>)}</ol> : <p className="stage-muted">No delivery events recorded for this package.</p>}
    </StageBlock>}
  </>;
}

function ReworkInboxView({
  onNavigate,
  onOpenReady,
  selectedId,
}: {
  selectedId?: string;
  onNavigate: StageProps['onNavigate'];
  onOpenReady: (esrId: string) => void;
}) {
  const { state, assessReworkESR } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [routeByEsr, setRouteByEsr] = useState<Record<string, 'report-correction' | 'additional-exam-deferred' | 'technical-delivery-issue' | 'pending-clarification'>>({});
  const [noteByEsr, setNoteByEsr] = useState<Record<string, string>>({});
  const [clarByEsr, setClarByEsr] = useState<Record<string, string>>({});
  const [defByEsr, setDefByEsr] = useState<Record<string, ReworkAnalyticsRecord['deficiencyCategory']>>({});
  const [stageByEsr, setStageByEsr] = useState<Record<string, ReworkAnalyticsRecord['responsibleStage']>>({});

  const inboxEsrs = Object.values(state?.esrsById ?? {}).filter(
    e => e.esrType === 'rework' && !e.workflowClosedAt && (e.reworkDetails?.assessmentStatus === 'pending-assessment' || e.reworkDetails?.reworkRoute === 'pending-clarification'),
  );

  return (
    <StageBlock
      title={`VA Rework Inbox (${inboxEsrs.length})`}
      description="Incoming Rework ESRs linked to completed original ESRs. Human assessment is required before routing to Stage 4, Stage 6, clarification, or deferred additional examination."
    >
      <ActionFeedback result={feedback} pending={pending} pendingText="Routing Rework ESR…" />
      {inboxEsrs.length === 0 ? <p className="stage-muted">No incoming Rework ESRs are awaiting human assessment.</p> : (
        <ul className="stage-records">
          {inboxEsrs.filter(esr => esr.id === selectedId).map(esr => {
            const rd = esr.reworkDetails!;
            const analytics = Object.values(state?.reworkAnalyticsById ?? {}).find(a => a.esrId === esr.id);
            const route = routeByEsr[esr.id] ?? 'report-correction';
            return (
              <li key={esr.id} style={{ padding: 12, borderRadius: 8, background: '#f8fbf9', border: '1px solid #d5e5dd', marginBottom: 10 }}>
                <div className="stage-card-header">
                  <b>{esr.id} · Original: {rd.originalEsrId} (Rework #{rd.reworkSequence})</b>
                  <Status text={rd.reworkRoute === 'pending-clarification' ? 'Pending VA Clarification' : labelFor(rd.reworkCategory)} tone="amber" />
                </div>
                <p style={{ margin: '6px 0' }}>
                  <strong>VA Instructions:</strong> {rd.vaSpecialInstructions}
                </p>
                <p className="stage-muted" style={{ fontSize: 12 }}>
                  Affected contentions ({rd.affectedContentionIds.length}): {rd.affectedContentionIds.join(', ')} ·
                  Submitted DBQ revisions: {Object.entries(rd.submittedRevisionIds).map(([d, v]) => `${d}@${v}`).join(', ') || 'None'} ·
                  Received {dateLabel(rd.requestReceivedAt)}
                </p>
                {analytics?.aiSuggestion && (
                  <p className="stage-muted" style={{ fontSize: 12 }}>
                    <strong>Advisory Categorization Suggestion:</strong> {labelFor(analytics.aiSuggestion.suggestedDeficiencyCategory)} · Likely origin: {analytics.aiSuggestion.suggestedResponsibleStage}
                  </p>
                )}
                <div className="stage-field-grid" style={{ marginTop: 8 }}>
                  <label className="intake-field">Required Action Route
                    <select value={route} onChange={e => setRouteByEsr({ ...routeByEsr, [esr.id]: e.target.value as typeof route })}>
                      <option value="report-correction">Medical report correction (Stage 4 → Stage 5 → Stage 6)</option>
                      <option value="additional-exam-deferred">Additional examination required (Flag deferred for now)</option>
                      <option value="technical-delivery-issue">Technical delivery issue (Route to Stage 6)</option>
                      <option value="pending-clarification">Unclear VA instructions (Hold for clarification)</option>
                    </select>
                  </label>
                  <label className="intake-field">Deficiency Category (Root-Cause)
                    <select value={defByEsr[esr.id] ?? analytics?.deficiencyCategory ?? 'incomplete-rationale'} onChange={e => setDefByEsr({ ...defByEsr, [esr.id]: e.target.value as ReworkAnalyticsRecord['deficiencyCategory'] })}>
                      <option value="missing-measurement">Missing measurement</option>
                      <option value="incomplete-rationale">Incomplete rationale</option>
                      <option value="contradictory-findings">Contradictory findings</option>
                      <option value="missing-diagnostic">Missing diagnostic</option>
                      <option value="technical-upload">Technical upload</option>
                      <option value="unclear-instructions">Unclear instructions</option>
                      <option value="other">Other</option>
                    </select>
                  </label>
                  <label className="intake-field">Responsible Workflow Stage
                    <select value={stageByEsr[esr.id] ?? 'Clinical Evaluation'} onChange={e => setStageByEsr({ ...stageByEsr, [esr.id]: e.target.value as ReworkAnalyticsRecord['responsibleStage'] })}>
                      <option value="Clinical Evaluation">Clinical Evaluation (Stage 4)</option>
                      <option value="Internal QA">Internal QA (Stage 5)</option>
                      <option value="VA Delivery & Review">VA Delivery &amp; Review (Stage 6)</option>
                      <option value="Medical Triage">Medical Triage (Stage 2)</option>
                      <option value="Scheduling">Scheduling (Stage 3)</option>
                      <option value="External VA">External VA</option>
                    </select>
                  </label>
                </div>
                {route === 'pending-clarification' && (
                  <label className="intake-field">Clarification question for VA
                    <textarea rows={2} value={clarByEsr[esr.id] ?? ''} onChange={e => setClarByEsr({ ...clarByEsr, [esr.id]: e.target.value })} />
                  </label>
                )}
                <label className="intake-field">Assessment &amp; corrective action note
                  <textarea rows={2} value={noteByEsr[esr.id] ?? ''} onChange={e => setNoteByEsr({ ...noteByEsr, [esr.id]: e.target.value })} />
                </label>
                <StageActions>
                  <button
                    type="button"
                    className="intake-primary"
                    disabled={pending || !(noteByEsr[esr.id] ?? '').trim() || (route === 'pending-clarification' && !(clarByEsr[esr.id] ?? '').trim())}
                    onClick={() => void execute(() => assessReworkESR(esr.id, {
                      route,
                      assessmentNote: noteByEsr[esr.id] ?? '',
                      deficiencyCategory: defByEsr[esr.id] ?? analytics?.deficiencyCategory,
                      responsibleStage: stageByEsr[esr.id] ?? 'Clinical Evaluation',
                      correctiveAction: noteByEsr[esr.id] ?? '',
                      clarificationRequestNote: clarByEsr[esr.id],
                    }), () => {
                      if (route === 'report-correction') onNavigate('Clinical Evaluation', esr.id);
                      if (route === 'technical-delivery-issue') onOpenReady(esr.id);
                    })}
                  >
                    {pending ? <><Loader2 size={15} className="delivery-spin" /> Routing Rework…</> : <>Confirm Rework Assessment &amp; Route <ArrowRight size={16} /></>}
                  </button>
                </StageActions>
              </li>
            );
          })}
        </ul>
      )}
    </StageBlock>
  );
}

export function DeliveryPage(props: StageProps) {
  const { state, updateReworkRootCause } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [section, setSection] = useState<Stage6Section>('ready');
  const [notice, setNotice] = useState<{ esrId: string; stage?: StageKey; entityId?: string }>();

  const esrs = Object.values(state?.esrsById ?? {});
  const packages = Object.values(state?.resultsPackagesById ?? {});
  const submittedPackages = packages.filter(p => (p.submissions?.length ?? 0) > 0);
  const reworkEsrs = esrs.filter(e => e.esrType === 'rework' && !e.workflowClosedAt);
  const completedEsrs = esrs.filter(e => Boolean(e.workflowClosedAt) || (state && computeEsrCompletionStatus(state, e.id) !== 'pending'));
  const analyticsList = Object.values(state?.reworkAnalyticsById ?? {});

  const [selected, setSelected] = useState<Partial<Record<Stage6Section, string>>>({});
  const inbox = reworkEsrs.filter(e => e.reworkDetails?.assessmentStatus === 'pending-assessment' || e.reworkDetails?.reworkRoute === 'pending-clarification');
  const caseRow = (id: string, esrId: string, kind: string) => {
    const esr = state?.esrsById[esrId];
    const vet = esr ? state?.veteransById[esr.veteranId] : undefined;
    const baseRef = vet?.claimIdentifier || esr?.practiceScenario?.requestReference || esr?.sourceReference || esrId;
    const reference = esr?.esrType === 'rework' ? `${baseRef} · Rework #${esr.reworkDetails?.reworkSequence ?? 1}` : baseRef;
    return { id, title: vet?.displayName || esrId, reference, kind };
  };
  const sectionNames = { ready: 'Ready for Delivery', submitted: 'Submitted Results', inbox: 'VA Rework Inbox',
    'in-progress': 'Rework in Progress', reconcile: 'Completed, Analytics & Reconciliation' };
  const rows = section === 'submitted' ? submittedPackages.map(p => {
    const count = p.submissions?.length ?? 0;
    return caseRow(p.id, p.esrId, `Package · ${count} submission${count === 1 ? '' : 's'}`);
  })
    : section === 'reconcile' ? [
      ...completedEsrs.map(e => caseRow('case-' + e.id, e.id, 'Completed ESR')),
      ...analyticsList.map(a => caseRow('analytics-' + a.id, a.esrId, 'Root-cause review')),
    ] : (section === 'inbox' ? inbox : reworkEsrs).map(e => caseRow(e.id, e.id, labelFor(e.reworkDetails?.reworkRoute ?? 'pending-assessment')));
  const active = rows.find(row => row.id === selected[section]) ?? rows[0];

  return (
    <div className="delivery-hub">
      <nav className="delivery-section-buttons" aria-label="Stage 6 delivery hub">
        <button type="button" aria-pressed={section === 'ready'} onClick={() => setSection('ready')}><Send size={15} /> Ready for Delivery</button>
        <button type="button" aria-pressed={section === 'submitted'} onClick={() => setSection('submitted')}><CheckCircle2 size={15} /> Submitted Results ({submittedPackages.length})</button>
        <button type="button" aria-pressed={section === 'inbox'} onClick={() => setSection('inbox')}><Inbox size={15} /> VA Rework Inbox ({inbox.length})</button>
        <button type="button" aria-pressed={section === 'in-progress'} onClick={() => setSection('in-progress')}><RefreshCw size={15} /> Rework in Progress ({reworkEsrs.length})</button>
        <button type="button" aria-pressed={section === 'reconcile'} onClick={() => setSection('reconcile')}><BarChart3 size={15} /> Completed, Analytics &amp; Reconciliation ({completedEsrs.length})</button>
      </nav>

      {section === 'ready' && (
        <StageWorkbench {...props} stage="VA Delivery & Review" title="Delivery queue" description="Upload versioned documents, transmit contention-level RAPs, assess VA Rework ESRs, and reconcile each contention before ESR closure."
          handoff={notice && (notice.stage ? <StageHandoff stage={notice.stage} esrId={notice.esrId} onNavigate={(stage, esrId) => props.onNavigate(stage, esrId, notice.entityId)}>Rework routed to {notice.stage}</StageHandoff> :
            <p className="triage-success" role="status">ESR closed · {notice.esrId}</p>)}
          render={(detail: DeliveryDetail) => <PackagePanel detail={detail} onClosed={() => setNotice({ esrId: detail.pack.esrId })}
            onRework={(stage, entityId) => setNotice({ esrId: detail.pack.esrId, stage, entityId })} />} />
      )}

      {section !== 'ready' && <div className="intake-page operations-page queue-workbench delivery-section">
        <StageHeading stage="VA Delivery & Review" description={sectionNames[section]} />
        <div className="intake-board">
          <aside className="intake-queue-card" aria-label={sectionNames[section]}>
            <div className="intake-queue-heading"><h2>{section === 'reconcile' ? 'Reconciliation queue' : sectionNames[section]}</h2><span>{rows.length} {rows.length === 1 ? 'item' : 'items'}</span></div>
            <ul className="intake-queue-list">{rows.map(row => <li key={row.id}>
              <button type="button" className="intake-queue-row" aria-pressed={row.id === active?.id}
                onClick={() => setSelected(current => ({ ...current, [section]: row.id }))}>
                <span className="intake-queue-row-main"><strong>{row.title}</strong><small>{row.reference} · {row.kind}</small></span>
                <ArrowRight size={15} aria-hidden="true" />
              </button>
            </li>)}</ul>
            {!rows.length && <p className="intake-queue-empty">No records in this queue.</p>}
          </aside>
          <section className="intake-case-card triage-case-workspace queue-detail delivery-section-detail" tabIndex={0} aria-label={active?.title ?? 'Record details'}>
            {active ? (
              <RecordHeader name={active.title} initials={active.title.split(/\s+/).slice(0, 2).map(word => word.charAt(0)).join('')}
                metadata={active.reference} summary={active.kind} />
            ) : (
              <div className="stage-block"><p className="stage-muted">Select an item from the list to view details.</p></div>
            )}
      {section === 'submitted' && (
        <StageBlock title="Submitted Results & Immutable Delivery History" description="Immutable snapshots of all delivered contention RAPs and full packages. Original ESR history is preserved even when VA issues a linked Rework ESR.">
          {submittedPackages.length === 0 ? <p className="stage-muted">No RAP deliveries or review submissions recorded yet.</p> : (
            <ul className="stage-records">
              {submittedPackages.filter(pack => pack.id === active?.id).map(pack => {
                const esr = state?.esrsById[pack.esrId];
                return (
                  <li key={pack.id}>
                    <div className="stage-card-header">
                      <b>{pack.esrId} · Package {pack.id}</b>
                      <Status text={labelFor(esr?.esrCompletionStatus ?? pack.esrCompletionStatus ?? 'pending')} tone={esr?.esrCompletionStatus === 'completed' ? 'green' : 'slate'} />
                    </div>
                    {(pack.submissions ?? []).map(sub => (
                      <p key={sub.sequence} className="stage-muted" style={{ fontSize: 12 }}>
                        Submission #{sub.sequence} · {dateLabel(sub.openedAt)} · {sub.manifest.contentions.length} contention(s) · {sub.manifest.reports.map(r => `${r.dbqId}@${r.revisionId}`).join(', ')} · RAP: {sub.rapTransmissionStatus ?? 'legacy-sim'} · Receipt: {sub.receiptConfirmationStatus ?? 'pending'}
                      </p>
                    ))}
                    {(esr?.childReworkEsrIds?.length ?? 0) > 0 && (
                      <p className="stage-muted" style={{ fontSize: 12 }}>Linked Rework ESRs: {esr?.childReworkEsrIds?.join(', ')}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </StageBlock>
      )}

      {section === 'inbox' && (
        <ReworkInboxView
          selectedId={active?.id}
          onNavigate={props.onNavigate}
          onOpenReady={esrId => {
            props.onReset();
            props.onNavigate('VA Delivery & Review', esrId);
            setSection('ready');
          }}
        />
      )}

      {section === 'in-progress' && (
        <StageBlock title="Rework in Progress" description="Tracks linked Rework ESRs moving through clinical correction (Stage 4), QA validation (Stage 5), Stage 6 technical delivery, or deferred exam hold.">
          {reworkEsrs.length === 0 ? <p className="stage-muted">No active linked Rework ESRs.</p> : (
            <ul className="stage-records">
              {reworkEsrs.filter(esr => esr.id === active?.id).map(esr => {
                const rd = esr.reworkDetails;
                const dbqs = Object.values(state?.dbqsById ?? {}).filter(d => d.esrId === esr.id);
                const nextStage: StageKey = dbqs.some(d => ['ready-for-qa', 'in-qa'].includes(d.status))
                  ? 'Internal QA'
                  : dbqs.some(d => ['draft', 'in-progress', 'awaiting-clinician-review', 'correction-required'].includes(d.status))
                    ? 'Clinical Evaluation'
                    : 'VA Delivery & Review';
                const targetLabel = rd?.reworkRoute === 'pending-clarification' ? 'VA Rework Inbox' : nextStage;
                return (
                  <li key={esr.id}>
                    <div className="stage-card-header">
                      <b>{esr.id} (Original: {esr.linkedOriginalEsrId})</b>
                      <Status text={labelFor(rd?.reworkRoute ?? 'pending-assessment')} tone={rd?.reworkRoute === 'additional-exam-deferred' ? 'amber' : 'slate'} />
                    </div>
                    <p>{rd?.vaSpecialInstructions}</p>
                    <p className="stage-muted" style={{ fontSize: 12 }}>
                      Route: {labelFor(rd?.reworkRoute ?? 'pending-assessment')} · Active stage: {nextStage} · DBQs: {dbqs.length}
                    </p>
                    <StageActions>
                      <button
                        type="button"
                        className="intake-secondary"
                        onClick={() => {
                          if (rd?.reworkRoute === 'pending-clarification') {
                            setSection('inbox');
                            return;
                          }
                          if (nextStage === 'VA Delivery & Review') {
                            props.onReset();
                            props.onNavigate('VA Delivery & Review', esr.id);
                            setSection('ready');
                            return;
                          }
                          props.onNavigate(nextStage, esr.id);
                        }}
                      >
                        Open {targetLabel} <ArrowRight size={14} />
                      </button>
                    </StageActions>
                  </li>
                );
              })}
            </ul>
          )}
        </StageBlock>
      )}

      {section === 'reconcile' && (
        <div>
          {active?.id.startsWith('analytics-') && <StageBlock title={`Rework Root-Cause Analytics (${analyticsList.length})`} description="Structured deficiency tracking for quality improvement. Advisory AI suggestions never modify VA rules or system prompts.">
            <ActionFeedback result={feedback} pending={pending} pendingText="Updating rework analytics…" />
            {analyticsList.length === 0 ? <p className="stage-muted">No rework analytics records captured yet.</p> : (
              <ul className="stage-records">
                {analyticsList.filter(rec => active?.id === `analytics-${rec.id}`).map(rec => (
                  <li key={rec.id}>
                    <div className="stage-card-header">
                      <b>{rec.esrId} · Original {rec.originalEsrId}</b>
                      <Status text={labelFor(rec.resolutionOutcome)} tone={rec.resolutionOutcome === 'resolved-accepted' ? 'green' : 'amber'} />
                    </div>
                    <p>{rec.vaReworkReason}</p>
                    <p className="stage-muted" style={{ fontSize: 12 }}>
                      DBQs: {rec.dbqFormTypes.join(', ') || 'Unspecified'} · Deficiency: {labelFor(rec.deficiencyCategory)} · Origin Stage: {rec.responsibleStage} · Corrective Action: {rec.correctiveAction}
                    </p>
                    {rec.resolutionOutcome === 'pending' && (
                      <StageActions>
                        <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => updateReworkRootCause(rec.id, { resolutionOutcome: 'resolved-accepted' }))}>
                          Mark Resolution Verified
                        </button>
                      </StageActions>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </StageBlock>}

          {active?.id.startsWith("case-") && <StageBlock title={`Completed & Reconciled ESRs (${completedEsrs.length})`}>
            {completedEsrs.length === 0 ? <p className="stage-muted">No completed ESRs yet.</p> : (
              <ul className="stage-records">
                {completedEsrs.filter(esr => active?.id === `case-${esr.id}`).map(esr => {
                  const findings = state ? computeReconciliationFindings(state, esr.id) : [];
                  return (
                    <li key={esr.id}>
                      <div className="stage-card-header">
                        <b>{esr.id}{esr.esrType === 'rework' ? ` (Rework of ${esr.linkedOriginalEsrId})` : ''}</b>
                        <Status text={labelFor(esr.workflowDisposition ?? (state ? computeEsrCompletionStatus(state, esr.id) : 'completed'))} tone="green" />
                      </div>
                      <p className="stage-muted" style={{ fontSize: 12 }}>
                        {esr.closureNote || 'Completed'}
                        {(esr.childReworkEsrIds?.length ?? 0) > 0 ? ` · Child Rework ESRs: ${esr.childReworkEsrIds?.join(', ')}` : ''}
                        {findings.length > 0 ? ` · Discrepancies: ${findings.map(f => f.detail).join('; ')}` : ' · Reconciled cleanly'}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
          </StageBlock>}
        </div>
      )}
          </section>
        </div>
      </div>}
    </div>
  );
}
