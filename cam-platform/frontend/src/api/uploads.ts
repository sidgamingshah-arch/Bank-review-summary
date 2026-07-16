import { api } from './client';
import type { CaseDocument, DocumentOrigin } from './types';

/**
 * Uploads exactly ONE file per request, per the contract (FR-C02 / NFR-07).
 * Multi-select is fanned out by the callers, awaiting each upload in turn.
 */
export function uploadCaseDocument(
  caseId: string,
  file: File,
  origin: DocumentOrigin = 'upload',
  periodLabel?: string,
): Promise<CaseDocument> {
  const form = new FormData();
  form.append('file', file);
  form.append('origin', origin);
  if (periodLabel) form.append('period_label', periodLabel);
  return api.postForm<CaseDocument>(`/api/cases/${caseId}/documents`, form);
}
