import type {
  AssessmentInput, AssessmentRun, Enterprise, EnterpriseInput, Expression, ExtractPolicyInput,
  ExtractionResult, FactKey, FactUpdate, HealthResponse, PolicyDetail, PolicyDraft,
  PolicyDraftList, PolicyImportMetadata, PolicyList, PublishPolicyInput,
  SaveCriteriaInput, SaveCriteriaResult, TraceResponse, RegisterRequest, RegisterResponse,
  LoginRequest, PasswordChangeRequest, SessionResponse, EnterpriseSummaryList, RunSummaryList,
} from './types';
import { factUnits } from '../demo';
import type { DiscoverySettings, DiscoveryRunList, CandidateList, CandidateDetail, CandidateUpdate } from './discoveryTypes';

const baseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '');
let csrfToken: string | null = null;
let requestGeneration = 0;

export function invalidateRequests() { requestGeneration++; }

export function setCsrfToken(token: string | null) {
  csrfToken = token;
  invalidateRequests();
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
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const safeEvidence = (value: unknown) => isObject(value) && typeof value.quote === 'string' && typeof value.sourceUrl === 'string' && /^https:\/\//i.test(value.sourceUrl) && typeof value.sourceVersion === 'string' && typeof value.textHash === 'string';

const safeDraftSpan = (value: unknown) => safeEvidence(value) && isObject(value) && typeof value.sourceId === 'string' && (value.page === null || (typeof value.page === 'number' && Number.isInteger(value.page) && value.page > 0)) && (value.section === null || typeof value.section === 'string');

export function validExpression(value: unknown, depth = 0): value is Expression {
  if (!isObject(value) || depth > 20 || typeof value.op !== 'string') return false;
  if (value.op === 'and' || value.op === 'or') return Array.isArray(value.children) && value.children.length >= 2 && value.children.every(child => validExpression(child, depth + 1));
  if (value.op === 'not') return validExpression(value.child, depth + 1);
  if (value.op === 'manual_review') return typeof value.reason === 'string';
  const period = value.period;
  if (!(period === 'assessment' || (isObject(period) && typeof period.startDate === 'string' && typeof period.endDate === 'string'))) return false;
  if (value.op === 'industry_exclusion') return value.factKey === 'industry' && Array.isArray(value.excluded) && value.excluded.every(item => typeof item === 'string');
  if (!['lt', 'lte', 'eq', 'gte', 'gt', 'ne'].includes(String(value.comparator))) return false;
  if (value.op === 'date') return ['establishedDate', 'specificMarketSince'].includes(String(value.factKey)) && typeof value.value === 'string';
  if (value.op === 'quarterly_average') return ((value.metric === 'assets' && value.unit === 'CNY') || (value.metric === 'employeeCount' && value.unit === 'person')) && typeof value.value === 'string';
  if (value.op === 'compare') return typeof value.factKey === 'string' && Object.hasOwn(factUnits, value.factKey) && value.unit === factUnits[value.factKey as keyof typeof factUnits] && (typeof value.value === 'string' || typeof value.value === 'boolean');
  return false;
}

const safeCriterion = (criterion: unknown) => isObject(criterion) && typeof criterion.id === 'string' && typeof criterion.title === 'string' && validExpression(criterion.expression) && Array.isArray(criterion.sourceSpans) && criterion.sourceSpans.every(safeDraftSpan) && ['none', 'conflict'].includes(String(criterion.reviewFinding)) && ['pending', 'passed', 'failed'].includes(String(criterion.validationStatus));
const safeFinding = (finding: unknown) => isObject(finding) && typeof finding.code === 'string' && typeof finding.message === 'string' && (finding.criterionId === null || typeof finding.criterionId === 'string') && Array.isArray(finding.sourceSpans) && finding.sourceSpans.every(safeDraftSpan);

function assertShape(value: unknown, kind: string): void {
  if (kind === 'DiscoverySettings' && (!isObject(value) || typeof value.enabled !== 'boolean' || !Array.isArray(value.sourceUrls) || !value.sourceUrls.every(item => typeof item === 'string') || typeof value.intervalMinutes !== 'number' || typeof value.timeZone !== 'string' || !isObject(value.limits) || typeof value.limits.minIntervalMinutes !== 'number' || typeof value.limits.maxIntervalMinutes !== 'number' || typeof value.limits.maxSourceUrls !== 'number')) throw new Error('接口契约错误：发现设置无效');
  if ((kind === 'DiscoveryRunList' || kind === 'CandidateList') && (!isObject(value) || !Array.isArray(value.items) || !(value.nextCursor === null || typeof value.nextCursor === 'string') || !value.items.every(item => isObject(item) && typeof item.id === 'string' && typeof item.status === 'string'))) throw new Error('接口契约错误：发现列表无效');
  if (kind === 'DiscoveryRunList' && isObject(value) && Array.isArray(value.items) && !value.items.every(item => isObject(item) && Array.isArray(item.sourceStatus) && item.sourceStatus.every(isObject))) throw new Error('接口契约错误：运行来源状态无效');
  if (kind === 'CandidateList' && isObject(value) && Array.isArray(value.items) && !value.items.every(item => isObject(item) && typeof item.attachmentCount === 'number' && typeof item.unsupportedAttachmentCount === 'number')) throw new Error('接口契约错误：候选附件信息无效');
  if (kind === 'CandidateDetail' && (!isObject(value) || typeof value.id !== 'string' || typeof value.status !== 'string' || typeof value.bodyText !== 'string' || typeof value.sourceUrl !== 'string' || typeof value.fetchedAt !== 'string' || typeof value.contentHash !== 'string' || !isObject(value.metadata) || !Array.isArray(value.criteria) || !value.criteria.every(safeCriterion) || !Array.isArray(value.validationFindings) || !value.validationFindings.every(safeFinding) || !Array.isArray(value.attachmentUrls) || !value.attachmentUrls.every(item => typeof item === 'string') || !Array.isArray(value.unsupportedAttachmentUrls) || !value.unsupportedAttachmentUrls.every(item => typeof item === 'string') || !Array.isArray(value.missingFields) || !value.missingFields.every(item => typeof item === 'string'))) throw new Error('接口契约错误：候选详情无效');
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`接口契约错误：${kind} 不是对象`);
  const record = value as Record<string, unknown>;
  if (kind === 'SessionResponse' || kind === 'RegisterResponse') {
    const user = record.user;
    if (!isObject(user) || typeof user.id !== 'string' || typeof user.username !== 'string' || !['enterprise', 'reviewer'].includes(String(user.role)) || !(user.enterpriseId === null || typeof user.enterpriseId === 'string')) throw new Error('接口契约错误：账号信息无效');
    if (kind === 'SessionResponse' && (typeof record.csrfToken !== 'string' || record.csrfToken.length < 16 || typeof record.expiresAt !== 'string')) throw new Error('接口契约错误：会话信息无效');
  }
  if (kind === 'EnterpriseSummaryList' || kind === 'RunSummaryList') {
    if (!Array.isArray(record.items) || !(record.nextCursor === null || typeof record.nextCursor === 'string')) throw new Error(`接口契约错误：${kind} 列表无效`);
    for (const item of record.items) {
      if (!isObject(item) || typeof item.id !== 'string') throw new Error(`接口契约错误：${kind} 条目无效`);
      if (kind === 'EnterpriseSummaryList' && (typeof item.nameSynthetic !== 'string' || !['self', 'other', 'unowned'].includes(String(item.ownership)) || !isObject(item.period) || typeof item.period.startDate !== 'string' || typeof item.period.endDate !== 'string')) throw new Error('接口契约错误：企业列表条目无效');
      if (kind === 'RunSummaryList' && (typeof item.enterpriseId !== 'string' || typeof item.status !== 'string' || typeof item.reportAvailable !== 'boolean' || typeof item.createdAt !== 'string' || typeof item.asOf !== 'string')) throw new Error('接口契约错误：运行列表条目无效');
    }
  }
  if (kind === 'PolicyList' || kind === 'PolicyDraftList' || kind === 'TraceResponse') {
    if (!Array.isArray(kind === 'TraceResponse' ? record.steps : record.items)) throw new Error(`接口契约错误：${kind} 缺少列表`);
  }
  if (kind === 'TraceResponse') {
    for (const step of record.steps as unknown[]) {
      if (!isObject(step) || (!Number.isInteger(step.sequence) || (step.sequence as number) < 1) || !['ingestion', 'extraction', 'citation_validation', 'human_review', 'profile', 'retrieval', 'evaluation', 'questioning', 'explanation'].includes(String(step.stage)) || !['ok', 'needs_review', 'retryable_error', 'fatal_error'].includes(String(step.status)) || typeof step.inputSummary !== 'string' || typeof step.outputSummary !== 'string' || typeof step.modelOrRuleVersion !== 'string' || (!Number.isInteger(step.elapsedMs) || (step.elapsedMs as number) < 0) || !Array.isArray(step.objectRefs) || !step.objectRefs.every(ref => isObject(ref) && Object.keys(ref).length === 2 && ['enterprise', 'policy', 'policyVersion'].includes(String(ref.type)) && typeof ref.id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref.id)) || !(step.policyId === null || typeof step.policyId === 'string') || !(step.policyVersion === null || typeof step.policyVersion === 'string') || !(step.errorCode === null || typeof step.errorCode === 'string') || !(step.humanAction === null || typeof step.humanAction === 'string')) throw new Error('接口契约错误：轨迹步骤无效');
    }
  }
  if (kind === 'PolicyDraft') {
    if (typeof record.id !== 'string' || typeof record.version !== 'string' || typeof record.title !== 'string' || typeof record.revision !== 'string' || !['pending_parse', 'pending_review', 'published', 'closed'].includes(String(record.status)) || !Array.isArray(record.sources) || !Array.isArray(record.criteria) || !Array.isArray(record.validationFindings)) throw new Error('接口契约错误：草稿字段缺失');
    for (const source of record.sources) if (!isObject(source) || typeof source.id !== 'string' || typeof source.title !== 'string' || typeof source.text !== 'string' || typeof source.sourceUrl !== 'string') throw new Error('接口契约错误：草稿来源无效');
    for (const criterion of record.criteria) if (!safeCriterion(criterion)) throw new Error('接口契约错误：草稿条件无效');
    for (const finding of record.validationFindings) if (!safeFinding(finding)) throw new Error('接口契约错误：草稿校验结果无效');
  }
  if (kind === 'PolicyDraftList') {
    if (!(record.nextCursor === null || typeof record.nextCursor === 'string')) throw new Error('接口契约错误：草稿游标无效');
    for (const draft of record.items as unknown[]) assertShape(draft, 'PolicyDraft');
  }
  if (kind === 'ExtractionResult' || kind === 'SaveCriteriaResult') {
    assertShape(record.draft, 'PolicyDraft');
    if (!Array.isArray(record.validationFindings) || !Array.isArray(kind === 'ExtractionResult' ? record.criteriaDrafts : record.criteria)) throw new Error(`接口契约错误：${kind} 字段缺失`);
    if (!(record.validationFindings as unknown[]).every(safeFinding) || !((kind === 'ExtractionResult' ? record.criteriaDrafts : record.criteria) as unknown[]).every(safeCriterion)) throw new Error(`接口契约错误：${kind} 条件或校验结果无效`);
    if (kind === 'ExtractionResult' && (!isObject(record.usage) || !['deepseek', 'fixture', 'saved'].includes(String(record.usage.mode)) || !(record.usage.modelVersion === null || typeof record.usage.modelVersion === 'string') || typeof record.usage.inputTokens !== 'number' || typeof record.usage.outputTokens !== 'number' || typeof record.usage.elapsedMs !== 'number' || (record.usage.tokensUnknown !== undefined && typeof record.usage.tokensUnknown !== 'boolean'))) throw new Error('接口契约错误：模型使用记录无效');
  }
  if (kind === 'PolicyList') {
    for (const item of record.items as unknown[]) {
      if (!isObject(item) || typeof item.id !== 'string' || typeof item.title !== 'string' || typeof item.version !== 'string' || !['tax', 'grant'].includes(String(item.type)) || !['published', 'closed'].includes(String(item.status))) throw new Error('接口契约错误：政策列表内容无效');
    }
    if (record.nextCursor !== null && typeof record.nextCursor !== 'string') throw new Error('接口契约错误：政策列表游标无效');
  }
  if (kind === 'Enterprise' && (typeof record.id !== 'string' || !Array.isArray(record.facts))) throw new Error('接口契约错误：Enterprise 字段缺失');
  if (kind === 'PolicyDetail') {
    if (typeof record.id !== 'string' || typeof record.version !== 'string' || typeof record.title !== 'string' || !['published', 'closed'].includes(String(record.status)) || !Array.isArray(record.criteria)) throw new Error('接口契约错误：政策详情字段缺失');
    for (const criterion of record.criteria) {
      if (!isObject(criterion) || typeof criterion.id !== 'string' || typeof criterion.title !== 'string' || !Array.isArray(criterion.sourceSpans) || !criterion.sourceSpans.every(safeDraftSpan)) throw new Error('接口契约错误：政策条件或引用无效');
    }
  }
  if (kind === 'AssessmentRun') {
    if (typeof record.id !== 'string' || !Array.isArray(record.assessments) || !record.policyVersionMap || typeof record.policyVersionMap !== 'object' || Array.isArray(record.policyVersionMap)) throw new Error('接口契约错误：AssessmentRun 字段缺失');
    const policyIds = new Set<string>();
    for (const assessment of record.assessments as Record<string, unknown>[]) {
      if (!isObject(assessment)) throw new Error('接口契约错误：判断结果无效');
      if (!['satisfied', 'not_satisfied', 'insufficient_evidence', 'conflict'].includes(String(assessment.result)) ||
          !['current', 'stale'].includes(String(assessment.validity))) {
        throw new Error('接口契约错误：未知判断状态');
      }
      if (typeof assessment.policyId !== 'string' || !assessment.policyId.trim() || policyIds.has(assessment.policyId.toLowerCase())) throw new Error('接口契约错误：分析结果的政策标识缺失或重复');
      policyIds.add(assessment.policyId.toLowerCase());
      if (!Array.isArray(assessment.criteriaResults)) throw new Error('接口契约错误：缺少条件结果');
      if (!Array.isArray(assessment.missingFacts) || !Array.isArray(assessment.staleReasons) || !Array.isArray(assessment.warnings)) throw new Error('接口契约错误：判断结果字段缺失');
      if (assessment.nextQuestion !== null && assessment.nextQuestion !== undefined) {
        const question = assessment.nextQuestion;
        if (!isObject(question) || typeof question.prompt !== 'string' || typeof question.reason !== 'string' || question.rank !== 1 || !isObject(question.factRef) || typeof question.factRef.key !== 'string' || !Object.hasOwn(factUnits, question.factRef.key) || !isObject(question.factRef.period) || typeof question.factRef.period.startDate !== 'string' || typeof question.factRef.period.endDate !== 'string') throw new Error('接口契约错误：优先问题无效');
      }
      for (const criterion of assessment.criteriaResults as Record<string, unknown>[]) {
        if (!isObject(criterion) || !['satisfied', 'not_satisfied', 'insufficient_evidence', 'conflict'].includes(String(criterion.result)) || !Array.isArray(criterion.factRefs) || !Array.isArray(criterion.evidenceRefs) || !criterion.evidenceRefs.every(safeEvidence)) throw new Error('接口契约错误：条件结果无效');
      }
    }
  }
}

async function request<T>(path: string, kind: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
  const generation = requestGeneration;
  const headers = new Headers(init.headers);
  if (csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes((init.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', csrfToken);
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers, credentials: 'include' });
  } catch {
    throw new ApiRequestError(0, 'NETWORK_ERROR', '无法连接服务，请检查网络或稍后重试。', null);
  }
  if (generation !== requestGeneration) throw new ApiRequestError(0, 'STALE_REQUEST', '请求已被账号切换替代。', null);
  const traceId = response.headers.get('X-Trace-Id');
  const etag = response.headers.get('ETag');
  const contentType = response.headers.get('Content-Type') || '';
  if (!response.ok) {
    let error: unknown;
    try { error = await response.json(); } catch { /* Malformed errors still remain visible. */ }
    const body = error as { error?: { code?: string; message?: string; details?: Record<string, unknown> }; traceId?: string } | undefined;
    if (generation !== requestGeneration) throw new ApiRequestError(0, 'STALE_REQUEST', '请求已被账号切换替代。', null);
    if (path !== '/auth/me' && typeof window !== 'undefined' && (body?.error?.code === 'SESSION_INVALID' || body?.error?.code === 'UNAUTHENTICATED' || body?.error?.code === 'DEMO_SESSION_REQUIRED' || body?.error?.code === 'DEMO_SESSION_INVALID')) window.dispatchEvent(new CustomEvent('huice-auth-error', { detail: body.error.code }));
    throw new ApiRequestError(response.status, body?.error?.code || 'INVALID_ERROR_RESPONSE', body?.error?.message || `服务返回 ${response.status}，错误格式不符合契约。`, body?.traceId || traceId, body?.error?.details || {});
  }
  if (response.status === 204 && kind === 'empty') return { data: undefined as T, etag, traceId };
  if (kind === 'markdown') {
    if (!contentType.includes('text/markdown')) throw new Error('接口契约错误：报告不是 Markdown');
    const data = await response.text();
    if (generation !== requestGeneration) throw new ApiRequestError(0, 'STALE_REQUEST', '请求已被账号切换替代。', null);
    return { data: data as T, etag, traceId };
  }
  if (!contentType.includes('application/json')) throw new Error('接口契约错误：响应不是 JSON');
  let data: unknown;
  try { data = await response.json(); } catch { throw new Error('接口契约错误：JSON 无法解析'); }
  if (generation !== requestGeneration) throw new ApiRequestError(0, 'STALE_REQUEST', '请求已被账号切换替代。', null);
  assertShape(data, kind);
  return { data: data as T, etag, traceId };
}

const json = (value: unknown) => JSON.stringify(value);
const match = (etag: string) => ({ 'If-Match': etag });

export const api = {
  discoverySettings: () => request<DiscoverySettings>('/admin/discovery/settings', 'DiscoverySettings'),
  updateDiscoverySettings: (input: Pick<DiscoverySettings, 'enabled' | 'sourceUrls' | 'intervalMinutes' | 'timeZone'>, etag: string) => request<DiscoverySettings>('/admin/discovery/settings', 'DiscoverySettings', { method: 'PUT', headers: match(etag), body: json(input) }),
  discoveryRuns: (cursor?: string) => request<DiscoveryRunList>(`/admin/discovery/runs${query({ cursor })}`, 'DiscoveryRunList'),
  discoveryCandidates: (status?: string, cursor?: string) => request<CandidateList>(`/admin/policy-candidates${query({ status, cursor })}`, 'CandidateList'),
  discoveryCandidate: (id: string) => request<CandidateDetail>(`/admin/policy-candidates/${segment(id)}`, 'CandidateDetail'),
  updateDiscoveryCandidate: (id: string, input: CandidateUpdate, etag: string) => request<CandidateDetail>(`/admin/policy-candidates/${segment(id)}`, 'CandidateDetail', { method: 'PATCH', headers: match(etag), body: json(input) }),
  submitDiscoveryCandidate: (id: string, etag: string, key: string) => request<PolicyDraft>(`/admin/policy-candidates/${segment(id)}/submit`, 'PolicyDraft', { method: 'POST', headers: { ...match(etag), 'Idempotency-Key': key } }),
  discardDiscoveryCandidate: (id: string, etag: string, reason: string) => request<CandidateDetail>(`/admin/policy-candidates/${segment(id)}/discard`, 'CandidateDetail', { method: 'POST', headers: match(etag), body: json({ reason: reason || null }) }),
  register: (input: RegisterRequest) => request<RegisterResponse>('/auth/register', 'RegisterResponse', { method: 'POST', body: json(input) }),
  login: (input: LoginRequest) => request<SessionResponse>('/auth/login', 'SessionResponse', { method: 'POST', body: json(input) }),
  logout: () => request<void>('/auth/logout', 'empty', { method: 'POST' }),
  currentSession: () => request<SessionResponse>('/auth/me', 'SessionResponse'),
  changePassword: (input: PasswordChangeRequest) => request<void>('/auth/password', 'empty', { method: 'POST', body: json(input) }),
  listEnterprises: (cursor?: string) => request<EnterpriseSummaryList>(`/enterprises${query({ cursor })}`, 'EnterpriseSummaryList'),
  listRuns: (enterpriseId?: string, cursor?: string) => request<RunSummaryList>(`/assessment-runs${query({ enterpriseId, cursor })}`, 'RunSummaryList'),
  getHealth: () => request<HealthResponse>('/health', 'HealthResponse'),
  listPolicies: (filters: { status?: 'published' | 'closed'; type?: 'tax' | 'grant'; region?: string; asOf?: string; cursor?: string; limit?: number } = {}) =>
    request<PolicyList>(`/policies${query(filters)}`, 'PolicyList'),
  getPolicy: (policyId: string, version?: string) => request<PolicyDetail>(`/policies/${segment(policyId)}${query({ version })}`, 'PolicyDetail'),
  createEnterprise: (input: EnterpriseInput) => request<Enterprise>('/enterprises', 'Enterprise', { method: 'POST', body: json(input) }),
  getEnterprise: async (enterpriseId: string) => {
    const response = await request<Enterprise>(`/enterprises/${segment(enterpriseId)}`, 'Enterprise');
    if (response.data.id.toLowerCase() !== enterpriseId.toLowerCase()) throw new Error('接口契约错误：返回的企业与请求不一致');
    return response;
  },
  updateFact: async (enterpriseId: string, key: FactKey, input: FactUpdate, etag: string) => {
    const response = await request<Enterprise>(`/enterprises/${segment(enterpriseId)}/facts/${segment(key)}`, 'Enterprise', { method: 'PATCH', headers: match(etag), body: json(input) });
    if (response.data.id.toLowerCase() !== enterpriseId.toLowerCase()) throw new Error('接口契约错误：返回的企业与请求不一致');
    return response;
  },
  createRun: (input: AssessmentInput) => request<AssessmentRun>('/assessment-runs', 'AssessmentRun', { method: 'POST', body: json(input) }),
  getRun: async (runId: string) => {
    const response = await request<AssessmentRun>(`/assessment-runs/${segment(runId)}`, 'AssessmentRun');
    if (response.data.id.toLowerCase() !== runId.toLowerCase()) throw new Error('接口契约错误：返回的分析记录与请求不一致');
    return response;
  },
  downloadReport: (runId: string) => request<string>(`/assessment-runs/${segment(runId)}/report.md`, 'markdown', { headers: { Accept: 'text/markdown' } }),
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
