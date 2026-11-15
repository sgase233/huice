import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setCsrfToken } from '../api/client';
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });
describe('E3 interface', () => {
  it('preserves zero and missing metrics and sends the requested dates', async () => {
    const availability = { state: 'precise' }; const missing = { state: 'not_collected' };
    const fetcher = vi.fn().mockResolvedValue(response({ timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15T00:00:00Z', days: [{ date: '2026-11-14', dayOpen: false, total: 0, succeeded: 0, failed: null, unfinished: null, durationSampleCount: 0, avgDurationMs: null, availability: { total: availability, succeeded: availability, failed: missing, unfinished: missing, durationSampleCount: missing, avgDurationMs: missing } }] }));
    vi.stubGlobal('fetch', fetcher); const result = await api.assessmentTrends('2026-11-14','2026-11-14');
    expect(result.data.days[0].total).toBe(0); expect(result.data.days[0].failed).toBeNull(); expect(fetcher.mock.calls[0][0]).toContain('from=2026-11-14&to=2026-11-14');
  });
  it('rejects missing availability and mismatched completeness identity', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ timeZone: 'Asia/Shanghai', generatedAt: 'now', days: [{ date: '2026-11-14', dayOpen: false, availability: {} }] })).mockResolvedValueOnce(response({ enterpriseId: 'other', generatedAt: 'now', units: [], groups: [], manualReview: [], totals: { filledCount: 0, applicableCount: 0, applicabilityUnknownCount: 0, ratio: null } }));
    vi.stubGlobal('fetch', fetcher); await expect(api.reviewWorkload('2026-11-14','2026-11-14')).rejects.toThrow('统计数据无效'); await expect(api.completeness('requested')).rejects.toThrow('完整度企业不匹配');
  });
});
