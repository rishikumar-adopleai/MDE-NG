import { HeadingText } from '../HeadingText';
import { AlertCircle, ArrowRight, ClipboardList } from 'lucide-react';
import type { IntakeWorkspaceView } from '../domain/model';
import { formatWorkspaceCopy } from './presentation';

type CaseView = IntakeWorkspaceView['viewsByEsrId'][string];

export function IntakeBrief({ view, onExceptions }: { view: CaseView; onExceptions: () => void }) {
  const request = view.vaInstructions;
  const conditions = request?.contentions ?? [];
  const forms = [...new Set([...(request?.requestedDbqs ?? []), ...conditions.flatMap(condition => condition.dbqs ?? [])])];
  const missing = view.missingDocuments ?? [];
  const issues = view.openExceptions + view.escalatedExceptions;
  const organizer = view.aiOrganizer;
  return <div className="intake-brief">
    <section aria-label="VA examination request" className="intake-request-summary">
      <header><span className="intake-kicker"><ClipboardList size={15} /><HeadingText>Request summary</HeadingText></span>{request?.isSimulated && <span className="intake-practice-label">Practice request</span>}</header>
      <h3>{conditions.map(condition => condition.title).join(' · ') || <HeadingText>Requested conditions not provided</HeadingText>}</h3>
      <dl><div><dt>Requested DBQs</dt><dd>{forms.join(', ') || 'Not specified'}</dd></div>
        <div><dt>Service branch</dt><dd>{request?.serviceBranch || 'Not specified'}</dd></div>
        <div><dt>Priority</dt><dd>{request?.priorityHandling?.toLowerCase() || 'Not specified'}</dd></div></dl>
      {request?.specialInstructions ? <details className="intake-request-notes"><summary>Read VA instructions</summary><p>{request.specialInstructions}</p></details> : <small>No additional VA instructions recorded.</small>}
    </section>
    <section className="intake-gap-summary" data-attention={Boolean(missing.length || issues)} aria-label="Missing items and next step">
      <span className="intake-kicker"><AlertCircle size={15} /> <HeadingText>Missing items &amp; next step</HeadingText></span>
      {missing.length ? <><h3>{missing.length} <HeadingText>{missing.length === 1 ? 'Expected document missing' : 'Expected documents missing'}</HeadingText></h3><ul>{missing.map(item => <li key={item.id || item.title}>{item.title || item.id}</li>)}</ul></> : <><h3><HeadingText>{view.manifestStatus === 'verified' ? 'Expected documents received' : 'Document completeness unknown'}</HeadingText></h3><p>{view.manifestStatus === 'verified' ? 'The received records match the request manifest.' : 'No verified document list is available to check against.'}</p></>}
      {(Boolean(view.unreadablePages) || Boolean(organizer?.mismatchCount) || Boolean(organizer?.duplicateCount) || Boolean(organizer?.corruptedCount)) && <ul>
        {view.unreadablePages > 0 && <li>Unreadable pages: {view.unreadablePages}</li>}
        {Boolean(organizer?.mismatchCount) && <li>Claimant mismatches: {organizer?.mismatchCount}</li>}
        {Boolean(organizer?.duplicateCount) && <li>Possible duplicates: {organizer?.duplicateCount}</li>}
        {Boolean(organizer?.corruptedCount) && <li>Damaged files: {organizer?.corruptedCount}</li>}
      </ul>}
      <p><strong>Next:</strong> {view.blocker ? formatWorkspaceCopy(view.blocker) : missing.length ? 'Review the missing items before deciding on handoff.' : 'Review the records, then validate to send to Triage.'}</p>
      {issues > 0 && <button type="button" className="intake-text-action" onClick={onExceptions}>Review exceptions ({issues}) <ArrowRight size={14} /></button>}
    </section>
  </div>;
}
