import { expect, test } from '@playwright/test';

const id = '11111111-1111-4111-8111-111111111111';
const pid = '22222222-2222-4222-8222-222222222222';
const rid = '33333333-3333-4333-8333-333333333333';
const period = { startDate: '2026-01-01', endDate: '2026-12-31' };
const policy = { id: pid, title: '已审合成政策', type: 'tax', issuer: '测试机构', region: 'CN', version: 'v1', status: 'published', effectiveFrom: '2026-01-01', effectiveTo: null, applicationWindow: null, sourceUrl: 'https://www.gov.cn/' };
const ref = { policyId: pid, sourceId: id, sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: 1, section: '第一条', quote: '测试原文', textHash: 'hash' };
const assessment = { policyId: pid, result: 'not_satisfied', validity: 'stale', staleReasons: [{ code: 'fact_changed', message: '事实已变化', changedCriterionIds: [], changedFactRefs: [] }], criteriaResults: [{ criterionId: id, result: 'not_satisfied', reason: '行业排除', factRefs: [{ key: 'industry', period }], evidenceRefs: [ref], calculation: null }], missingFacts: [], nextQuestion: null, validUntil: null, preparation: {}, explanation: '测试解释', explanationMode: 'fixture', warnings: [] };
const run = { id: rid, enterpriseId: id, period, asOf: '2026-10-30T00:00:00Z', createdAt: '2026-10-30T00:00:00Z', policyVersionMap: { [pid]: 'v1' }, factVersion: 'abc', ruleVersion: 'v1', supersedesRunId: null, assessments: [assessment] };

test('跳到主要内容不会退出工作台', async ({ page }) => {
  await page.goto('/#enterprise');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#real-main$/);
  await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
});

test('真实模式通过 API 创建画像、分析并按快照版本读取证据', async ({ page }) => {
  let enterprise: object = {};
  let detailUrl = '';
  await page.route('**/api/v1/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    const json = (value: object, headers = {}) => route.fulfill({ json: value, headers });
    if (url.pathname.endsWith('/enterprises') && request.method() === 'POST') {
      const body = request.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((fact: object) => ({ ...fact, verified: false })) };
      return json(enterprise, { ETag: '"abc"' });
    }
    if (url.pathname.endsWith(`/enterprises/${id}`)) return json(enterprise, { ETag: '"abc"' });
    if (url.pathname.endsWith('/policies')) { expect(url.searchParams.get('region')).toBe('440300'); return json({ items: [policy], nextCursor: null }); }
    if (url.pathname.endsWith('/assessment-runs') && request.method() === 'POST') { expect(request.postDataJSON().policyIds).toEqual([pid]); return json(run); }
    if (url.pathname.endsWith(`/assessment-runs/${rid}`)) return json(run);
    if (url.pathname.endsWith(`/policies/${pid}`)) { detailUrl = request.url(); return json({ ...policy, criteria: [{ id, title: '行业条件', sourceSpans: [ref] }] }); }
    return route.abort();
  });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByText('已保存合成企业：合成企业 A')).toBeVisible();
  await page.getByRole('button', { name: '机会列表' }).click();
  await expect(page.getByText('已审合成政策')).toBeVisible();
  await page.getByRole('checkbox', { name: '选择分析' }).check();
  await page.getByRole('button', { name: '分析所选政策' }).click();
  await expect(page.getByText('不满足条件')).toBeVisible();
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 850 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const box = await page.getByRole('checkbox', { name: '选择分析' }).boundingBox();
    expect(box?.width).toBeLessThanOrEqual(24);
    await page.screenshot({ path: `test-results/real-opportunities-${width}.png`, fullPage: true });
  }
  await page.getByRole('button', { name: '查看详情' }).click();
  await expect(page.getByText('测试原文')).toBeVisible();
  await expect(page.getByText('事实已变化')).toBeVisible();
  expect(new URL(detailUrl).searchParams.get('version')).toBe('v1');
});

test('真实模式保留输入并显示接口错误，不切换成固定结果', async ({ page }) => {
  await page.route('**/api/v1/enterprises', route => route.fulfill({ status: 503, json: { error: { code: 'DEPENDENCY_UNAVAILABLE', message: '服务暂不可用', details: {} }, traceId: 'trace-test' } }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('alert')).toContainText('服务暂不可用');
  await expect(page.getByRole('alert')).toContainText('trace-test');
  await expect(page.getByLabel('示例企业名称')).toHaveValue('合成企业 A');
  await expect(page.getByText('静态合成演示 · 未进行真实资格判断')).toHaveCount(0);
});

test('新增布尔事实以 JSON 布尔值保存，比例和负增量可填写', async ({ page }) => {
  let posted: { facts: Array<{ key: string; value: unknown }> } | undefined;
  await page.route('**/api/v1/enterprises', async route => { posted = route.request().postDataJSON(); await route.fulfill({ status: 503, json: { error: { message: '测试终止' } } }); });
  await page.goto('/#enterprise');
  for (const [key, value] of [['creditRecord', 'true'], ['rdAddDeductionClaimed', 'false'], ['rdExpenseIncrease', '-125.50'], ['rdExpenseRevenueRatio', '0.03']]) {
    await page.getByRole('button', { name: '添加一项信息' }).click();
    const fact = page.locator('fieldset').last();
    await fact.getByLabel('信息类型').selectOption(key);
    await fact.getByRole('checkbox', { name: '未知' }).uncheck();
    if (key === 'creditRecord' || key === 'rdAddDeductionClaimed') await fact.getByLabel('值').selectOption(value);
    else await fact.getByLabel('值').fill(value);
  }
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('alert')).toContainText('测试终止');
  expect(posted?.facts.find(f => f.key === 'creditRecord')?.value).toBe(true);
  expect(posted?.facts.find(f => f.key === 'rdAddDeductionClaimed')?.value).toBe(false);
  expect(posted?.facts.find(f => f.key === 'rdExpenseIncrease')?.value).toBe('-125.50');
  expect(posted?.facts.find(f => f.key === 'rdExpenseRevenueRatio')?.value).toBe('0.03');
});

test('真实模式移动端可读且错误摘要可定位', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/#enterprise');
  await page.getByLabel('示例企业名称').fill('');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('alert')).toBeFocused();
  await page.getByRole('link', { name: '请填写示例企业名称。' }).click();
  await expect(page.getByLabel('示例企业名称')).toBeFocused();
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 812 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/real-enterprise-${width}.png`, fullPage: true });
  }
});

for (const [state, label] of [['satisfied', '条件初步满足'], ['insufficient_evidence', '还缺资料'], ['conflict', '条款待人工核对']] as const) {
  test(`真实模式展示 ${state} 和独立失效状态`, async ({ page }) => {
    const evaluated = { ...assessment, result: state, criteriaResults: [{ ...assessment.criteriaResults[0], result: state, evidenceRefs: state === 'conflict' ? [ref, { ...ref, quote: '另一条冲突原文' }] : [ref], calculation: { formula: '(期初 + 期末) / 2', inputs: [{ factRef: { key: 'employeeCountStart', period }, value: '2' }], intermediateValues: ['2.5'], value: '2.5', unit: 'person' } }] };
    const snapshot = { ...run, assessments: [evaluated] };
    let enterprise: object = {};
    await page.route('**/api/v1/**', async route => {
      const req = route.request(); const url = new URL(req.url());
      if (url.pathname.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) }; return route.fulfill({ json: enterprise }); }
      if (url.pathname.endsWith(`/enterprises/${id}`)) return route.fulfill({ json: enterprise });
      if (url.pathname.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
      if (url.pathname.endsWith('/assessment-runs') || url.pathname.endsWith(`/assessment-runs/${rid}`)) return route.fulfill({ json: snapshot });
      if (url.pathname.endsWith(`/policies/${pid}`)) return route.fulfill({ json: { ...policy, status: 'closed', criteria: [{ id, title: '合成条件', sourceSpans: [ref] }] } });
      return route.abort();
    });
    await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click();
    await page.getByRole('button', { name: '机会列表' }).click();
    await page.getByRole('checkbox', { name: '选择分析' }).check();
    await page.getByRole('button', { name: '分析所选政策' }).click();
    await expect(page.getByText(label).first()).toBeVisible();
    await page.getByRole('button', { name: '查看详情' }).click();
    await expect(page.getByText('依据已失效，待复核').first()).toBeVisible();
    await expect(page.getByText('申报窗口已关闭').first()).toBeVisible();
    await expect(page.getByRole('region', { name: '服务端计算过程' })).toContainText('期初人数');
    await expect(page.getByRole('region', { name: '服务端计算过程' })).toContainText('2.5');
    await expect(page.getByRole('button', { name: /立即申报/ })).toHaveCount(0);
    if (state === 'conflict') await expect(page.getByText('另一条冲突原文')).toBeVisible();
    for (const width of [375, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (state === 'conflict') await page.screenshot({ path: `test-results/real-conflict-${width}.png`, fullPage: true });
    }
  });
}

test('未保存画像切换页面后保留，提交期间禁止重复操作', async ({ page }) => {
  let requests = 0;
  await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
  await page.route('**/api/v1/enterprises', async route => {
    requests++;
    await new Promise(resolve => setTimeout(resolve, 1000));
    await route.fulfill({ status: 422, json: { error: { code: 'VALIDATION_ERROR', message: '请检查字段' }, traceId: 'trace-422' } });
  });
  await page.goto('/#enterprise');
  await page.getByLabel('示例企业名称').fill('尚未保存的样本');
  await page.getByRole('button', { name: '机会列表' }).click();
  await page.getByRole('button', { name: '企业画像' }).click();
  await expect(page.getByLabel('示例企业名称')).toHaveValue('尚未保存的样本');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('button', { name: '机会列表' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '保存中…' })).toBeDisabled();
  await expect(page.getByRole('alert')).toContainText('trace-422');
  await expect(page.getByLabel('示例企业名称')).toHaveValue('尚未保存的样本');
  expect(requests).toBe(1);
});

test('列表 403 和版本详情 404 明确显示，不回退到样例', async ({ page }) => {
  let denied = true;
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/policies')) return denied ? route.fulfill({ status: 403, json: { error: { code: 'FORBIDDEN', message: '无访问权限' }, traceId: 'trace-403' } }) : route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (url.pathname.endsWith(`/policies/${pid}`)) return route.fulfill({ status: 404, json: { error: { code: 'POLICY_NOT_FOUND', message: '政策版本不存在' }, traceId: 'trace-404' } });
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '机会列表' }).click();
  await expect(page.getByRole('alert')).toContainText('trace-403');
  denied = false; await page.getByRole('button', { name: '刷新列表' }).click();
  await expect(page.getByText('已审合成政策')).toBeVisible();
  await page.getByRole('button', { name: '查看详情' }).click();
  await expect(page.getByRole('alert')).toContainText('trace-404');
  await expect(page.getByText('测试原文')).toHaveCount(0);
});

test('刷新运行失败不展示旧结论，错误政策版本不能充当快照', async ({ page }) => {
  let enterprise: object = {};
  let runReadFails = true;
  let wrongVersion = true;
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.pathname.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) }; return route.fulfill({ json: enterprise }); }
    if (url.pathname.endsWith(`/enterprises/${id}`)) return route.fulfill({ json: enterprise });
    if (url.pathname.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (url.pathname.endsWith('/assessment-runs')) return route.fulfill({ json: run });
    if (url.pathname.endsWith(`/assessment-runs/${rid}`)) return runReadFails ? route.fulfill({ status: 503, json: { error: { message: '运行读取失败' } } }) : route.fulfill({ json: run });
    if (url.pathname.endsWith(`/policies/${pid}`)) return route.fulfill({ json: { ...policy, version: wrongVersion ? 'v2' : 'v1', criteria: [] } });
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click();
  await page.getByRole('button', { name: '机会列表' }).click();
  await page.getByRole('checkbox', { name: '选择分析' }).check();
  await page.getByRole('button', { name: '分析所选政策' }).click();
  await expect(page.getByText('不满足条件')).toBeVisible();
  await page.getByRole('button', { name: '刷新列表' }).click();
  await expect(page.getByRole('alert')).toContainText('运行读取失败');
  await expect(page.getByText('结果暂不可用，请刷新后核对。')).toBeVisible();
  await expect(page.getByText('不满足条件')).toHaveCount(0);
  runReadFails = false; await page.getByRole('button', { name: '查看详情' }).click();
  await expect(page.getByRole('alert')).toContainText('政策版本与本次分析记录不一致');
  await expect(page.getByText('测试原文')).toHaveCount(0);
  wrongVersion = false;
  await page.getByRole('button', { name: '机会列表' }).click();
  await page.getByRole('button', { name: '查看详情' }).click();
  await expect(page.getByRole('alert')).toContainText('政策条件与本次分析记录不一致');
});

test('字段错误提示关联输入且摘要可定位至出错值', async ({ page }) => {
  await page.goto('/#enterprise');
  const income = page.locator('fieldset').nth(2);
  await income.getByRole('checkbox', { name: '未知' }).uncheck();
  await income.getByLabel('值').fill('1.234');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  const input = income.getByLabel('值');
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  await expect(input).toHaveAttribute('aria-describedby', 'fact-error-2');
  await page.getByRole('link', { name: /应纳税所得额请填写/ }).click();
  await expect(input).toBeFocused();
});

test('分页、筛选保留可见的选择计数，切换模式清空会话', async ({ page }) => {
  const second = { ...policy, id: '44444444-4444-4444-8444-444444444444', title: '另一项合成政策', type: 'grant' };
  await page.route('**/api/v1/policies**', route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('cursor')) return route.fulfill({ json: { items: [second], nextCursor: null } });
    if (url.searchParams.get('type') === 'grant') return route.fulfill({ json: { items: [second], nextCursor: null } });
    return route.fulfill({ json: { items: [policy], nextCursor: 'next-page' } });
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '机会列表' }).click();
  await page.getByRole('checkbox', { name: '选择分析' }).check();
  await page.getByRole('button', { name: '加载更多' }).click();
  await expect(page.getByText('另一项合成政策')).toBeVisible();
  await page.getByLabel('政策类型').selectOption('grant');
  await page.getByRole('button', { name: '应用筛选' }).click();
  await expect(page.getByText('已选择 1 项政策', { exact: false })).toBeVisible();
  await expect(page.getByText('已审合成政策')).toHaveCount(0);
  await page.getByRole('link', { name: '切换固定样例演示' }).click();
  await expect(page.getByText('静态合成演示 · 未进行真实资格判断')).toBeVisible();
  await page.getByRole('link', { name: '切换真实接口' }).click();
  await page.getByRole('button', { name: '机会列表' }).click();
  await expect(page.getByText('已选择 0 项政策', { exact: false })).toBeVisible();
});

test('真实模式支持 hash 入口与浏览器前进后退', async ({ page }) => {
  await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [policy], nextCursor: null } }));
  await page.goto('/#opportunities');
  await expect(page.getByText('已审合成政策')).toBeVisible();
  await page.getByRole('button', { name: '企业画像' }).click();
  await expect(page).toHaveURL(/#enterprise$/);
  await page.goBack();
  await expect(page.getByRole('heading', { name: '政策机会' })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
});

test('较早的列表响应不会覆盖更新的请求', async ({ page }) => {
  let count = 0;
  let releaseFirst!: () => void;
  let firstDone!: () => void;
  const waitFirst = new Promise<void>(resolve => { releaseFirst = resolve; });
  const finishedFirst = new Promise<void>(resolve => { firstDone = resolve; });
  await page.route('**/api/v1/policies**', async route => {
    count++;
    if (count === 1) {
      await waitFirst;
      await route.fulfill({ json: { items: [{ ...policy, title: '过期响应' }], nextCursor: null } });
      firstDone();
    } else await route.fulfill({ json: { items: [{ ...policy, title: '最新响应' }], nextCursor: null } });
  });
  await page.goto('/#opportunities');
  await expect(page.getByText('最新响应')).toBeVisible();
  releaseFirst(); await finishedFirst;
  await expect(page.getByText('过期响应')).toHaveCount(0);
  await expect(page.getByText('最新响应')).toBeVisible();
});

test('分析网络中断不自动重试，401 显示错误且不生成结论', async ({ page }) => {
  let attempts = 0;
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.pathname.endsWith('/enterprises')) { const body = req.postDataJSON(); return route.fulfill({ json: { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) } }); }
    if (url.pathname.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (url.pathname.endsWith('/assessment-runs')) {
      attempts++;
      if (attempts === 1) return route.abort('failed');
      return route.fulfill({ status: 401, json: { error: { code: 'UNAUTHORIZED', message: '需要授权' }, traceId: 'trace-401' } });
    }
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click();
  await page.getByRole('button', { name: '机会列表' }).click();
  await page.getByRole('checkbox', { name: '选择分析' }).check();
  await page.getByRole('button', { name: '分析所选政策' }).click();
  await expect(page.getByRole('alert')).toContainText('无法连接服务');
  expect(attempts).toBe(1);
  await expect(page.getByText('尚未分析')).toBeVisible();
  await page.getByRole('button', { name: '分析所选政策' }).click();
  await expect(page.getByRole('alert')).toContainText('trace-401');
  expect(attempts).toBe(2);
  await expect(page.getByText('尚未分析')).toBeVisible();
});

test('人工核查条件保持证据不足并展示后端理由', async ({ page }) => {
  let enterprise: object = {};
  const result = { ...assessment, result: 'insufficient_evidence', validity: 'current', staleReasons: [], criteriaResults: [{ ...assessment.criteriaResults[0], result: 'insufficient_evidence', reason: '需人工核查法人资格', factRefs: [] }] };
  const snapshot = { ...run, assessments: [result] };
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.pathname.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) }; return route.fulfill({ json: enterprise }); }
    if (url.pathname.endsWith(`/enterprises/${id}`)) return route.fulfill({ json: enterprise });
    if (url.pathname.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (url.pathname.endsWith('/assessment-runs') || url.pathname.endsWith(`/assessment-runs/${rid}`)) return route.fulfill({ json: snapshot });
    if (url.pathname.endsWith(`/policies/${pid}`)) return route.fulfill({ json: { ...policy, criteria: [{ id, title: '人工核查条件', expression: { op: 'manual_review', reason: '需人工核查法人资格' }, sourceSpans: [ref] }] } });
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click();
  await page.getByRole('button', { name: '机会列表' }).click();
  await page.getByRole('checkbox', { name: '选择分析' }).check();
  await page.getByRole('button', { name: '分析所选政策' }).click();
  await page.getByRole('button', { name: '查看详情' }).click();
  await expect(page.getByText('需人工核查法人资格')).toBeVisible();
  await expect(page.getByText('还缺资料').first()).toBeVisible();
  await expect(page.getByText('条件初步满足')).toHaveCount(0);
});

test('补问写入事实并重算，跳过不修改事实，报告按版本下载', async ({ page }) => {
  const question = { factRef: { key: 'taxableIncome', period }, prompt: '请填写应纳税所得额', reason: '影响一项政策', rank: 1 };
  const prep = { type: 'tax', handlingMode: 'tax_self_assessment', officialEntryUrl: 'https://www.gov.cn/', deadline: null, items: [
    ...(['facts_filled', 'facts_missing', 'manual_check', 'policy_closed'] as const).map((status, i) => ({ materialId: `M-${i}`, title: `材料${i}`, description: '核对材料', requiredFactRefs: [question.factRef], missing: i === 1 || i === 2, status })),
    { title: '留存凭证', description: '旧版材料', requiredFactRefs: [question.factRef], missing: false },
  ] };
  const first = { ...run, assessments: [{ ...assessment, result: 'insufficient_evidence', nextQuestion: question, preparation: prep }] };
  const secondId = '44444444-4444-4444-8444-444444444444';
  const second = { ...first, id: secondId, supersedesRunId: rid, assessments: [{ ...first.assessments[0], nextQuestion: { ...question, factRef: { key: 'creditRecord', period }, prompt: '诚信记录是否良好' } }] };
  const thirdId = '55555555-5555-4555-8555-555555555555';
  const third = { ...second, id: thirdId, supersedesRunId: secondId, assessments: [{ ...second.assessments[0], nextQuestion: null }] };
  let enterprise: any; let creates = 0; let patches = 0; let failReport = false; let reportSnapshot: object = third;
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.pathname.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) }; return route.fulfill({ json: enterprise, headers: { ETag: '"abc"', 'Access-Control-Expose-Headers': 'ETag' } }); }
    if (url.pathname.endsWith('/facts/taxableIncome')) { patches++; expect(req.headers()['if-match']).toBe('"abc"'); expect(req.postDataJSON()).toEqual({ value: '0', unit: 'CNY', period, source: 'user_input' }); enterprise = { ...enterprise, factVersion: 'def', facts: [...enterprise.facts, { key: 'taxableIncome', ...req.postDataJSON(), verified: false }] }; return route.fulfill({ json: enterprise, headers: { ETag: '"def"', 'Access-Control-Expose-Headers': 'ETag' } }); }
    if (url.pathname.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (url.pathname.endsWith('/assessment-runs') && req.method() === 'POST') { creates++; const body = req.postDataJSON(); if (creates === 2) expect(body.supersedesRunId).toBe(rid); if (creates === 3) { expect(body.supersedesRunId).toBe(secondId); expect(body.skippedFactRefs).toEqual([{ key: 'creditRecord', period }]); } return route.fulfill({ json: creates === 1 ? first : creates === 2 ? second : third }); }
    if (url.pathname.endsWith(`/assessment-runs/${thirdId}/report.md`)) { expect(req.headers().accept).toBe('text/markdown'); if (failReport) return route.fulfill({ status: 503, json: { error: { message: '报告暂不可用' }, traceId: 'report-trace' } }); return route.fulfill({ body: '# 测试报告', contentType: 'text/markdown' }); }
    if (url.pathname.endsWith(`/assessment-runs/${thirdId}`)) return route.fulfill({ json: reportSnapshot });
    if (url.pathname.endsWith(`/assessment-runs/${rid}`)) return route.fulfill({ json: first });
    if (url.pathname.endsWith(`/policies/${pid}`)) return route.fulfill({ json: { ...policy, criteria: [{ id, title: '行业条件', sourceSpans: [ref] }] } });
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click();
  await page.getByRole('button', { name: '机会列表' }).click(); await page.getByRole('checkbox', { name: '选择分析' }).check(); await page.getByRole('button', { name: '分析所选政策' }).click();
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 850 });
    const input = await page.getByLabel('回答（元）').boundingBox();
    const button = await page.getByRole('button', { name: '保存并重新分析' }).boundingBox();
    expect(input && button && input.y + input.height <= button.y).toBeTruthy();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width === 375 || width === 1440) await page.getByRole('region', { name: '优先补充问题' }).screenshot({ path: `test-results/question-${width}.png` });
  }
  await page.getByLabel('回答（元）').fill('1.234'); await page.getByRole('button', { name: '保存并重新分析' }).click();
  await expect(page.getByLabel('回答（元）')).toBeFocused(); await expect(page.getByLabel('回答（元）')).toHaveAttribute('aria-invalid', 'true');
  expect(patches).toBe(0);
  await page.getByLabel('回答（元）').fill('0'); await page.getByRole('button', { name: '保存并重新分析' }).click();
  await expect(page.getByText('诚信记录是否良好')).toBeVisible(); await page.getByRole('button', { name: '跳过（未知或不确定）' }).click();
  await expect(page.getByText(/当前没有需要补充的问题/)).toBeVisible(); expect(patches).toBe(1);
  await page.getByLabel('本次会话的分析历史').selectOption(rid);
  await expect(page.getByText('依据已失效，待复核')).toBeVisible();
  await expect(page.getByRole('heading', { name: '优先补充问题' })).toHaveCount(0);
  await page.getByLabel('本次会话的分析历史').selectOption(thirdId);
  await page.getByRole('button', { name: '清单与报告' }).click(); await expect(page.getByText('留存凭证')).toBeVisible();
  for (const label of ['所需事实已填写，不代表材料已提交或核验', '所需事实有缺项', '需人工核对', '政策已关闭', '材料状态未确认，请人工核对']) await expect(page.getByText(label, { exact: false })).toBeVisible();
  await expect(page.getByText('资料已提供', { exact: false })).toHaveCount(0);
  await expect(page.getByText('本次分析没有此类政策。')).toBeVisible();
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 850 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByRole('heading', { name: '准备清单与报告' })).toBeVisible();
    await page.screenshot({ path: `test-results/real-report-${width}.png`, fullPage: true });
  }
  const downloadButton = page.getByRole('button', { name: '下载报告（Markdown 格式）' });
  await downloadButton.focus(); await expect(downloadButton).toBeFocused();
  const download = page.waitForEvent('download'); await page.keyboard.press('Enter'); expect((await download).suggestedFilename()).toBe(`assessment-${thirdId}.md`);
  failReport = true;
  await page.getByRole('button', { name: '下载报告（Markdown 格式）' }).click();
  await expect(page.getByRole('alert')).toContainText('报告暂不可用');
  await expect(page.getByRole('alert')).toContainText('report-trace');
  for (const invalid of [
    { ...prep.items[0], status: 'unknown' },
    { ...prep.items[0], materialId: undefined },
    { ...prep.items[0], status: undefined },
  ]) {
    reportSnapshot = { ...third, assessments: [{ ...third.assessments[0], preparation: { ...prep, items: [invalid] } }] };
    await page.getByRole('button', { name: '清单与报告' }).click();
    await expect(page.getByRole('alert')).toContainText('准备清单无效');
  }
});

test('补问冲突保留输入并读取当前画像；重算失败只重试分析', async ({ page }) => {
  const question = { factRef: { key: 'taxableIncome', period }, prompt: '补充所得额', reason: '需要数据', rank: 1 };
  const first = { ...run, assessments: [{ ...assessment, result: 'insufficient_evidence', nextQuestion: question }] };
  const next = { ...first, id: '66666666-6666-4666-8666-666666666666', assessments: [{ ...first.assessments[0], nextQuestion: null }] };
  let enterprise: any; let patches = 0; let creates = 0; let reads = 0;
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    const headers = { ETag: '"version"', 'Access-Control-Expose-Headers': 'ETag' };
    if (path.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) }; return route.fulfill({ json: enterprise, headers }); }
    if (path.endsWith(`/enterprises/${id}`)) { reads++; return route.fulfill({ json: enterprise, headers }); }
    if (path.endsWith('/facts/taxableIncome')) { patches++; if (patches === 1) return route.fulfill({ status: 409, json: { error: { message: '画像已更改' }, traceId: 'conflict-id' } }); return route.fulfill({ json: enterprise, headers }); }
    if (path.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (path.endsWith('/assessment-runs') && req.method() === 'POST') { creates++; if (creates === 2) return route.fulfill({ status: 503, json: { error: { message: '分析暂不可用' } } }); return route.fulfill({ json: creates === 1 ? first : next }); }
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click(); await page.getByRole('button', { name: '机会列表' }).click(); await page.getByRole('checkbox', { name: '选择分析' }).check(); await page.getByRole('button', { name: '分析所选政策' }).click();
  const answer = page.getByLabel('回答（元）'); await answer.fill('200'); await page.getByRole('button', { name: '保存并重新分析' }).click();
  await expect(page.getByRole('alert')).toContainText('画像已更改'); await expect(answer).toHaveValue('200'); expect(reads).toBe(1); expect(patches).toBe(1);
  await page.getByRole('button', { name: '保存并重新分析' }).click(); await expect(page.getByRole('alert')).toContainText('事实已保存，但分析未完成');
  await page.getByRole('button', { name: '重试分析' }).click(); await expect(page.getByText(/当前没有需要补充的问题/)).toBeVisible(); expect(patches).toBe(2); expect(creates).toBe(3);
});

test('补问写入网络中断后先读取画像且不自动重发', async ({ page }) => {
  const question = { factRef: { key: 'taxableIncome', period }, prompt: '补充所得额', reason: '需要数据', rank: 1 };
  const snapshot = { ...run, assessments: [{ ...assessment, result: 'insufficient_evidence', nextQuestion: question }] };
  let enterprise: any; let patches = 0; let reads = 0;
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    const headers = { ETag: '"updated"', 'Access-Control-Expose-Headers': 'ETag' };
    if (path.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); enterprise = { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) }; return route.fulfill({ json: enterprise, headers }); }
    if (path.endsWith(`/enterprises/${id}`)) { reads++; return route.fulfill({ json: enterprise, headers }); }
    if (path.endsWith('/facts/taxableIncome')) { patches++; enterprise = { ...enterprise, facts: [...enterprise.facts.filter((fact: { key: string }) => fact.key !== 'taxableIncome'), { key: 'taxableIncome', value: '200', unit: 'CNY', period, source: 'user_input', verified: false }] }; return route.abort('failed'); }
    if (path.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (path.endsWith('/assessment-runs')) return route.fulfill({ json: snapshot });
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click(); await page.getByRole('button', { name: '机会列表' }).click(); await page.getByRole('checkbox', { name: '选择分析' }).check(); await page.getByRole('button', { name: '分析所选政策' }).click();
  await page.getByLabel('回答（元）').fill('200'); await page.getByRole('button', { name: '保存并重新分析' }).click();
  await expect(page.getByRole('alert')).toContainText('请重新读取画像并核对后重试');
  await expect(page.getByText('当前填写的内容：200')).toBeVisible();
  await expect(page.getByLabel('回答（元）')).toHaveValue('200');
  expect(reads).toBe(1); expect(patches).toBe(1);
});

test('历史切换保留各运行跳过记录，报告加载失败提示不可用', async ({ page }) => {
  const otherId = '77777777-7777-4777-8777-777777777777';
  const ref1 = { key: 'taxableIncome', period }; const ref2 = { key: 'creditRecord', period };
  const q = (factRef: object, prompt: string) => ({ factRef, prompt, reason: '缺少事实', rank: 1 });
  const first = { ...run, assessments: [{ ...assessment, nextQuestion: q(ref1, '第一问') }] };
  const second = { ...first, id: otherId, assessments: [{ ...first.assessments[0], nextQuestion: q(ref2, '第二问') }] };
  let posts = 0; let failRun = false; let lastSkipped: unknown;
  await page.route('**/api/v1/**', route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (path.endsWith('/enterprises') && req.method() === 'POST') { const body = req.postDataJSON(); return route.fulfill({ json: { ...body, id, factVersion: 'abc', facts: body.facts.map((f: object) => ({ ...f, verified: false })) } }); }
    if (path.endsWith('/policies')) return route.fulfill({ json: { items: [policy], nextCursor: null } });
    if (path.endsWith('/assessment-runs') && req.method() === 'POST') { posts++; if (posts === 3) { lastSkipped = req.postDataJSON().skippedFactRefs; return route.fulfill({ status: 503, json: { error: { message: '测试终止' } } }); } return route.fulfill({ json: posts === 1 ? first : second }); }
    if (path.endsWith(`/assessment-runs/${rid}`)) return route.fulfill({ json: first });
    if (path.endsWith(`/assessment-runs/${otherId}`)) return failRun ? route.fulfill({ status: 503, json: { error: { message: '加载失败' } } }) : route.fulfill({ json: second });
    return route.abort();
  });
  await page.goto('/#enterprise'); await page.getByRole('button', { name: '保存当前样本' }).click(); await page.getByRole('button', { name: '机会列表' }).click(); await page.getByRole('checkbox', { name: '选择分析' }).check(); await page.getByRole('button', { name: '分析所选政策' }).click();
  await page.getByRole('button', { name: '跳过（未知或不确定）' }).click(); await expect(page.getByText('第二问')).toBeVisible();
  await page.getByLabel('本次会话的分析历史').selectOption(rid); await page.getByLabel('本次会话的分析历史').selectOption(otherId);
  await page.getByRole('button', { name: '跳过（未知或不确定）' }).click(); await expect(page.getByRole('alert')).toContainText('测试终止'); expect(lastSkipped).toEqual([ref1, ref2]);
  failRun = true; await page.getByRole('button', { name: '清单与报告' }).click();
  await expect(page.getByRole('alert')).toContainText('加载失败'); await expect(page.getByText('报告暂不可用。', { exact: true })).toBeVisible();
  await expect(page.getByText('请先完成分析，再打开报告。')).toHaveCount(0);
});
