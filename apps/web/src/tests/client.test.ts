import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiRequestError, invalidateRequests, setCsrfToken, validExpression } from '../api/client';

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });

const response = (body: unknown, status = 200, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'X-Trace-Id': 'trace-1', ...extra } });

describe('HTTP contract boundary', () => {
  it('rejects an enterprise different from the requested identity', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'other', facts: [] })));
    await expect(api.getEnterprise('expected')).rejects.toThrow('返回的企业与请求不一致');
  });
  it('rejects a different run returned for the requested id', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'other', policyVersionMap: {}, assessments: [] })));
    await expect(api.getRun('requested')).rejects.toThrow('返回的分析记录与请求不一致');
  });
  it('rejects missing or duplicate policy identities before counting results', async () => {
    const item = { policyId: 'policy-1', result: 'satisfied', validity: 'current', criteriaResults: [], missingFacts: [], staleReasons: [], warnings: [] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ id: 'run', policyVersionMap: {}, assessments: [{ ...item, policyId: '' }] })).mockResolvedValueOnce(response({ id: 'run', policyVersionMap: {}, assessments: [item, { ...item, policyId: 'POLICY-1' }] })));
    await expect(api.getRun('run')).rejects.toThrow('政策标识缺失或重复');
    await expect(api.getRun('run')).rejects.toThrow('政策标识缺失或重复');
  });
  it('accepts typed trace object references and rejects malformed references', async () => {
    const step = { sequence: 1, stage: 'profile', status: 'ok', inputSummary: '', outputSummary: '', policyId: null, policyVersion: null, modelOrRuleVersion: 'v1', elapsedMs: 0, errorCode: null, humanAction: null };
    const ref = { type: 'enterprise', id: '11111111-1111-4111-8111-111111111111' };
    const fetcher = vi.fn().mockResolvedValueOnce(response({ steps: [{ ...step, objectRefs: [ref] }] })).mockResolvedValueOnce(response({ steps: [{ ...step, objectRefs: [{ ...ref, type: 'unknown' }] }] })).mockResolvedValueOnce(response({ steps: [{ ...step, objectRefs: [{ ...ref, quote: '额外字段' }] }] }));
    vi.stubGlobal('fetch', fetcher);
    expect((await api.getTrace('run')).data.steps[0].objectRefs).toEqual([ref]);
    await expect(api.getTrace('run')).rejects.toThrow('轨迹步骤无效');
    await expect(api.getTrace('run')).rejects.toThrow('轨迹步骤无效');
  });
  it('rejects quarterly rules with incompatible metric and unit', () => {
    const rule = { op: 'quarterly_average', metric: 'assets', period: 'assessment', comparator: 'lte', value: '100', unit: 'CNY' };
    expect(validExpression(rule)).toBe(true);
    expect(validExpression({ ...rule, unit: 'person' })).toBe(false);
    expect(validExpression({ ...rule, metric: 'employeeCount', unit: 'person' })).toBe(true);
    expect(validExpression({ ...rule, metric: 'employeeCount' })).toBe(false);
  });
  it('rejects trace sequence and duration outside the contract', async () => {
    const step = { sequence: 1, stage: 'profile', status: 'ok', inputSummary: '', outputSummary: '', objectRefs: [], policyId: null, policyVersion: null, modelOrRuleVersion: 'v1', elapsedMs: 0, errorCode: null, humanAction: null };
    const invalid = [{ sequence: 0 }, { sequence: 1.5 }, { elapsedMs: -1 }, { elapsedMs: 0.5 }];
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(response({ steps: [{ ...step, ...invalid.shift() }] }))));
    for (let i = 0; i < 4; i++) await expect(api.getTrace('run')).rejects.toThrow('轨迹步骤无效');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ steps: [step] })));
    expect((await api.getTrace('run')).data.steps[0].elapsedMs).toBe(0);
  });
  it('rejects a question with an unknown fact key', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'r', policyVersionMap: {}, assessments: [{ policyId: 'p', result: 'insufficient_evidence', validity: 'current', criteriaResults: [], missingFacts: [], staleReasons: [], warnings: [], nextQuestion: { prompt: '问题', reason: '理由', rank: 1, factRef: { key: 'notAKey', period: { startDate: '2026-01-01', endDate: '2026-12-31' } } } }] })));
    await expect(api.getRun('r')).rejects.toThrow('优先问题无效');
  });
  it('rejects malformed restricted responses before rendering', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ steps: [{ sequence: 1, stage: 'unknown' }] })).mockResolvedValueOnce(response({ items: [], nextCursor: 3 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(api.getTrace('run')).rejects.toThrow('轨迹步骤无效');
    await expect(api.listAdminPolicies()).rejects.toThrow('草稿游标无效');
  });

  it('rejects malformed extraction and save result entries', async () => {
    const draft = { id: 'd', version: 'v1', title: '草稿', revision: 'r', status: 'pending_review', sources: [], criteria: [], validationFindings: [] };
    const usage = { mode: 'fixture', modelVersion: null, inputTokens: 0, outputTokens: 0, elapsedMs: 0 };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ draft, criteriaDrafts: [{ id: 'bad' }], validationFindings: [], usage })).mockResolvedValueOnce(response({ draft, criteria: [], validationFindings: [{ code: 'bad' }] })));
    await expect(api.extractPolicy('d', { version: 'v1' }, '"r"', 'key')).rejects.toThrow('条件或校验结果无效');
    await expect(api.saveCriteria('d', { version: 'v1', criteria: [] }, '"r"')).rejects.toThrow('条件或校验结果无效');
  });

  it('rejects an unknown draft expression instead of losing it in the editor', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'd', version: 'v1', title: '草稿', revision: 'r', status: 'pending_review', sources: [], criteria: [{ id: 'c', title: '条件', expression: { op: 'future_op' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'pending' }], validationFindings: [] })));
    await expect(api.getAdminPolicy('d')).rejects.toThrow('草稿条件无效');
  });

  it('rejects a draft citation missing its source identifier', async () => {
    const draft = { id: 'd', version: 'v1', title: '草稿', revision: 'r', status: 'pending_review', sources: [], criteria: [{ id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [{ sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', quote: '原文', textHash: 'hash', page: 1, section: null }], reviewFinding: 'none', validationStatus: 'pending' }], validationFindings: [] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(draft)));
    await expect(api.getAdminPolicy('d')).rejects.toThrow('草稿条件无效');
  });

  it('encodes queries and includes credentials', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ items: [], nextCursor: null }));
    vi.stubGlobal('fetch', fetcher);
    await api.listPolicies({ type: 'tax', region: '44 0300' });
    expect(fetcher.mock.calls[0][0]).toContain('region=44+0300');
    expect(fetcher.mock.calls[0][1].credentials).toBe('include');
  });

  it('keeps If-Match and body period in their distinct positions', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ id: 'e1', facts: [] }, 200, { ETag: '"version-2"' }));
    vi.stubGlobal('fetch', fetcher);
    const result = await api.updateFact('e1', 'taxableIncome', { value: '0', unit: 'CNY', period: { startDate: '2026-01-01', endDate: '2026-12-31' }, source: 'user_input' }, '"version-1"');
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PATCH');
    expect((init.headers as Headers).get('If-Match')).toBe('"version-1"');
    expect(JSON.parse(init.body as string).value).toBe('0');
    expect(result.etag).toBe('"version-2"');
    setCsrfToken('runtime-csrf-token');
    fetcher.mockResolvedValueOnce(response({ items: [], nextCursor: null }));
    await api.listAdminPolicies();
    expect((fetcher.mock.calls[1][1].headers as Headers).has('Authorization')).toBe(false);
    expect((fetcher.mock.calls[1][1].headers as Headers).has('X-CSRF-Token')).toBe(false);
  });

  it('uses CSRF on session writes and accepts an empty 204 response', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetcher);
    setCsrfToken('runtime-csrf-token');
    await api.changePassword({ currentPassword: 'old-password', newPassword: 'new-password-123' });
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('runtime-csrf-token');
    expect(init.credentials).toBe('include');
    expect((init.headers as Headers).has('Authorization')).toBe(false);
  });

  it('rejects a malformed session before granting a role', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ user: { id: '1', username: 'a', role: 'owner', enterpriseId: null }, csrfToken: '1234567890123456', expiresAt: 'now' })));
    await expect(api.currentSession()).rejects.toThrow('账号信息无效');
  });

  it('rejects a response from before an account change', async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise<Response>(done => { resolve = done; })));
    const pending = api.getEnterprise('old');
    invalidateRequests();
    resolve(response({ id: 'old', facts: [] }));
    await expect(pending).rejects.toMatchObject({ code: 'STALE_REQUEST' });
  });

  it('does not set a JSON content type on multipart uploads', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ id: 'draft', version: 'v1', title: '合成', revision: 'revision', status: 'pending_parse', sources: [], criteria: [], validationFindings: [] }, 201));
    vi.stubGlobal('fetch', fetcher);
    await api.importPolicy({ version: 'demo-v1', title: '合成', type: 'tax', issuer: '演示', region: '440300', documentNumber: null, applicableEntities: [], publishedAt: '2026-01-01', effectiveFrom: '2026-01-01', effectiveTo: null, applicationWindow: null, sourceUrl: 'https://www.gov.cn/', officialEntryUrl: 'https://www.gov.cn/' }, [new File(['body'], 'body.txt')]);
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect((init.headers as Headers).has('Content-Type')).toBe(false);
    expect((init.body as FormData).get('metadata')).toContain('demo-v1');
    expect((init.body as FormData).getAll('files')).toHaveLength(1);
  });

  it('parses an error envelope and never retries a conflict', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ error: { code: 'VERSION_CONFLICT', message: '版本冲突', details: { currentVersion: 'v2' } }, traceId: 'trace-1' }, 409));
    vi.stubGlobal('fetch', fetcher);
    await expect(api.updateFact('e1', 'region', { value: '440300', unit: 'code', period: { startDate: '2026-01-01', endDate: '2026-12-31' }, source: 'user_input' }, '"v1"')).rejects.toMatchObject({ status: 409, code: 'VERSION_CONFLICT', traceId: 'trace-1' } as ApiRequestError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('reads Markdown success as text and JSON failure as an error', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('# 报告', { status: 200, headers: { 'Content-Type': 'text/markdown; charset=utf-8' } })).mockResolvedValueOnce(response({ error: { code: 'NOT_FOUND', message: '不存在', details: {} }, traceId: 'trace-2' }, 404));
    vi.stubGlobal('fetch', fetcher);
    expect((await api.downloadReport('run1')).data).toBe('# 报告');
    await expect(api.downloadReport('run2')).rejects.toMatchObject({ code: 'NOT_FOUND', traceId: 'trace-2' });
  });

  it('exposes an unknown judgement enum as a contract error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'run1', policyVersionMap: {}, assessments: [{ result: 'maybe', validity: 'current' }] })));
    await expect(api.getRun('run1')).rejects.toThrow('未知判断状态');
  });

  it('rejects unknown criterion states instead of rendering an unlabelled result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'run1', policyVersionMap: {}, assessments: [{ policyId: 'p', result: 'satisfied', validity: 'current', missingFacts: [], staleReasons: [], warnings: [], criteriaResults: [{ result: 'maybe', factRefs: [], evidenceRefs: [] }] }] })));
    await expect(api.getRun('run1')).rejects.toThrow('条件结果无效');
  });

  it('rejects missing policy criteria and unsafe evidence links as contract errors', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ id: 'p', title: '政策', version: 'v1', status: 'published' }))
      .mockResolvedValueOnce(response({ id: 'p', title: '政策', version: 'v1', status: 'published', criteria: [{ id: 'c', title: '条件', sourceSpans: [{ quote: '原文', sourceUrl: 'javascript:alert(1)', sourceVersion: 'v1', textHash: 'x' }] }] }));
    vi.stubGlobal('fetch', fetcher);
    await expect(api.getPolicy('p')).rejects.toThrow('政策详情字段缺失');
    await expect(api.getPolicy('p')).rejects.toThrow('政策条件或引用无效');
  });

  it('rejects unknown policy status rather than treating it as open', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ items: [{ id: 'p', title: '政策', version: 'v1', type: 'tax', status: 'unknown' }], nextCursor: null })));
    await expect(api.listPolicies()).rejects.toThrow('政策列表内容无效');
  });
});

  it('八类规则及嵌套条件读取后按原结构提交，保留 false 与精确数值', async () => {
    const leaf = { op: 'manual_review', reason: '人工确认' };
    const expressions = [
      { op: 'and', children: [leaf, leaf] }, { op: 'or', children: [leaf, leaf] }, { op: 'not', child: leaf },
      { op: 'compare', factKey: 'independentLegalEntity', period: 'assessment', comparator: 'eq', value: false, unit: 'boolean' },
      { op: 'date', factKey: 'establishedDate', period: 'assessment', comparator: 'lte', value: '2020-01-01' },
      { op: 'industry_exclusion', factKey: 'industry', period: 'assessment', excluded: ['F51'] },
      { op: 'quarterly_average', metric: 'assets', period: 'assessment', comparator: 'lte', value: '50000000.00', unit: 'CNY' }, leaf,
    ];
    const criteria = expressions.map((expression, i) => ({ id: `c${i}`, title: `条件${i}`, expression, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }));
    const draft = { id: 'd', version: 'v1', title: '草稿', revision: 'r', status: 'pending_review', sources: [], criteria, validationFindings: [] };
    const fetcher = vi.fn().mockResolvedValueOnce(response(draft, 200, { ETag: '"r"' })).mockResolvedValueOnce(response({ draft, criteria, validationFindings: [] }, 200, { ETag: '"r2"' }));
    vi.stubGlobal('fetch', fetcher);
    const loaded = await api.getAdminPolicy('d', 'v1');
    const editable = loaded.data.criteria.map(({ validationStatus: _status, ...item }) => item);
    await api.saveCriteria('d', { version: 'v1', criteria: editable }, loaded.etag!);
    const sent = JSON.parse(fetcher.mock.calls[1][1].body as string);
    expect(sent.criteria.map((item: { expression: unknown }) => item.expression)).toEqual(expressions);
  });
