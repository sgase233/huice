import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiRequestError, setAdminToken } from '../api/client';

afterEach(() => { vi.unstubAllGlobals(); setAdminToken(null); });

const response = (body: unknown, status = 200, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'X-Trace-Id': 'trace-1', ...extra } });

describe('HTTP contract boundary', () => {
  it('encodes queries and includes credentials', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ items: [], nextCursor: null }));
    vi.stubGlobal('fetch', fetcher);
    await api.listPolicies({ type: 'tax', region: '44 0300' });
    expect(fetcher.mock.calls[0][0]).toContain('region=44+0300');
    expect(fetcher.mock.calls[0][1].credentials).toBe('include');
  });

  it('keeps If-Match, body period, and runtime-only bearer in their distinct positions', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ id: 'e1', facts: [] }, 200, { ETag: '"version-2"' }));
    vi.stubGlobal('fetch', fetcher);
    const result = await api.updateFact('e1', 'taxableIncome', { value: '0', unit: 'CNY', period: { startDate: '2026-01-01', endDate: '2026-12-31' }, source: 'user_input' }, '"version-1"');
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PATCH');
    expect((init.headers as Headers).get('If-Match')).toBe('"version-1"');
    expect(JSON.parse(init.body as string).value).toBe('0');
    expect(result.etag).toBe('"version-2"');
    setAdminToken('runtime-token');
    fetcher.mockResolvedValueOnce(response({ items: [], nextCursor: null }));
    await api.listAdminPolicies();
    expect((fetcher.mock.calls[1][1].headers as Headers).get('Authorization')).toBe('Bearer runtime-token');
  });

  it('does not set a JSON content type on multipart uploads', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ id: 'draft' }, 201));
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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ id: 'run1', assessments: [{ result: 'maybe', validity: 'current' }] })));
    await expect(api.getRun('run1')).rejects.toThrow('未知判断状态');
  });
});
