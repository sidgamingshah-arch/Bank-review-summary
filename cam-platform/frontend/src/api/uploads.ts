import { api } from './client';
import type { CaseDocument, DocumentOrigin, MasterType, MastersBulkReport, MastersTypeReport } from './types';

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

/** Bulk-load masters from a filled-in Excel template. Entries land as drafts. */
export function uploadMastersBulk(file: File): Promise<MastersBulkReport> {
  const form = new FormData();
  form.append('file', file);
  return api.postForm<MastersBulkReport>('/api/masters/bulk-upload', form);
}

/** Bulk-load ONE master type from its Excel template. Entries land as drafts. */
export function uploadMasterTypeXlsx(mtype: MasterType, file: File): Promise<MastersTypeReport> {
  const form = new FormData();
  form.append('file', file);
  return api.postForm<MastersTypeReport>(`/api/masters/${mtype}/xlsx-upload`, form);
}
