import { HeadingText } from '../HeadingText';
import { useState } from 'react';
import { Activity, ArrowRight, FileText, ListChecks } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { StageKey } from '../domain/model';
import type { deliveryView } from '../../server/stageViews';
import { formatStatus, formatWorkspaceCopy } from './presentation';
import { ActionFeedback, dateLabel, StageActions, StageBlock, StageHandoff, StageWorkbench, useStageAction, type StageProps } from './StageWorkbench';
import { Status } from './shared';

type DeliveryDetail = ReturnType<typeof deliveryView>;
type Response = 'accepted' | 'report-fix' | 'new-exam-requested';
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
        <option value="accepted">Accepted</option><option value="report-fix">Report fix requested</option><option value="new-exam-requested">New examination requested</option>
      </select></label>
      {response !== 'accepted' && <label className="intake-field">{response === 'report-fix' ? 'Affected report' : 'Affected contention'}<select value={target} onChange={event => setTarget(event.target.value)}>
        <option value="">Select the affected record</option>{options.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}
      </select></label>}
    </div>
    <label className="intake-field">Response details<textarea rows={2} maxLength={3000} value={note} onChange={event => setNote(event.target.value)} /></label>
    <ActionFeedback result={feedback} />
    <StageActions><button type="button" className="intake-primary" disabled={pending || !note.trim() || (response !== 'accepted' && !target)} onClick={save}>Record response <ArrowRight size={16} /></button></StageActions>
  </StageBlock>;
}

function Reconciliation({ detail, onClosed }: { detail: DeliveryDetail; onClosed: () => void }) {
  const { recordSimulatedContentionCancellation, createReconciliationTask, completeReconciliationTask, recordDeliveryReview, closeESR } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [target, setTarget] = useState('');
  const [cancelNote, setCancelNote] = useState('');
  const [staffNote, setStaffNote] = useState('');
  const [taskNote, setTaskNote] = useState('');
  const [resolution, setResolution] = useState<Record<string, string>>({});
  const [closure, setClosure] = useState('');
  const { pack } = detail;
  const cancellable = detail.contentions.filter(item => !['accepted','cancelled'].includes(item.simulatedDisposition ?? 'pending'));
  return <>
    <StageBlock title="Contention dispositions">
      <ul className="stage-records">{detail.contentions.map(item => <li key={item.id}><div className="stage-card-header"><b>{item.title}</b><Status text={labelFor(item.simulatedDisposition ?? 'pending')} tone={item.simulatedDisposition === 'accepted' ? 'green' : 'slate'} /></div>
        {item.simulatedDispositionNote && <p>{item.simulatedDispositionNote}</p>}
      </li>)}</ul>
      {detail.actions.cancel && cancellable.length > 0 && <details><summary>Record contention cancellation</summary>
        <label className="intake-field">Contention<select value={target} onChange={event => setTarget(event.target.value)}><option value="">Select a contention</option>{cancellable.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <label className="intake-field">Cancellation reason<textarea rows={2} maxLength={3000} value={cancelNote} onChange={event => setCancelNote(event.target.value)} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !target || !cancelNote.trim()} onClick={() => void execute(() => recordSimulatedContentionCancellation(pack.esrId, target, cancelNote), () => { setTarget(''); setCancelNote(''); })}>Record cancellation</button></StageActions>
      </details>}
    </StageBlock>
    <StageBlock title="Reconciliation tasks">
      {detail.tasks.length ? <ul className="stage-records">{detail.tasks.map(task => <li key={task.id}><b>{task.details}</b>
        <label className="intake-field">Resolution<textarea rows={2} maxLength={3000} value={resolution[task.id] ?? ''} onChange={event => setResolution({ ...resolution, [task.id]: event.target.value })} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !resolution[task.id]?.trim()} onClick={() => void execute(() => completeReconciliationTask(task.id, resolution[task.id]))}>Complete task</button></StageActions>
      </li>)}</ul> : <p className="stage-muted">No reconciliation tasks are open.</p>}
      <details><summary>Add reconciliation task</summary>
        <label className="intake-field">Follow-up details<textarea rows={2} maxLength={3000} value={taskNote} onChange={event => setTaskNote(event.target.value)} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !taskNote.trim()} onClick={() => void execute(() => createReconciliationTask(pack.id, taskNote), () => setTaskNote(''))}>Add task</button></StageActions>
      </details>
      {pack.preparationStatus === 'prepared-locally' && <details><summary>Record staff review note</summary>
        <label className="intake-field">Staff note<textarea rows={2} maxLength={3000} value={staffNote} onChange={event => setStaffNote(event.target.value)} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !staffNote.trim()} onClick={() => void execute(() => recordDeliveryReview(pack.id, staffNote), () => setStaffNote(''))}>Save review note</button></StageActions>
      </details>}
    </StageBlock>
    <div className="stage-action-footer">
      {detail.closureBlockers.length ? <ul className="stage-list">{detail.closureBlockers.map(reason => <li key={reason}>{formatWorkspaceCopy(reason)}</li>)}</ul> :
        <label className="intake-field">Closure note<textarea rows={2} maxLength={3000} value={closure} onChange={event => setClosure(event.target.value)} /></label>}
      <ActionFeedback result={feedback} />
      <StageActions><button type="button" className="intake-primary" disabled={pending || Boolean(detail.closureBlockers.length) || !closure.trim()} onClick={() => void execute(() => closeESR(pack.esrId, closure), onClosed)}>Close ESR <ArrowRight size={16} /></button></StageActions>
    </div>
  </>;
}

function PackagePanel({ detail, onRework, onClosed }: { detail: DeliveryDetail; onRework: (stage: StageKey, entityId?: string) => void; onClosed: () => void }) {
  const { validateResultsPackage, prepareResultsPackage, simulatePackageTransmission } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [section, setSection] = useState<'package' | 'reconcile' | 'activity'>('package');
  const { pack, actions } = detail;
  return <>
    <nav className="intake-sections" aria-label="Delivery work sections">
      <button type="button" aria-pressed={section === 'package'} onClick={() => setSection('package')}><FileText size={16} />Package</button>
      <button type="button" aria-pressed={section === 'reconcile'} onClick={() => setSection('reconcile')}><ListChecks size={16} />Reconciliation</button>
      <button type="button" aria-pressed={section === 'activity'} onClick={() => setSection('activity')}><Activity size={16} />Activity</button>
    </nav>
    {section === 'package' && <>
      <StageBlock title={`Results package · Revision ${pack.versionNumber}`} description="External transmission is unavailable.">
        <div className="stage-card-header"><Status text={labelFor(pack.preparationStatus)} tone={pack.validationStatus === 'blocked' ? 'amber' : 'slate'} />
          {pack.simulatedResponse && <Status text={labelFor(pack.simulatedResponse)} tone={pack.simulatedResponse === 'accepted' ? 'green' : 'slate'} />}</div>
        {detail.blockers.length > 0 && <ul className="stage-list">{detail.blockers.map(issue => <li key={issue}>{formatWorkspaceCopy(issue)}</li>)}</ul>}
        {!!detail.reports.length && <ul className="stage-records">{detail.reports.map(report => <li key={report.id}><div className="stage-card-header"><b>{report.formType}</b><small>{labelFor(report.status)}</small></div></li>)}</ul>}
        {!!detail.diagnostics.length && <details><summary><HeadingText>Reviewed diagnostic references</HeadingText></summary><ul className="stage-list">{detail.diagnostics.map(item => <li key={item.id}>{item.reference}</li>)}</ul></details>}
        <ActionFeedback result={feedback} />
        <StageActions>
          {actions.validate && <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => validateResultsPackage(pack.id))}>Validate package</button>}
          {actions.prepare && <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => prepareResultsPackage(pack.id))}>Prepare revision</button>}
          {actions.simulate && <button type="button" className="intake-primary" disabled={pending} onClick={() => void execute(() => simulatePackageTransmission(pack.id))}>Start internal delivery review <ArrowRight size={16} /></button>}
          {pack.simulatedResponse === 'accepted' && <button type="button" className="intake-primary" onClick={() => setSection('reconcile')}>Continue to reconciliation <ArrowRight size={16} /></button>}
        </StageActions>
      </StageBlock>
      {actions.respond && <LocalResponse detail={detail} onRework={onRework} />}
    </>}
    {section === 'reconcile' && <Reconciliation detail={detail} onClosed={onClosed} />}
    {section === 'activity' && <StageBlock title="Package revisions and events">
      {pack.submissions?.slice().reverse().map(submission => <details key={submission.sequence}><summary>Review revision {submission.sequence} · {submission.response ? labelFor(submission.response.kind) : 'Awaiting review response'}</summary>
        <p className="stage-muted">{dateLabel(submission.openedAt)} · {submission.manifest.reports.length} reports · {submission.manifest.contentions.length} contentions</p>
        {submission.response && <p className="stage-muted">{submission.response.details}</p>}
      </details>)}
      {detail.timeline.length ? <ol className="stage-records">{detail.timeline.map(event => <li key={event.id}><div className="stage-card-header"><b>{event.eventType === 'simulated-transmission' ? 'Internal delivery review opened' : labelFor(event.eventType)}</b><small>{dateLabel(event.recordedAt)}</small></div><p>{formatWorkspaceCopy(event.details)}</p></li>)}</ol> : <p className="stage-muted">No delivery events recorded for this package.</p>}
    </StageBlock>}
  </>;
}

export function DeliveryPage(props: StageProps) {
  const [notice, setNotice] = useState<{ esrId: string; stage?: StageKey; entityId?: string }>();
  return <StageWorkbench {...props} stage="VA Delivery & Review" title="Delivery queue" description="Prepare approved results, record review responses and reconcile each contention before closing the ESR."
    handoff={notice && (notice.stage ? <StageHandoff stage={notice.stage} esrId={notice.esrId} onNavigate={(stage, esrId) => props.onNavigate(stage, esrId, notice.entityId)}>Rework routed to {notice.stage}</StageHandoff> :
      <p className="triage-success" role="status">ESR closed · {notice.esrId}</p>)}
    render={(detail: DeliveryDetail) => <PackagePanel detail={detail} onClosed={() => setNotice({ esrId: detail.pack.esrId })}
      onRework={(stage, entityId) => setNotice({ esrId: detail.pack.esrId, stage, entityId })} />} />;
}
