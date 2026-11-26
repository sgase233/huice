import { expect, test } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});


for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test(`卡片滚动动画遵循动态效果设置：${reducedMotion}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion });
    await page.addInitScript(() => {
      const original = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        if (this.classList.contains('fact-card')) {
          this.setAttribute('data-reveal-count', String(Number(this.getAttribute('data-reveal-count') || 0) + 1));
          this.setAttribute('data-reveal-duration', String(typeof args[1] === 'number' ? args[1] : args[1]?.duration));
        }
        return original.apply(this, args);
      };
    });
    await page.goto('/#enterprise');
    const card = page.locator('.fact-card').last();
    await card.scrollIntoViewIfNeeded();
    if (reducedMotion === 'reduce') {
      await expect(card).not.toHaveAttribute('data-reveal-count');
    } else {
      await expect(card).toHaveAttribute('data-reveal-count', '1');
      await expect(card).toHaveAttribute('data-reveal-duration', '1200');
      await page.evaluate(() => window.scrollTo(0, 0));
      await card.scrollIntoViewIfNeeded();
      await expect(card).toHaveAttribute('data-reveal-count', '1');
    }
  });
}

test('审核条件卡片增减使用收展动画并保留其他输入', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '测试机关', revision: id, status: 'pending_review', sources: [{ id, title: '正文', sourceUrl: 'https://www.gov.cn/', text: '合成原文' }], criteria: [{ id, title: '条件一', expression: { op: 'manual_review', reason: '人工核对' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => route.fulfill({ json: route.request().url().includes('/admin/policies/') ? draft : { items: [draft], nextCursor: null }, headers: { ETag: '"revision"', 'Access-Control-Expose-Headers': 'ETag' } }));
  await page.goto('/#admin');
  await page.getByRole('button', { name: '读取草稿' }).click();
  await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  const cards = page.locator('.admin-criterion');
  await expect(cards).toHaveCount(2);
  await expect(cards.last().locator('input').first()).toBeFocused();
  await cards.last().getByLabel('标题').fill('保留的内容');
  page.once('dialog', dialog => dialog.accept());
  await cards.first().getByRole('button', { name: '删除条件', exact: true }).click();
  await expect(page.locator('.motion-card-slot.is-removing')).toHaveCount(1);
  await expect(cards).toHaveCount(1);
  await expect(cards.first().getByLabel('标题')).toHaveValue('保留的内容');
});

test('减少动态效果时审核卡片直接删除', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '测试机关', revision: id, status: 'pending_review', sources: [], criteria: [{ id, title: '条件一', expression: { op: 'manual_review', reason: '人工核对' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => route.fulfill({ json: route.request().url().includes('/admin/policies/') ? draft : { items: [draft], nextCursor: null }, headers: { ETag: '"revision"', 'Access-Control-Expose-Headers': 'ETag' } }));
  await page.goto('/#admin'); await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /合成草稿/ }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '删除条件', exact: true }).click();
  await expect(page.locator('.admin-criterion')).toHaveCount(0);
  await expect(page.locator('.motion-card-slot.is-removing')).toHaveCount(0);
});

test('删除规则子条件和引用时只收起对应卡片', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  const id = '11111111-1111-4111-8111-111111111111';
  const source = { id, title: '正文', sourceUrl: 'https://www.gov.cn/', text: '第一项与第二项' };
  const span = (quote: string) => ({ sourceId: id, sourceUrl: source.sourceUrl, sourceVersion: 'v1', page: null, section: null, quote, textHash: 'hash' });
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '测试机关', revision: id, status: 'pending_review', sources: [source], criteria: [{ id, title: '条件一', expression: { op: 'and', children: ['第一项', '第二项', '第三项'].map(reason => ({ op: 'manual_review', reason })) }, sourceSpans: [span('第一项'), span('第二项')], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => route.fulfill({ json: route.request().url().includes('/admin/policies/') ? draft : { items: [draft], nextCursor: null }, headers: { ETag: '"revision"', 'Access-Control-Expose-Headers': 'ETag' } }));
  await page.goto('/#admin'); await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /合成草稿/ }).click();
  const card = page.locator('.admin-criterion').first();
  page.once('dialog', dialog => dialog.accept());
  await card.getByRole('button', { name: '删除子条件 1', exact: true }).click();
  await expect(card.locator('.motion-card-slot.is-removing')).toHaveCount(1);
  await expect(card.getByLabel('人工核查原因')).toHaveCount(2);
  await expect(card.getByLabel('人工核查原因').first()).toHaveValue('第二项');
  await expect(card.locator('.motion-card-slot.is-removing')).toHaveCount(0);
  page.once('dialog', dialog => dialog.accept());
  await card.getByRole('button', { name: '删除引用' }).first().click();
  await expect(card.locator('.motion-card-slot.is-removing')).toHaveCount(1);
  await expect(card.getByLabel('引文（最多 2000 字）')).toHaveCount(1);
  await expect(card.getByLabel('引文（最多 2000 字）')).toHaveValue('第二项');
});
