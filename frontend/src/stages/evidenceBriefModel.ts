import type { EvidenceDocument, LogicalDocument } from '../domain/model';

type ReviewDocument = EvidenceDocument | LogicalDocument;
type Source = { page?: number; text: string };

const normalize = (text: string) => text.replace(/\s+/g, ' ').trim();
const pageCount = (count: number) => `${count} ${count === 1 ? 'page' : 'pages'}`;
const benefitsTitle = /explanation of benefits|\beob\b/i;

function describeRecord(document: ReviewDocument, title: string) {
  if (document.administrativeOnly || ['examination-requests', 'va-request'].includes(document.category)) return 'This is an examination request. Use it to check what was requested, not as a clinical finding.';
  if (benefitsTitle.test(title.replace(/[_-]+/g, ' '))) return 'An explanation of benefits describes how an insurer processed charges for care.';
  if (/\bdbq\b|disability benefits questionnaire/i.test(title.replaceAll('_', ' '))) return 'This is a Disability Benefits Questionnaire (DBQ). The form title identifies its subject; it does not establish a diagnosis or show which fields were completed.';
  if (document.category === 'insurance-financial') return 'This is an insurance or financial record. Review it for billing or coverage information.';
  if (document.category === 'laboratory') return 'This is a laboratory record. Check extracted results against the source, including units and reference ranges.';
  return 'No narrative summary is available. The points below come from the record and its processing details.';
}

function summarySentences(text: string): string[] {
  return text.trim().split(/\n+/).flatMap(line => {
    const pieces = normalize(line.replace(/^\s*(?:[-•*]|\d+[.)])\s+/, '')).split(/(?<=[.!?])\s+(?=[A-Z“"])/);
    const sentences: string[] = [];
    for (const piece of pieces.filter(Boolean)) {
      if (sentences.length && /(?:\b(?:Dr|Mr|Ms|Mrs|vs|No|Fig|St)|\b[A-Z]|\b[A-Z]\.[A-Z])\.$/i.test(sentences.at(-1)!)) sentences[sentences.length - 1] += ` ${piece}`;
      else sentences.push(piece);
    }
    return sentences;
  });
}

export function buildEvidenceBrief(document: ReviewDocument, parent?: EvidenceDocument, contentionId?: string) {
  const logical = 'sectionId' in document;
  const file = logical ? parent : document;
  const status = file?.classificationStatus ?? document.classificationStatus;
  const pending = status === 'queued' || status === 'processing';
  const processing = file?.processing;
  const tagged = file?.syntheticExcerpts ?? [];
  const linked = contentionId ? tagged.filter(item => item.contentionId === contentionId) : tagged;
  const noMatch = !!contentionId && (tagged.length ? !linked.length : !!file?.contentionIds?.length && !file.contentionIds.includes(contentionId));
  const fileSources = processing?.sources ?? [];
  const selected = (contentionId && tagged.length) || !fileSources.length
    ? linked.map(item => ({page: item.pageNumber, text: item.text})) : fileSources;
  const sources: Source[] = [];
  for (const source of noMatch ? [] : selected) {
    if (!source.text?.trim() || (logical && document.pageCount > 0 && (source.page < document.startPage || source.page > document.endPage))) continue;
    if (!sources.some(item => item.page === source.page && item.text === source.text)) sources.push(source);
  }
  const title = (!logical && document.sections?.length === 1 && document.sections[0].title) || document.title;
  const summary = (logical ? document.summary : processing?.summary)?.trim();
  const generated = !pending && !noMatch && processing?.provider === 'ai' && !processing.aiUnavailable && !!summary;
  const sentences = generated ? summarySentences(summary) : [];
  if (!pending && !noMatch && !sources.length && summary && processing?.provider === 'local' && !processing.error && (file?.sections?.length ?? 0) <= 1) sources.push({text: summary});
  const paragraph = pending ? 'This document is awaiting processing. A summary is not available yet.'
    : noMatch ? 'No excerpt is linked to this contention. A document-wide summary is not shown in its place.'
    : sentences[0] || describeRecord(document, title);
  const points = sentences.slice(1, 4);
  const issues: string[] = [];
  if (!pending && !noMatch) {
    if (!generated) {
      const panels = document.extractedData?.labPanels ?? [];
      for (const panel of panels.slice(0, 2)) points.push(`${panel.panelName}: ${panel.results.length} extracted ${panel.results.length === 1 ? 'result' : 'results'}${panel.collectionDate ? ` · ${panel.collectionDate}` : ''}.`);
      for (const source of sources) {
        if (points.length >= 2 || !source.page) break;
        const lines = source.text.split(/\r?\n/).map(normalize);
        const labeled = lines.filter(value => value.length <= 160 && /^(?:claim status|remark code|date of service|exam date|collection date|specimen|requested forms?|requested contention|document type|findings|impression)\s*:\s*[^:_]+$/i.test(value));
        const notice = benefitsTitle.test(title.replace(/[_-]+/g, ' ')) && source.text.match(/\bthis is not a bill\b/i)?.[0];
        if (notice) labeled.unshift(notice);
        if (!labeled.length) {
          const statement = lines.find(line => line.length <= 190 && /^(?:(?:the )?(?:patient|veteran|claimant) (?:reports?|denies|denied|reported|underwent)|(?:exam(?:ination)?|imaging|testing) (?:showed|found|was)|no evidence of)\b/i.test(line) && /[.!?]$/.test(line));
          if (statement) labeled.push(statement);
        }
        for (const line of labeled.slice(0, 3 - points.length)) points.push(`“${line}” (${file?.category === 'va-request' && file.administrativeOnly ? 'contention' : 'p.'} ${source.page})`);
      }
    }
    if (!points.length) {
      if (logical && document.pageCount) points.push(document.startPage === document.endPage ? `Filed on page ${document.startPage}.` : `Filed on pages ${document.startPage}–${document.endPage}.`);
      else if (!document.administrativeOnly && document.pageCount) points.push(`${pageCount(document.pageCount)} in the source file.`);
      if (!logical && (document.sections?.length ?? 0) > 1) points.push(`${document.sections!.length} reports identified in this file.`);
      if (logical ? document.ocrApplied : document.quality?.ocrApplied) points.push('OCR was used to read scanned text.');
    }
    if (logical) issues.push(...(document.issues ?? []));
    else {
      if (document.unreadablePageCount) issues.push(`${pageCount(document.unreadablePageCount)} could not be read.`);
      if (document.quality?.claimantMatch === 'mismatch') issues.push('The claimant details do not match. Verify the source before relying on it.');
    }
    if ((document.classificationStatus === 'needs-human-review' || (logical && document.actionRequired)) && !issues.length) issues.push('Staff review is required for this document.');
    if (!sources.length) issues.push('No source excerpt is available to verify here.');
  }
  return {title, paragraph, points, issues, sources, more: sentences.slice(4), generated, section: logical && (file?.sections?.length ?? 0) > 1};
}
