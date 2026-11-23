import { useEffect, useId, useRef, useState } from 'react';
import { ChevronRight, FileText, RefreshCw, Sparkles, X } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { AIStageReview } from '../data/repositories/workspaceApi';
import type { StageKey } from '../domain/model';
import { documentName, formatWorkspaceCopy } from './presentation';

const stageReviewCopy: Record<StageKey, { title: string; action: string }> = {
  Intake: { title: 'AI Document Review Assistant', action: 'Review documents' },
  'Medical Triage': { title: 'AI Evidence Summary', action: 'Summarize linked evidence' },
  Scheduling: { title: 'AI Scheduling Summary', action: 'Review scheduling and outreach' },
  'Clinical Evaluation': { title: 'AI Clinical Documentation Review', action: 'Review report and diagnostics' },
  'Internal QA': { title: 'AI Report Quality Review', action: 'Check signed report' },
  'VA Delivery & Review': { title: 'AI Delivery Summary', action: 'Generate summary' },
};

export function StageAIReview({ stage, entityId }: { stage: StageKey; entityId: string }) {
  const { runtime, requestStageReview } = useWorkspace();
  const [review, setReview] = useState<AIStageReview>();
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [sourceId, setSourceId] = useState<string>();
  const currentRequest = useRef<AbortController | undefined>(undefined);
  const citationPanel = useId();
  const sourceHeading = useRef<HTMLHeadingElement>(null);
  const sourceButton = useRef<HTMLButtonElement>(null);
  const configured = runtime?.aiEnabled;
  const copy = stageReviewCopy[stage];
  const selectedSource = review?.sources.find(source => source.id === sourceId);

  useEffect(() => () => currentRequest.current?.abort(), []);
  useEffect(() => { if (sourceId) sourceHeading.current?.focus(); }, [sourceId]);

  const generate = async () => {
    if (currentRequest.current) return;
    const controller = new AbortController();
    currentRequest.current = controller;
    setPending(true);
    setError('');
    try {
      const result = await requestStageReview(stage, entityId, controller.signal);
      if (!controller.signal.aborted) {
        setSourceId(undefined);
        setReview(result);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'AI review is unavailable.');
    } finally {
      currentRequest.current = undefined;
      if (!controller.signal.aborted) setPending(false);
    }
  };

  const citations = (ids: string[], showLabels = false) => <span className={`stage-ai-citations${showLabels ? ' stage-ai-citation-list' : ''}`} aria-label="Sources">
    {[...new Set(ids)].map(id => {
      const index = review?.sources.findIndex(source => source.id === id) ?? -1;
      if (!review || index < 0) return null;
      const sourceLabel = documentName(review.sources[index].label);
      const label = `Source ${index + 1} · ${sourceLabel}`;
      return <button type="button" key={id} aria-label={label} title={label} aria-controls={citationPanel}
        aria-expanded={sourceId === id} onClick={event => {
          sourceButton.current = event.currentTarget;
          setSourceId(current => current === id ? undefined : id);
        }}>{index + 1}{showLabels && <span>{sourceLabel}</span>}</button>;
    })}
  </span>;

  return <details className="stage-ai">
    <summary><Sparkles size={17} aria-hidden="true" /><strong>{copy.title}</strong>
      <ChevronRight size={16} aria-hidden="true" />
    </summary>
    <div className="stage-ai-content">
      {!configured && <p>AI review is unavailable in this workspace.</p>}
      {error && <p className="stage-ai-error" role="alert">{error}</p>}
      {review && <section className="stage-ai-result" aria-label="AI review result">
        <div className="stage-ai-result-main">
          <p className="stage-ai-overview" role="status">{formatWorkspaceCopy(review.overview.text)}</p>
          {configured && <button type="button" className="intake-secondary stage-ai-refresh" disabled={pending}
            aria-label="Regenerate summary" onClick={() => void generate()}>
            <RefreshCw size={15} aria-hidden="true" />{pending ? 'Generating…' : 'Regenerate'}
          </button>}
        </div>
        <details className="stage-ai-sources">
          <summary><FileText size={15} aria-hidden="true" /> Sources ({review.overview.source_ids.length}) <ChevronRight size={14} aria-hidden="true" /></summary>
          {citations(review.overview.source_ids, true)}
        </details>
        {!!review.review_points.length && <details className="stage-ai-followup">
          <summary>Items to verify</summary>
          <ul>{review.review_points.map((point, index) => <li key={index}>{formatWorkspaceCopy(point.text)} {citations(point.source_ids)}</li>)}</ul>
        </details>}
        <div id={citationPanel} hidden={!selectedSource} className="stage-ai-source">
          {selectedSource && <>
            <div className="stage-ai-source-heading">
              <h4 ref={sourceHeading} tabIndex={-1}>{documentName(selectedSource.label)}</h4>
              <button type="button" className="intake-icon-button" aria-label="Close source" onClick={() => {
                setSourceId(undefined);
                sourceButton.current?.focus();
              }}><X size={17} /></button>
            </div>
            <pre>{selectedSource.excerpt}</pre>
          </>}
        </div>
      </section>}
      {configured && !review && <div className="stage-ai-actions">
        {pending && <p role="status">Generating a cited summary.</p>}
        <button type="button" className="intake-secondary" disabled={pending} onClick={() => void generate()}>
          {pending ? 'Generating…' : error ? 'Retry summary' : copy.action}
        </button>
      </div>}
    </div>
  </details>;
}
