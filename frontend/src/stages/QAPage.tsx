import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  ListChecks,
  Lock,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { EvidenceDocument, QAFinding } from '../domain/model';
import type { qaView } from '../../server/stageViews';
import { EvidenceRecords } from './ClinicalPage';
import { SourceDrawer, type SourceTarget } from './TriagePage';
import { formVersionLabel, formatWorkspaceCopy } from './presentation';
import { ActionFeedback, dateLabel, resultEntityId, StageActions, StageBlock, StageHandoff, StageWorkbench, useStageAction, type StageProps } from './StageWorkbench';
import { Status } from './shared';

type QADetail = ReturnType<typeof qaView>;
const labelFor = (value: string) => value.replaceAll('-', ' ');

function ReviewerFinding({
  finding,
  blockedByCheck,
  onCorrection,
  onOpenSource,
}: {
  finding: QAFinding;
  blockedByCheck: boolean;
  onCorrection: (entityId?: string) => void;
  onOpenSource: (target: SourceTarget) => void;
}) {
  const { requestCorrection, resolveQAFinding } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [comment, setComment] = useState('');
  const actionable = ['open', 'correction-requested'].includes(finding.status);
  const originLabel =
    finding.origin === 'deterministic'
      ? 'Rule Check'
      : finding.origin === 'ai-audit'
        ? 'AI Clinical Audit'
        : 'Reviewer Finding';
  const severityTone =
    finding.severity === 'blocking' ? 'red' : finding.severity === 'review' ? 'amber' : 'slate';
  return <li>
    <div className="stage-card-header">
      <b>{formatWorkspaceCopy(finding.title)}</b>
      <Status text={`${finding.severity} · ${originLabel}`} tone={severityTone} />
    </div>
    <p>{formatWorkspaceCopy(finding.sectionReference)} · {labelFor(finding.status)}</p>
    {finding.comments.length > 0 && <p>{finding.comments.at(-1)}</p>}
    {finding.citedSources && finding.citedSources.length > 0 && (
      <div className="triage-citation-row" style={{ marginTop: 6, marginBottom: 6 }}>
        {finding.citedSources.map(src => (
          <button
            key={`${src.sourceId}:${src.documentId}:${src.pageNumber}`}
            type="button"
            className="triage-citation-chip"
            onClick={() =>
              onOpenSource({
                documentId: src.documentId,
                documentTitle: src.documentTitle,
                pageNumber: src.pageNumber,
                citationLabel: src.citationLabel,
                fallbackExcerpt: src.excerpt,
              })
            }
          >
            <FileText size={13} /> [{src.sourceId}] {src.citationLabel}
          </button>
        ))}
      </div>
    )}
    <details><summary>{actionable ? 'Review & disposition finding' : 'Disposition history'}</summary>
      <ol className="stage-list">{finding.resolutionHistory.map((event, index) => <li key={index}>{dateLabel(event.at)} · {labelFor(event.status)} · {event.comment}</li>)}</ol>
      {actionable && <>
        <label className="intake-field">Disposition note<textarea rows={2} maxLength={3000} value={comment} onChange={event => setComment(event.target.value)} /></label>
        <StageActions>
          {finding.status === 'open' && <button type="button" className="intake-secondary" disabled={pending || !comment.trim()} onClick={() => void execute(() => requestCorrection(finding.id, comment), result => onCorrection(resultEntityId(result.value)))}>Return to Clinical</button>}
          <button type="button" className="intake-secondary" disabled={pending || !comment.trim() || blockedByCheck} onClick={() => void execute(() => resolveQAFinding(finding.id, comment), () => setComment(''))}>Resolve finding</button>
          {finding.origin === 'ai-audit' && finding.severity !== 'blocking' && (
            <button
              type="button"
              className="intake-secondary"
              disabled={pending}
              onClick={() =>
                void execute(() =>
                  resolveQAFinding(
                    finding.id,
                    comment.trim() || 'Reviewed AI clinical warning and confirmed report adequacy.',
                  ),
                  () => setComment(''),
                )
              }
            >
              Dismiss advisory
            </button>
          )}
        </StageActions>
      </>}
      <ActionFeedback result={feedback} />
    </details>
  </li>;
}

function ReviewReport({
  detail,
  onApproved,
  onCorrection,
  onOpenSource,
}: {
  detail: QADetail;
  onApproved: (packageReady: boolean, entityId?: string) => void;
  onCorrection: (entityId?: string) => void;
  onOpenSource: (target: SourceTarget) => void;
}) {
  const { runQAChecklist, runQAAudit, approveQAReview, addQAFinding } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const { review, dbq, current, fields } = detail;
  const [section, setSection] = useState<'report' | 'checks' | 'findings'>('report');
  const [comparison, setComparison] = useState('');
  const [approvalNote, setApprovalNote] = useState('');
  const [manualAiConfirmed, setManualAiConfirmed] = useState(false);
  const [finding, setFinding] = useState({ title: '', sectionReference: '', severity: 'blocking' as QAFinding['severity'], issueCategory: 'evidence-consistency' as QAFinding['issueCategory'], comment: '' });
  const autoAuditRequestedRef = useRef<string>('');
  const prior = detail.versions.find(item => item.id === comparison);
  const checks = review.checks ?? [];
  const passedChecksCount = checks.filter(c => c.status === 'passed' || c.status === 'not-applicable').length;
  const openBlockingCount = detail.findings.filter(f => ['blocking', 'review'].includes(f.severity) && ['open', 'correction-requested'].includes(f.status)).length;
  const aiAudit = review.aiAudit;
  const coverage = review.evidenceCoverage;
  const aiNeedsManualOverride = Boolean(aiAudit && (['failed', 'partial'].includes(aiAudit.status ?? '') || aiAudit.manualReviewRequired));
  const quarantinedFiles = review.quarantinedPrivacyFiles ?? [];
  const isAuditing = review.qaProcessing?.status === 'processing' || pending;

  // Trigger Stage 5 audit automatically when a locked review enters QA without full checks yet
  useEffect(() => {
    const sig = `${review.id}:${review.versionId}`;
    if (autoAuditRequestedRef.current === sig) return;
    const needsAudit = checks.length < 12 || !review.ruleVersion || review.isStale || checks.some(c => c.versionId !== review.versionId);
    if (needsAudit && review.qaProcessing?.status !== 'processing') {
      autoAuditRequestedRef.current = sig;
      void execute(() => runQAAudit(review.id, false));
    }
  }, [review.id, review.versionId, review.ruleVersion, review.isStale, checks.length]);

  const approve = () => void execute(() => approveQAReview(review.id, approvalNote, manualAiConfirmed), result => {
    const value = result.value;
    onApproved(Boolean(value && typeof value === 'object' && 'packageReady' in value && value.packageReady), resultEntityId(value));
  });
  return <>
    {review.isVaReportFix && (
      <div className="triage-distress-banner" role="alert" style={{ marginBottom: 12 }}>
        <ShieldAlert size={20} />
        <div>
          <strong>VA Report Fix Requested · Priority Top of Queue</strong>
          <p style={{ margin: '4px 0 0' }}>{review.vaFixNote || 'Returned from VA delivery review for urgent QA verification.'}</p>
        </div>
      </div>
    )}

    {review.isStale && (
      <div className="triage-stale-banner" role="alert" style={{ marginBottom: 12 }}>
        <AlertTriangle size={18} />
        <div>
          <strong>QA Review Stale · Re-evaluation Required</strong>
          <span style={{ display: 'block' }}>{review.staleReason || 'Underlying intake evidence or diagnostic reports changed since last QA audit. Refresh QA Audit before approval.'}</span>
        </div>
      </div>
    )}

    <div className="triage-summary-section" style={{ marginBottom: 14, padding: '14px 16px', background: '#f8fbf9', borderRadius: 12, border: '1px solid #d5e5dd' }}>
      <div className="triage-section-head" style={{ marginBottom: 8 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0, fontSize: 14 }}>
          <Lock size={15} /> Locked Signed Revision {current.versionNumber} · Automated VA Rules &amp; Clinical Audit
        </h3>
        <div className="triage-overview-actions">
          <Status
            text={checks.length ? `${passedChecksCount}/${checks.length} Rules Satisfied` : review.qaProcessing?.status === 'queued' ? 'Audit Queued' : 'Not Audited'}
            tone={checks.length > 0 && passedChecksCount === checks.length && openBlockingCount === 0 && !review.isStale ? 'green' : openBlockingCount > 0 || review.isStale ? 'red' : 'amber'}
          />
          <button
            type="button"
            className="intake-secondary"
            disabled={isAuditing}
            onClick={() => void execute(() => runQAAudit(review.id, true))}
          >
            <RefreshCw size={14} /> {isAuditing ? 'Auditing…' : 'Refresh QA Audit'}
          </button>
        </div>
      </div>
      {dbq.localAttestation && (
        <p className="stage-muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
          <strong>Signing Clinician:</strong> {dbq.localAttestation.examinerName} ({dbq.localAttestation.credentials.join(', ')}) · Independent QA reviewer verification active
        </p>
      )}
      {coverage && (
        <p className="stage-muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
          <strong>Evidence Coverage:</strong> {coverage.processedCount}/{coverage.availableCount} processed · {coverage.relevantToExamCount} relevant to exam · {coverage.aiReviewedCount} AI-reviewed ({coverage.batchesSucceeded}/{coverage.batchesPlanned} batches)
          {coverage.humanInspectionRequiredCount > 0 ? ` · ${coverage.humanInspectionRequiredCount} human inspection needed` : ''}
          {coverage.unavailableOrUnreadableCount > 0 ? ` · ${coverage.unavailableOrUnreadableCount} unreadable/pending` : ''}
        </p>
      )}
      {aiAudit && (
        <div style={{ marginTop: 8, padding: '10px 12px', background: '#fff', borderRadius: 8, border: aiNeedsManualOverride ? '1px solid #f59e0b' : '1px solid #e1ebe6' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <strong style={{ fontSize: 12.5, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <Sparkles size={14} /> AI Clinical Audit ({aiAudit.status ?? 'completed'} · {aiAudit.provider || 'Deterministic Rules Only'})
            </strong>
            <span style={{ fontSize: 11, color: '#64748b' }}>Advisory only · Never alters Pass/Fail rules</span>
          </div>
          <p style={{ margin: 0, fontSize: 13 }}>{aiAudit.summary}</p>
          {aiNeedsManualOverride && (
            <p style={{ margin: '6px 0 0', fontSize: 12, color: '#b45309' }}>
              Manual human QA inspection and explicit confirmation are required before approving this signed revision.
            </p>
          )}
          {aiAudit.citedSources && aiAudit.citedSources.length > 0 && (
            <div className="triage-citation-row" style={{ marginTop: 8 }}>
              {aiAudit.citedSources.map(src => (
                <button
                  key={`${src.sourceId}:${src.documentId}:${src.pageNumber}`}
                  type="button"
                  className="triage-citation-chip"
                  onClick={() =>
                    onOpenSource({
                      documentId: src.documentId,
                      documentTitle: src.documentTitle,
                      pageNumber: src.pageNumber,
                      citationLabel: src.citationLabel,
                      fallbackExcerpt: src.excerpt,
                    })
                  }
                >
                  <FileText size={13} /> [{src.sourceId}] {src.citationLabel}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>

    {quarantinedFiles.length > 0 && (
      <div className="triage-stale-banner" role="status" style={{ marginBottom: 14 }}>
        <ShieldAlert size={16} />
        <div>
          <strong>Privacy Guard: Restricted Raw Psychological Material Quarantined (3-Year Local Storage)</strong>
          {quarantinedFiles.map(item => (
            <span key={item.id} style={{ display: 'block' }}>{item.sourceReference}: {item.reason}</span>
          ))}
        </div>
      </div>
    )}

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
        {Boolean((dbq as unknown as { draftMedicalReview?: { summary?: string } }).draftMedicalReview?.summary) && (
          <details style={{ marginTop: 10 }}>
            <summary>Stage 4 Clinician Medical Review Summary</summary>
            <p style={{ marginTop: 6 }}>{(dbq as unknown as { draftMedicalReview?: { summary?: string } }).draftMedicalReview?.summary}</p>
          </details>
        )}
      </StageBlock>
      <StageBlock title="Linked source evidence">
        <div className="triage-citation-row" style={{ marginBottom: 10 }}>
          {detail.evidence.map(doc => (
            <button
              key={doc.id}
              type="button"
              className="triage-citation-chip"
              onClick={() =>
                onOpenSource({
                  documentId: doc.id,
                  documentTitle: doc.title,
                  pageNumber: 1,
                  citationLabel: `${doc.title} · p. 1`,
                })
              }
            >
              <FileText size={13} /> Inspect {doc.title}
            </button>
          ))}
        </div>
        <EvidenceRecords evidence={detail.evidence} />
      </StageBlock>
    </>}
    {section === 'checks' && <StageBlock title="VA Contract & Deterministic Rule Scorecard" description="Checks required fields, clinician sign-off, license/NPI/portability & VA C&P training validity on the exam date, forbidden commentary context, raw/uncertain psych quarantine, 24h/48h labs & safety alerts, ACE remarks, Veteran/ESR identity, and BVA scope.">
      {!!checks.length && <ul className="stage-records">{checks.map(check => {
        const badgeText =
          check.status === 'passed' ? 'Passed' :
          check.status === 'not-applicable' ? 'Not Applicable' :
          check.status === 'human-review-required' ? 'Human Review Required' :
          check.status === 'unable-to-verify' ? 'Unable to Verify' :
          'Failed (Blocking)';
        const badgeTone =
          check.status === 'passed' ? 'green' :
          check.status === 'not-applicable' ? 'slate' :
          check.status === 'failed' ? 'red' :
          'amber';
        return <li key={check.key}>
          <div className="stage-card-header">
            <b>{check.ruleId ? `[${check.ruleId}] ` : ''}{formatWorkspaceCopy(check.label)}</b>
            <Status text={badgeText} tone={badgeTone} />
          </div>
          {check.contractReference && <p className="stage-muted" style={{ fontSize: 11.5 }}>{check.contractReference}{check.affectedSection ? ` · ${check.affectedSection}` : ''}</p>}
          <p>{formatWorkspaceCopy(check.detail)}</p>
        </li>;
      })}</ul>}
      <StageActions>
        <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => runQAChecklist(review.id))}>Re-run rule checks</button>
        <button type="button" className="intake-secondary" disabled={pending} onClick={() => void execute(() => runQAAudit(review.id, true))}>Run full rules + AI audit</button>
      </StageActions>
    </StageBlock>}
    {section === 'findings' && <StageBlock title="Rule Findings, AI Warnings & Manual QA Notes">
      {detail.findings.length > 0 ? <ul className="stage-records">{detail.findings.map(item => <ReviewerFinding key={item.id} finding={item} onCorrection={onCorrection} onOpenSource={onOpenSource}
        blockedByCheck={item.origin === 'deterministic' && checks.some(check => check.key === item.checkKey && check.status === 'failed')} />)}</ul> : <p className="stage-muted">No findings or warnings recorded for this review.</p>}
      <details><summary>Add manual finding</summary>
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
      {(() => {
        const effectiveBlockers = aiNeedsManualOverride && manualAiConfirmed
          ? detail.blockers.filter(b => !b.includes('AI clinical audit failed or was partial'))
          : detail.blockers;
        return <>
          {effectiveBlockers.length > 0 && <ul className="stage-list">{effectiveBlockers.map(blocker => <li key={blocker}>{blocker}</li>)}</ul>}
          {aiNeedsManualOverride && (
            <label className="intake-field" style={{ display: 'flex', flexDirection: 'row', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
              <input type="checkbox" checked={manualAiConfirmed} onChange={event => setManualAiConfirmed(event.target.checked)} style={{ width: 'auto', marginTop: 3 }} />
              <span>I confirm that I manually inspected all clinical facts, signed DBQ fields, diagnostic results, and linked eFolder evidence because AI clinical audit was {aiAudit?.status ?? 'unavailable'}.</span>
            </label>
          )}
          {effectiveBlockers.length === 0 && (
            <label className="intake-field">Independent reviewer decision<textarea rows={2} maxLength={3000} value={approvalNote} onChange={event => setApprovalNote(event.target.value)} placeholder={aiNeedsManualOverride ? 'Document your manual QA evidence review (minimum 10 characters)...' : 'Confirm all contract checks passed and findings are resolved before releasing to Stage 6...'} /></label>
          )}
          <ActionFeedback result={feedback} />
          <StageActions>
            {effectiveBlockers.length === 0 && (
              <span style={{ fontSize: 12, color: '#166534', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <CheckCircle2 size={15} /> Ready for Stage 6 package release
              </span>
            )}
            <button type="button" className="intake-primary" disabled={pending || effectiveBlockers.length > 0 || !approvalNote.trim() || (aiNeedsManualOverride && (!manualAiConfirmed || approvalNote.trim().length < 10))} onClick={approve}>
              Approve signed revision <ArrowRight size={16} />
            </button>
          </StageActions>
        </>;
      })()}
    </div>
  </>;
}

export function QAPage(props: StageProps) {
  const { state } = useWorkspace();
  const [handoff, setHandoff] = useState<{ esrId: string; type: 'approved' | 'correction' | 'ready'; entityId?: string }>();
  const [sourceDrawer, setSourceDrawer] = useState<SourceTarget>();
  const drawerDocs: EvidenceDocument[] = sourceDrawer
    ? Object.values(state?.evidenceDocumentsById ?? {}).filter(
        d => d.id === sourceDrawer.documentId || d.title === sourceDrawer.documentTitle,
      )
    : [];
  return <>
    <StageWorkbench {...props} stage="Internal QA" title="QA review queue" description="Check the signed report against VA contract rules & clinical evidence, inspect AI warnings, and approve or return for correction."
      handoff={handoff && (handoff.type === 'approved' ? <p className="triage-success" role="status">Revision approved · Package readiness depends on the remaining reports for {handoff.esrId}.</p> :
        <StageHandoff stage={handoff.type === 'ready' ? 'VA Delivery & Review' : 'Clinical Evaluation'} esrId={handoff.esrId} entityId={handoff.entityId} onNavigate={props.onNavigate}>
          {handoff.type === 'ready' ? 'Approved reports ready for package validation' : 'Selected report returned for correction'}
        </StageHandoff>)}
      render={(detail: QADetail) => (
        <ReviewReport
          detail={detail}
          onOpenSource={setSourceDrawer}
          onCorrection={entityId => setHandoff({ esrId: detail.review.esrId, type: 'correction', entityId })}
          onApproved={(packageReady, entityId) => setHandoff({ esrId: detail.review.esrId, type: packageReady ? 'ready' : 'approved', entityId })}
        />
      )} />
    {sourceDrawer && (
      <SourceDrawer
        target={sourceDrawer}
        documents={drawerDocs}
        onClose={() => setSourceDrawer(undefined)}
      />
    )}
  </>;
}
