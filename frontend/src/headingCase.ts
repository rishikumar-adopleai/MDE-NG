const acronyms = new Map(['ACE', 'AI', 'BVA', 'DBQ', 'DBQs', 'EMS', 'ESR', 'ESRs', 'NLP', 'QA', 'VA']
  .map(word => [word.toLowerCase(), word] as const));

export function titleCaseHeading(label: string): string {
  return label.replace(/\p{L}+(?:['’]\p{L}+)*/gu, word =>
    acronyms.get(word.toLowerCase()) ?? word[0].toUpperCase() + word.slice(1).toLowerCase());
}
