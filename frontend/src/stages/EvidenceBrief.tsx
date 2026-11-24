import { useId } from 'react';
import { ChevronRight } from 'lucide-react';
import { buildEvidenceBrief } from './evidenceBriefModel';

export { buildEvidenceBrief };

export function EvidenceBrief({ brief, sourceName, administrative }: {
  brief: ReturnType<typeof buildEvidenceBrief>; sourceName: string; administrative?: boolean;
}) {
  const heading = useId();
  return <section className="evidence-brief" aria-labelledby={heading}>
    <header><h4 id={heading}>{brief.section ? 'Report summary' : 'Document summary'}</h4>{brief.generated && <small>AI-generated</small>}</header>
    <p className="evidence-intro">{brief.paragraph}</p>
    {brief.points.length > 0 && <><strong className="evidence-label">Key points</strong><ul>{brief.points.map((point, index) => <li key={index}>{point}</li>)}</ul></>}
    {brief.more.length > 0 && <details className="evidence-disclosure"><summary><ChevronRight size={15} />Remaining summary points</summary><ul>{brief.more.map((point, index) => <li key={index}>{point}</li>)}</ul></details>}
    {brief.issues.length > 0 && <div className="evidence-attention"><strong className="evidence-label">Needs review</strong><ul>{brief.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></div>}
    {brief.sources.length > 0 && <details className="evidence-disclosure evidence-original"><summary><ChevronRight size={15} aria-hidden="true" />View source text <span>({brief.sources.length} {brief.sources.length === 1 ? 'excerpt' : 'excerpts'})</span></summary>
      <div className="evidence-reader" role="region" tabIndex={0} aria-label={`Source excerpts for ${brief.title}`}>
        <strong className="evidence-filename">{sourceName}</strong>
        {brief.sources.map((source, index) => <blockquote key={index}><b>{source.page == null ? 'Extracted text' : `${administrative ? 'Contention' : 'Page'} ${source.page}`}</b><p>{source.text}</p></blockquote>)}
      </div>
    </details>}
  </section>;
}
