import { useState } from 'react';
import { ArrowRight, ClipboardCheck, FileText, ListChecks } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { QAFinding } from '../domain/model';
import type { qaView } from '../../server/stageViews';
import { EvidenceRecords } from './ClinicalPage';
import { formVersionLabel, formatWorkspaceCopy } from './presentation';
import { ActionFeedback, dateLabel, resultEntityId, StageActions, StageBlock, StageHandoff, StageWorkbench, useStageAction, type StageProps } from './StageWorkbench';
import { Status } from './shared';

type QADetail = ReturnType<typeof qaView>;
const labelFor = (value: string) => value.replaceAll('-', ' ');

function ReviewerFinding({ finding, blockedByCheck, onCorrection }: { finding: QAFinding; blockedByCheck: boolean; onCorrection: (entityId?: string) => void }) {
  const { requestCorrection, resolveQAFinding } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [comment, setComment] = useState('');
  const actionable = ['open', 'correction-requested'].includes(finding.status);
  return <li>
    <div className="stage-card-header"><b>{finding.title}</b><Status text={finding.severity} tone={finding.severity === 'blocking' ? 'red' : 'amber'} /></div>
    <p>{finding.sectionReference} · {labelFor(finding.status)} · {finding.origin === 'deterministic' ? 'Rule check' : 'Reviewer finding'}</p>
    {finding.comments.length > 0 && <p>{finding.comments.at(-1)}</p>}
    <details><summary>{actionable ? 'Review finding' : 'Disposition history'}</summary>
      <ol className="stage-list">{finding.resolutionHistory.map((event, index) => <li key={index}>{dateLabel(event.at)} · {labelFor(event.status)} · {event.comment}</li>)}</ol>
      {actionable && <>
        <label className="intake-field">Disposition note<textarea rows={2} maxLength={3000} value={comment} onChange={event => setComment(event.target.value)} /></label>
        <StageActions>
          {finding.status === 'open' && <button type="button" className="intake-secondary" disabled={pending || !comment.trim()} onClick={() => void execute(() => requestCorrection(finding.id, comment), result => onCorrection(resultEntityId(result.value)))}>Return to Clinical</button>}
          <button type="button" className="intake-secondary" disabled={pending || !comment.trim() || blockedByCheck} onClick={() => void execute(() => resolveQAFinding(finding.id, comment), () => setComment(''))}>Resolve finding</button>
        </StageActions>
      </>}
      <ActionFeedback result={feedback} />
    </details>
  </li>;
}

function ReviewReport({ detail, onApproved, onCorrection }: { detail: QADetail; onApproved: (packageReady: boolean, entityId?: string) => void; onCorrection: (entityId?: string) => void }) {
  const { runQAChecklist, approveQAReview, addQAFinding } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const { review, dbq, current, fields } = detail;
  const [section, setSection] = useState<'report' | 'checks' | 'findings'>('report');
  const [comparison, setComparison] = useState('');
  const [approvalNote, setApprovalNote] = useState('');
  const [finding, setFinding] = useState({ title: '', sectionReference: '', severity: 'blocking' as QAFinding['severity'], issueCategory: 'evidence-consistency' as QAFinding['issueCategory'], comment: '' });
  const prior = detail.versions.find(item => item.id === comparison);
  const checks = review.checks ?? [];
  const approve = () => void execute(() => approveQAReview(review.id, approvalNote), result => {
    const value = result.value;
    onApproved(Boolean(value && typeof value === 'object' && 'packageReady' in value && value.packageReady), resultEntityId(value));
  });
  return <>
    <nav className="intake-sections" aria-label="QA work sections">
      <button type="button" aria-pressed={section === 'report'} onClick={() => setSection('report')}><FileText size={16} />Signed report</button>
      <button type="button" aria-pressed={section === 'checks'} onClick={() => setSection('checks')}><ClipboardCheck size={16} />Checks <span>{checks.length}</span></button>
      <button type="button" aria-pressed={section === 'findings'} onClick={() => setSection('findings')}><ListChecks size={16} />Findings <span>{detail.findings.length}</span></button>
    </nav>
    {section === 'report' && <>
      <StageBlock title="Signed revision" description={`${dbq.formType} · Report revision ${current.versionNumber} · ${dbq.catalogReference ? `Catalog revision ${dbq.catalogReference.revisionId}` : formVersionLabel(current.formVersion)}`}>
        {dbq.localAttestation && <p className="stage-muted">{dbq.localAttestation.examinerName} · {dbq.localAttestation.credentials.join(', ')} · {dateLabel(dbq.localAttestation.at)}</p>}
        {detail.versions.length > 1 && <label className="intake-field">Compare with earlier revision<select value={comparison} onChange={event => setComparison(event.target.value)}><option value="">Current revision only</option>{detail.versions.filter(item => item.id !== current.id).map(item => <option key={item.id} value={item.id}>Revision {item.versionNumber}</option>)}</select></label>}
        <ul className="stage-records">{fields.map(field => <li key={field.key}>
          <b>{field.label}</b><p>{String(current.values[field.key] ?? '—')}</p>
          {prior && prior.values[field.key] !== current.values[field.key] && <p className="stage-muted">Revision {prior.versionNumber} · {String(prior.values[field.key] ?? '—')}</p>}
        </li>)}</ul>
      </StageBlock>
      <StageBlock title="Linked source evidence"><EvidenceRecords evidence={detail.evidence} /></StageBlock>
    </>}
    {section === 'checks' && <StageBlock title="Version checks" description="Check the signed revision for completeness, sign-off, evidence, form version and diagnostics.">
      {!!checks.length && <ul className="stage-records">{checks.map(check => <li key={check.key}><div className="stage-card-header"><b>{formatWorkspaceCopy(check.label)}</b><Status text={check.status === 'passed' ? 'Passed' : 'Needs correction'} tone={check.status === 'passed' ? 'green' : 'red'} /></div><p>{formatWorkspaceCopy(check.detail)}</p></li>)}</ul>}
      <StageActions><button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => runQAChecklist(review.id))}>Run QA checklist</button></StageActions>
    </StageBlock>}
    {section === 'findings' && <StageBlock title="Reviewer findings">
      {detail.findings.length > 0 ? <ul className="stage-records">{detail.findings.map(item => <ReviewerFinding key={item.id} finding={item} onCorrection={onCorrection}
        blockedByCheck={item.origin === 'deterministic' && checks.some(check => check.key === item.checkKey && check.status === 'failed')} />)}</ul> : <p className="stage-muted">No findings recorded for this review.</p>}
      <details><summary>Add finding</summary>
        <form onSubmit={event => { event.preventDefault(); void execute(() => addQAFinding(review.id, finding), () => setFinding({ ...finding, title: '', sectionReference: '', comment: '' })); }}>
          <div className="stage-field-grid">
            <label className="intake-field">Title<input required maxLength={180} value={finding.title} onChange={event => setFinding({ ...finding, title: event.target.value })} /></label>
            <label className="intake-field">Report section<input required maxLength={180} value={finding.sectionReference} onChange={event => setFinding({ ...finding, sectionReference: event.target.value })} /></label>
            <label className="intake-field">Severity<select value={finding.severity} onChange={event => setFinding({ ...finding, severity: event.target.value as QAFinding['severity'] })}>{detail.severities.map(value => <option key={value}>{value}</option>)}</select></label>
            <label className="intake-field">Category<select value={finding.issueCategory} onChange={event => setFinding({ ...finding, issueCategory: event.target.value as QAFinding['issueCategory'] })}>{detail.categories.map(value => <option key={value} value={value}>{labelFor(value)}</option>)}</select></label>
          </div>
          <label className="intake-field">Reviewer note<textarea required rows={2} maxLength={3000} value={finding.comment} onChange={event => setFinding({ ...finding, comment: event.target.value })} /></label>
          <StageActions><button type="submit" className="intake-secondary" disabled={pending}>Record finding</button></StageActions>
        </form>
      </details>
    </StageBlock>}
    <div className="stage-action-footer">
      {detail.blockers.length > 0 && <ul className="stage-list">{detail.blockers.map(blocker => <li key={blocker}>{blocker}</li>)}</ul>}
      {!detail.blockers.length && <label className="intake-field">Independent reviewer decision<textarea rows={2} maxLength={3000} value={approvalNote} onChange={event => setApprovalNote(event.target.value)} /></label>}
      <ActionFeedback result={feedback} />
      <StageActions><button type="button" className="intake-primary" disabled={pending || detail.blockers.length > 0 || !approvalNote.trim()} onClick={approve}>Approve signed revision <ArrowRight size={16} /></button></StageActions>
    </div>
  </>;
}

export function QAPage(props: StageProps) {
  const [handoff, setHandoff] = useState<{ esrId: string; type: 'approved' | 'correction' | 'ready'; entityId?: string }>();
  return <StageWorkbench {...props} stage="Internal QA" title="QA review queue" description="Check the signed report against linked evidence, resolve findings and approve the revision."
    handoff={handoff && (handoff.type === 'approved' ? <p className="triage-success" role="status">Revision approved · Package readiness depends on the remaining reports for {handoff.esrId}.</p> :
      <StageHandoff stage={handoff.type === 'ready' ? 'VA Delivery & Review' : 'Clinical Evaluation'} esrId={handoff.esrId} entityId={handoff.entityId} onNavigate={props.onNavigate}>
        {handoff.type === 'ready' ? 'Approved reports ready for package validation' : 'Selected report returned for correction'}
      </StageHandoff>)}
    render={(detail: QADetail) => <ReviewReport detail={detail} onCorrection={entityId => setHandoff({ esrId: detail.review.esrId, type: 'correction', entityId })}
      onApproved={(packageReady, entityId) => setHandoff({ esrId: detail.review.esrId, type: packageReady ? 'ready' : 'approved', entityId })} />} />;
}
