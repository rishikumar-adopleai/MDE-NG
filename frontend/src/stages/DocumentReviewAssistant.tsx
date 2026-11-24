import { useEffect, useId, useRef, useState } from 'react';
import { Bot, Check, CheckCircle2, ChevronRight, Paperclip, RefreshCw, X } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { DocumentReviewDecision } from '../domain/model';
import { Status } from './shared';
import { documentName, formatWorkspaceCopy } from './presentation';

const checklistLabels = ['Document evidence', 'Document readiness', 'Staff verification'];

export function DocumentReviewAssistant({ esrId }: { esrId: string }) {
  const { state, runtime, reviewIntakeDocuments } = useWorkspace();
  const [pending, setPending] = useState<'review' | 'decision'>();
  const [error, setError] = useState('');
  const [sourceId, setSourceId] = useState<string>();
  const request = useRef<AbortController | undefined>(undefined);
  const sourcePanel = useId();
  const sourceTitle = useRef<HTMLHeadingElement>(null);
  const sourceButton = useRef<HTMLButtonElement>(null);
  const item = state?.aiInsightsById[`document-review-${esrId}`];
  const review = item?.documentReview;
  const checklist = review?.format === 'checklist';
  const selectedSource = review?.sources.find(source => source.id === sourceId);

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (sourceId) sourceTitle.current?.focus(); }, [sourceId]);

  async function submit(decision?: DocumentReviewDecision) {
    if (request.current || (decision && !review)) return;
    const controller = new AbortController();
    request.current = controller;
    setPending(decision ? 'decision' : 'review');
    setError('');
    try {
      await reviewIntakeDocuments(esrId, controller.signal, decision && review
        ? { reviewId: review.reviewId, decision } : undefined);
      if (!decision && !controller.signal.aborted) setSourceId(undefined);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'The review request failed.');
    } finally {
      request.current = undefined;
      if (!controller.signal.aborted) setPending(undefined);
    }
  }

  return <details className="intake-subdetails">
    <summary><ChevronRight size={16} aria-hidden="true" /> Document review suggestion</summary>
    <section className="ai-block intake-document-review" aria-label="Document review assistant">
      <div className="ai-block-head">
        <span><Bot size={18} aria-hidden="true" /></span>
        <div><b>Document Review Assistant</b><small>Staff review</small></div>
        {review && <Status text="Suggestion" tone="violet" />}
      </div>
      {review && <ul>{[review.overview, ...review.review_points].map((point, index) => <li key={index}>
        <CheckCircle2 size={15} aria-hidden="true" />
        <span>{checklist && <strong>{checklistLabels[index]} · </strong>}{formatWorkspaceCopy(point.text)}</span>
      </li>)}</ul>}
      {review && <details className="intake-review-sources">
        <summary className="citation-toggle"><Paperclip size={13} aria-hidden="true" />
          {review.sources.length} evidence {review.sources.length === 1 ? 'reference' : 'references'}
          <ChevronRight size={14} aria-hidden="true" />
        </summary>
        <div className="citation-list">{review.sources.map(source => <button type="button" key={source.id}
          aria-controls={sourcePanel} aria-expanded={source.id === sourceId} onClick={event => {
            sourceButton.current = event.currentTarget;
            setSourceId(current => current === source.id ? undefined : source.id);
          }}>{documentName(source.label)}<ChevronRight size={13} aria-hidden="true" /></button>)}</div>
        <div id={sourcePanel} hidden={!selectedSource} className="stage-ai-source">
          {selectedSource && <><div className="stage-ai-source-heading">
            <h4 tabIndex={-1} ref={sourceTitle}>{documentName(selectedSource.label)}</h4>
            <button type="button" className="intake-icon-button" aria-label="Close source" onClick={() => {
              setSourceId(undefined);
              sourceButton.current?.focus();
            }}><X size={17} aria-hidden="true" /></button>
          </div><pre>{selectedSource.excerpt}</pre>
          </>}
        </div>
      </details>}
      {runtime?.aiEnabled ? <button type="button" className="intake-secondary intake-review-generate"
        disabled={Boolean(pending)} onClick={() => void submit()}>
        <RefreshCw size={15} aria-hidden="true" />
        {pending === 'review' ? 'Reviewing documents…' : checklist ? 'Run review again' : review ? 'Run three-point review' : 'Review documents'}
      </button> : <p>AI review is unavailable in this workspace.</p>}
      {error && <p className="stage-ai-error" role="alert">{error}</p>}
      {item && review && <div className="human-controls">
        <button type="button" disabled={Boolean(pending)} className={item.decision === 'accepted-for-human-review' ? 'selected' : ''}
          aria-pressed={item.decision === 'accepted-for-human-review'} onClick={() => void submit('accepted-for-human-review')}>
          <Check size={14} aria-hidden="true" />Accept for review</button>
        <button type="button" disabled={Boolean(pending)} className={item.decision === 'rejected' ? 'selected' : ''}
          aria-pressed={item.decision === 'rejected'} onClick={() => void submit('rejected')}>
          <X size={14} aria-hidden="true" />Reject</button>
      </div>}
      {item?.decision === 'accepted-for-human-review' && <p className="ai-disposition accepted" role="status">Accepted for staff review.</p>}
      {item?.decision === 'rejected' && <p className="ai-disposition rejected" role="status">Review rejected.</p>}
    </section>
  </details>;
}
