import { expect, test } from '@playwright/test';

test('five-page synthetic flow keeps results separate from profile edits', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => requests.push(request.url()));
  await page.goto('/?mode=demo');
  await expect(page.locator('nav[aria-label="主导航"] .workflow-icon')).toHaveCount(5);
  await expect(page.getByLabel('单位或格式').first()).toHaveValue('代码');
  await expect(page.getByLabel('单位或格式').nth(2)).toHaveValue('元');
  await expect(page.getByText('静态合成演示 · 未进行真实资格判断')).toBeVisible();
  const income = page.locator('fieldset').nth(2);
  await income.getByRole('checkbox', { name: '未知' }).uncheck();
  await income.getByRole('textbox', { name: '值' }).fill('0');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByText('已保存当前合成样本。机会列表已恢复为“尚未分析”。')).toBeVisible();
  await page.getByRole('link', { name: /机会列表/ }).click();
  await expect(page.getByText('尚未分析').first()).toBeVisible();
  await page.getByRole('button', { name: '查看固定示例详情' }).last().click();
  await expect(page.getByText('条款待人工核对').first()).toBeVisible();
  await expect(page.getByText('依据已失效，待复核').first()).toBeVisible();
  await expect(page.getByText('申报窗口已关闭').first()).toBeVisible();
  await expect(page.locator('.evidence-row')).toHaveCount(2);
  await expect(page.getByRole('button', { name: /申报/ })).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: 'test-results/detail-1440.png', fullPage: true });
  await page.getByRole('link', { name: '查看清单与报告示例' }).click();
  await expect(page.getByRole('heading', { name: '准备材料示例' })).toHaveCount(2);
  await expect(page.getByText('税收 · 自行判断')).toBeVisible();
  await expect(page.getByText('资金 · 项目申报')).toBeVisible();
  await page.screenshot({ path: 'test-results/report-1440.png', fullPage: true });
  await page.getByRole('link', { name: /审核预览/ }).click();
  await expect(page.getByRole('button', { name: '审核发布（后续阶段）' })).toBeDisabled();
  await page.getByRole('link', { name: /企业画像/ }).click();
  await income.getByRole('textbox', { name: '值' }).fill('1.234');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('alert')).toContainText('最多两位小数');
  await income.getByRole('textbox', { name: '值' }).fill('0');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await page.getByRole('link', { name: /机会列表/ }).click();
  await expect(page.getByText('尚未分析')).toHaveCount(4);
  expect(requests.filter(url => url.includes('/api/v1/'))).toEqual([]);
});

test('all four static states remain labelled and filters change the candidate set', async ({ page }) => {
  await page.goto('/?mode=demo#opportunities');
  await page.getByLabel('政策类型').selectOption('tax');
  await expect(page.getByText('2 项合成候选')).toBeVisible();
  await page.getByLabel('政策类型').selectOption('all');
  const expected = ['还缺资料', '条件初步满足', '不满足条件', '条款待人工核对'];
  for (let index = 0; index < expected.length; index++) {
    await page.getByRole('button', { name: '查看固定示例详情' }).nth(index).click();
    await expect(page.getByText(expected[index]).first()).toBeVisible();
    await page.getByRole('link', { name: /机会列表/ }).click();
  }
});

test('mobile layout has no horizontal overflow and keyboard navigation works', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/?mode=demo');
  await page.screenshot({ path: 'test-results/enterprise-375.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.getByRole('link', { name: /机会列表/ }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/opportunities-375.png', fullPage: true });
  await page.setViewportSize({ width: 768, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/opportunities-768.png', fullPage: true });
});

test('工作流程切换时播放过渡，减少动态效果时不播放', async ({ page }) => {
  for (const mode of ['/?mode=demo#enterprise', '/#enterprise']) {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
    await page.goto(mode);
    await page.evaluate(() => {
      const original = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        if (this.classList.contains('main-content')) {
          document.body.dataset.workflowAnimations = String(Number(document.body.dataset.workflowAnimations || 0) + 1);
          document.body.dataset.workflowDuration = String(typeof args[1] === 'number' ? args[1] : args[1]?.duration);
        }
        return original.apply(this, args);
      };
    });
    const navigation = page.getByRole('navigation', { name: '主导航' });
    await navigation.getByText('机会列表').click();
    await expect(page.locator('body')).toHaveAttribute('data-workflow-animations', '1');
    await expect(page.locator('body')).toHaveAttribute('data-workflow-duration', '960');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await navigation.getByText('企业画像').click();
    await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
    await expect(page.locator('body')).toHaveAttribute('data-workflow-animations', '1');
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('流程选中背景随导航移动并与当前项对齐', async ({ page }) => {
  for (const width of [375, 1440]) {
    await page.setViewportSize({ width, height: 850 });
    for (const mode of ['/?mode=demo#enterprise', '/#enterprise']) {
      await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
      await page.goto(mode);
      const nav = page.getByRole('navigation', { name: '主导航' });
      const indicator = nav.locator('.workflow-indicator');
      const before = await indicator.evaluate(element => (element as HTMLElement).style.transform);
      await nav.getByText('机会列表').click();
      await expect.poll(() => indicator.evaluate(element => (element as HTMLElement).style.transform)).not.toBe(before);
      await expect.poll(() => nav.evaluate(element => {
        const highlight = element.querySelector('.workflow-indicator')!.getBoundingClientRect();
        const selected = element.querySelector('[aria-current="page"]')!.getBoundingClientRect();
        return Math.max(Math.abs(highlight.left - selected.left), Math.abs(highlight.top - selected.top), Math.abs(highlight.width - selected.width));
      })).toBeLessThan(1);
      await page.unrouteAll({ behavior: 'wait' });
    }
  }
});

test('侧边栏收缩后保留导航、悬浮提示及键盘操作', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 850 });
  for (const mode of ['/?mode=demo#enterprise', '/#enterprise']) {
    await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
    await page.goto(mode);
    const nav = page.getByRole('navigation', { name: '主导航' });
    const before = await nav.boundingBox();
    const firstItem = nav.locator('.dock-item').first();
    const firstTop = (await firstItem.boundingBox())!.y;
    const iconCenters = await nav.locator('.workflow-icon').evaluateAll(icons => icons.map(icon => { const rect = icon.getBoundingClientRect(); return rect.left + rect.width / 2; }));
    const toggleTop = (await nav.getByRole('button', { name: '收起侧边栏' }).boundingBox())!.y;
    const captionTop = (await nav.getByText('工作流程').boundingBox())!.y;
    expect(Math.abs(captionTop - toggleTop)).toBeLessThan(48);
    await nav.getByRole('button', { name: '收起侧边栏' }).click();
    await expect.poll(async () => Math.abs((await firstItem.boundingBox())!.y - firstTop)).toBeLessThan(1);
    const toggle = nav.getByRole('button', { name: '展开侧边栏' });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect.poll(() => nav.evaluate(el => {
      const menu = el.querySelector('.dock-menu')!.getBoundingClientRect();
      const icon = el.querySelector('.workflow-icon')!.getBoundingClientRect();
      return Math.abs((menu.left + menu.width / 2) - (icon.left + icon.width / 2));
    })).toBeLessThan(1);
    await expect.poll(async () => (await nav.boundingBox())!.width).toBeLessThanOrEqual(76);
    expect((await nav.boundingBox())!.width).toBeLessThan(before!.width);
    expect(Math.abs((await toggle.boundingBox())!.y - toggleTop)).toBeLessThan(1);
    expect(Math.abs((await firstItem.boundingBox())!.y - firstTop)).toBeLessThan(1);
    const collapsedCenters = await nav.locator('.workflow-icon').evaluateAll(icons => icons.map(icon => { const rect = icon.getBoundingClientRect(); return rect.left + rect.width / 2; }));
    collapsedCenters.forEach((center, index) => expect(Math.abs(center - iconCenters[index])).toBeLessThan(1));
    const target = nav.getByRole(mode.includes('demo') ? 'link' : 'button', { name: '机会列表' });
    await target.hover();
    await expect(nav.locator('.dock-tip')).toHaveAttribute('data-visible', 'true');
    await expect(nav.locator('.dock-tip')).toHaveText('机会列表');
    await page.mouse.move(600, 100);
    await expect(nav.locator('.dock-tip')).toHaveAttribute('data-visible', 'false');
    await expect(nav.locator('.dock-tip')).toHaveText('机会列表');
    await target.hover();
    await expect.poll(() => target.evaluate(el => parseFloat(el.style.getPropertyValue('--dock-factor')))).toBe(1);
    await target.focus();
    await target.press('Enter');
    await expect(target).toHaveAttribute('aria-current', 'page');
    await toggle.click();
    await expect(nav.getByRole('button', { name: '收起侧边栏' })).toHaveAttribute('aria-expanded', 'true');
    await expect(target.locator('.dock-label')).toBeVisible();
    await expect.poll(async () => (await nav.boundingBox())!.width).toBeGreaterThanOrEqual(before!.width - 1);
    expect(Math.abs((await nav.getByRole('button', { name: '收起侧边栏' }).boundingBox())!.y - toggleTop)).toBeLessThan(1);
    expect(Math.abs((await firstItem.boundingBox())!.y - firstTop)).toBeLessThan(1);
    const expandedCenters = await nav.locator('.workflow-icon').evaluateAll(icons => icons.map(icon => { const rect = icon.getBoundingClientRect(); return rect.left + rect.width / 2; }));
    expandedCenters.forEach((center, index) => expect(Math.abs(center - iconCenters[index])).toBeLessThan(1));
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('侧栏文字平滑切换且遵循减少动态效果', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 850 });
  await page.goto('/?mode=demo#enterprise');
  const nav = page.getByRole('navigation', { name: '主导航' });
  const label = nav.locator('.dock-label').first();
  await nav.getByRole('button', { name: '收起侧边栏' }).click();
  await expect.poll(() => label.evaluate(el => getComputedStyle(el).opacity)).toBe('0');
  await expect(label).toHaveCSS('visibility', 'hidden');
  await nav.getByRole('button', { name: '展开侧边栏' }).click();
  await expect.poll(() => label.evaluate(el => getComputedStyle(el).opacity)).toBe('1');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await nav.getByRole('button', { name: '收起侧边栏' }).click();
  await expect(label).toHaveCSS('opacity', '0');
  expect(parseFloat(await label.evaluate(el => getComputedStyle(el).transitionDuration))).toBeLessThan(.001);
});
