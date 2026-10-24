import type {
  AssessmentInput, AssessmentRun, Enterprise, EnterpriseInput, ExtractPolicyInput,
  ExtractionResult, FactKey, FactUpdate, HealthResponse, PolicyDetail, PolicyDraft,
  PolicyDraftList, PolicyImportMetadata, PolicyList, PublishPolicyInput,
  SaveCriteriaInput, SaveCriteriaResult, TraceResponse,
} from './types';

const baseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '');
let adminToken: string | null = null;

export function setAdminToken(token: string | null) {
  adminToken = token;
}

export class ApiRequestError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly traceId: string | null,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

export type ApiResponse<T> = { data: T; etag: string | null; traceId: string | null };

function query(values: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  return search.size ? `?${search}` : '';
}

const segment = (value: string) => encodeURIComponent(value);

function assertShape(value: unknown, kind: string): void {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`接口契约错误：${kind} 不是对象`);
  const record = value as Record<string, unknown>;
  if (kind === 'PolicyList' || kind === 'PolicyDraftList' || kind === 'TraceResponse') {
    if (!Array.isArray(kind === 'TraceResponse' ? record.steps : record.items)) throw new Error(`接口契约错误：${kind} 缺少列表`);
  }
  if (kind === 'Enterprise' && (typeof record.id !== 'string' || !Array.isArray(record.facts))) throw new Error('接口契约错误：Enterprise 字段缺失');
  if (kind === 'AssessmentRun') {
    if (typeof record.id !== 'string' || !Array.isArray(record.assessments)) throw new Error('接口契约错误：AssessmentRun 字段缺失');
    for (const assessment of record.assessments as Record<string, unknown>[]) {
      if (!['satisfied', 'not_satisfied', 'insufficient_evidence', 'conflict'].includes(String(assessment.result)) ||
          !['current', 'stale'].includes(String(assessment.validity))) {
        throw new Error('接口契约错误：未知判断状态');
      }
    }
  }
}

async function request<T>(path: string, kind: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
  const headers = new Headers(init.headers);
  if (adminToken && (path.startsWith('/admin/') || path.endsWith('/trace'))) headers.set('Authorization', `Bearer ${adminToken}`);
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers, credentials: 'include' });
  } catch {
    throw new ApiRequestError(0, 'NETWORK_ERROR', '无法连接服务，请检查网络或稍后重试。', null);
  }
  const traceId = response.headers.get('X-Trace-Id');
  const etag = response.headers.get('ETag');
  const contentType = response.headers.get('Content-Type') || '';
  if (!response.ok) {
    let error: unknown;
    try { error = await response.json(); } catch { /* Malformed errors still remain visible. */ }
    const body = error as { error?: { code?: string; message?: string; details?: Record<string, unknown> }; traceId?: string } | undefined;
    throw new ApiRequestError(response.status, body?.error?.code || 'INVALID_ERROR_RESPONSE', body?.error?.message || `服务返回 ${response.status}，错误格式不符合契约。`, body?.traceId || traceId, body?.error?.details || {});
  }
  if (kind === 'markdown') {
    if (!contentType.includes('text/markdown')) throw new Error('接口契约错误：报告不是 Markdown');
    return { data: await response.text() as T, etag, traceId };
  }
  if (!contentType.includes('application/json')) throw new Error('接口契约错误：响应不是 JSON');
  let data: unknown;
  try { data = await response.json(); } catch { throw new Error('接口契约错误：JSON 无法解析'); }
  assertShape(data, kind);
  return { data: data as T, etag, traceId };
}

const json = (value: unknown) => JSON.stringify(value);
const match = (etag: string) => ({ 'If-Match': etag });

export const api = {
  getHealth: () => request<HealthResponse>('/health', 'HealthResponse'),
  listPolicies: (filters: { status?: 'published' | 'closed'; type?: 'tax' | 'grant'; region?: string; asOf?: string; cursor?: string; limit?: number } = {}) =>
    request<PolicyList>(`/policies${query(filters)}`, 'PolicyList'),
  getPolicy: (policyId: string, version?: string) => request<PolicyDetail>(`/policies/${segment(policyId)}${query({ version })}`, 'PolicyDetail'),
  createEnterprise: (input: EnterpriseInput) => request<Enterprise>('/enterprises', 'Enterprise', { method: 'POST', body: json(input) }),
  getEnterprise: (enterpriseId: string) => request<Enterprise>(`/enterprises/${segment(enterpriseId)}`, 'Enterprise'),
  updateFact: (enterpriseId: string, key: FactKey, input: FactUpdate, etag: string) =>
    request<Enterprise>(`/enterprises/${segment(enterpriseId)}/facts/${segment(key)}`, 'Enterprise', { method: 'PATCH', headers: match(etag), body: json(input) }),
  createRun: (input: AssessmentInput) => request<AssessmentRun>('/assessment-runs', 'AssessmentRun', { method: 'POST', body: json(input) }),
  getRun: (runId: string) => request<AssessmentRun>(`/assessment-runs/${segment(runId)}`, 'AssessmentRun'),
  downloadReport: (runId: string) => request<string>(`/assessment-runs/${segment(runId)}/report.md`, 'markdown'),
  getTrace: (runId: string) => request<TraceResponse>(`/assessment-runs/${segment(runId)}/trace`, 'TraceResponse'),
  listAdminPolicies: (filters: { status?: string; type?: string; cursor?: string; limit?: number } = {}) =>
    request<PolicyDraftList>(`/admin/policies${query(filters)}`, 'PolicyDraftList'),
  getAdminPolicy: (policyId: string, version?: string) => request<PolicyDraft>(`/admin/policies/${segment(policyId)}${query({ version })}`, 'PolicyDraft'),
  importPolicy: (metadata: PolicyImportMetadata, files: File[]) => {
    const form = new FormData();
    form.append('metadata', json(metadata));
    files.forEach(file => form.append('files', file));
    return request<PolicyDraft>('/admin/policies/import', 'PolicyDraft', { method: 'POST', body: form });
  },
  extractPolicy: (policyId: string, input: ExtractPolicyInput, etag: string, idempotencyKey: string) =>
    request<ExtractionResult>(`/admin/policies/${segment(policyId)}/extract`, 'ExtractionResult', { method: 'POST', headers: { ...match(etag), 'Idempotency-Key': idempotencyKey }, body: json(input) }),
  saveCriteria: (policyId: string, input: SaveCriteriaInput, etag: string) =>
    request<SaveCriteriaResult>(`/admin/policies/${segment(policyId)}/criteria`, 'SaveCriteriaResult', { method: 'PUT', headers: match(etag), body: json(input) }),
  publishPolicy: (policyId: string, input: PublishPolicyInput, etag: string) =>
    request<PolicyDetail>(`/admin/policies/${segment(policyId)}/publish`, 'PolicyDetail', { method: 'POST', headers: match(etag), body: json(input) }),
};
