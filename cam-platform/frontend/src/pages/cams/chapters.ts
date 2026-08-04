import type { CamSection } from '../../api/types';

/** CAM sections have no chapter field, so the outline groups them
 *  presentationally by keyword-matching the section code/name. First matching
 *  rule wins; anything unmatched falls into "Other sections". This is display
 *  only — it never affects ordering, saving or the API. */
export interface Chapter {
  key: string;
  label: string;
}

const RULES: { key: string; label: string; re: RegExp }[] = [
  { key: 'exec', label: 'Executive summary', re: /exec|summary|recommendation|proposal|overview/ },
  {
    key: 'borrower',
    label: 'Borrower & business',
    re: /borrower|business|profile|management|group|ownership|company|background|industry|market/,
  },
  {
    key: 'financial',
    label: 'Financial analysis',
    re: /financ|ratio|cash|statement|kpi|liquid|leverage|profit|balance|working[_ ]?capital|debt/,
  },
  {
    key: 'facility',
    label: 'Facility & security',
    re: /facilit|structure|security|collateral|covenant|pricing|limit|exposure|guarantee/,
  },
  {
    key: 'risk',
    label: 'Risk & governance',
    re: /risk|rating|grade|policy|conduct|complian|esg|banking|regulat|assurance|consistency|materiality/,
  },
];

/** Chapters in display order (empty ones are omitted at render time). */
export const CHAPTER_ORDER: Chapter[] = [
  ...RULES.map((r) => ({ key: r.key, label: r.label })),
  { key: 'other', label: 'Other sections' },
];

export function chapterKey(section: CamSection): string {
  const hay = `${section.section_code} ${section.name}`.toLowerCase();
  for (const r of RULES) if (r.re.test(hay)) return r.key;
  return 'other';
}
