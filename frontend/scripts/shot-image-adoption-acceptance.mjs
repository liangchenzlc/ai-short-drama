async (page) => {
  // Run via playwright-cli on an open storyboard page. All writes are intercepted.
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  page.removeAllListeners('dialog');
  const requests = [];
  const blockedWrites = [];
  const errors = [];
  const candidates = new Map();
  const details = new Map();
  let mode = 'conflict';
  let adopted = null;
  let focusRefreshes = 0;
  const check = (value, message) => { if (!value) throw new Error(message); };
  page.setDefaultTimeout(15000);
  const onError = error => errors.push(String(error));
  const onDialog = async dialog => { await dialog.accept(); };
  page.on('pageerror', onError);
  page.on('dialog', onDialog);
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (request.method() !== 'GET') {
      if (request.method() === 'POST' && /\/media-library\/items\/\d+\/apply$/.test(path)) {
        const body = request.postDataJSON();
        requests.push(body);
        if (mode === 'conflict') return reply({ error: { code: 'shot_version_conflict', message: '验收：分镜版本冲突，请核对后重试。' } }, 409);
        if (!body.acknowledge_stale_source) return reply({ error: { code: 'stale_generation_source', message: '验收：旧上下文' } }, 409);
        const asset = candidates.get(path.split('/').at(-2));
        check(asset, 'candidate not captured');
        const detail = details.get(asset.generation_id);
        adopted = { id: body.target.id, media_id: asset.media_id, media_asset_id: asset.asset_id, url: asset.url,
          layout: detail.source.layout, aspect: detail.parameters.aspect, resolution: detail.parameters.resolution, is_stale: false };
        return reply({});
      }
      blockedWrites.push(path);
      return reply({ error: { code: 'acceptance_write_blocked', message: '验收禁止写入真实数据' } }, 409);
    }
    const response = await route.fetch();
    if (path.endsWith('/media-library/items')) {
      const data = await response.json();
      for (const asset of data.items) candidates.set(asset.asset_id, asset);
      return route.fulfill({ response });
    }
    if (/\/ai\/generations\/\d+$/.test(path)) {
      const data = await response.json(); details.set(data.generation_id, data);
      return route.fulfill({ response });
    }
    if (/\/shots\/\d+$/.test(path)) {
      // Reproduce the focus refresh that used to invalidate the pending adoption.
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      focusRefreshes++;
    }
    if (adopted && path.endsWith('/shots')) {
      const data = await response.json();
      data.items = data.items.map(shot => shot.id === adopted.id ? { ...shot, image: adopted } : shot);
      return reply(data);
    }
    return route.fulfill({ response });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const shot = page.locator('.storyboard-item').first();
    if (await shot.locator('.storyboard-summary').getAttribute('aria-expanded') !== 'true') {
      await shot.locator('.storyboard-summary').click();
    }
    await shot.getByRole('button', { name: '生成记录', exact: true }).click();
    const history = page.getByRole('dialog', { name: '分镜图片生成记录', exact: true });
    await history.locator('.image-candidate .asset-library-preview').first().click();
    const preview = page.getByRole('dialog', { name: '分镜图片预览', exact: true });
    await preview.getByRole('button', { name: '确认采用', exact: true }).click();
    await preview.getByText('分镜已被其他窗口修改。输入已保留，请重新加载后合并。', { exact: true }).waitFor();
    check(requests.length === 1, 'focus silently cancelled adoption or duplicated request');
    await page.screenshot({ path: '../output/playwright/adoption-visible-error.png' });
    mode = 'success';
    await preview.getByRole('button', { name: '确认采用', exact: true }).click();
    await preview.waitFor({ state: 'hidden' });
    await history.waitFor({ state: 'hidden' });
    await shot.getByText('图片已采用。', { exact: true }).waitFor();
    await shot.getByText('已采用图片', { exact: true }).waitFor();
    check(requests.length === 3 && requests[2].acknowledge_stale_source === true, 'stale confirmation did not finish');
    check(await shot.locator('.shot-current-preview img').getAttribute('src') === adopted.url, 'current image did not refresh');
    await page.screenshot({ path: '../output/playwright/adoption-success-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await shot.locator('.shot-current-preview').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '../output/playwright/adoption-success-mobile.png' });
    check(!blockedWrites.length, 'unexpected write attempted');
    check(!errors.length, 'browser errors: ' + errors.join(';'));
    return { passed: true, interceptedAdoptions: requests.length, focusRefreshes,
      checks: ['visible conflict inside preview', 'focus refresh during adoption', 'stale confirmation', 'preview closes on success', 'current image refreshes'],
      realWrites: 0 };
  } catch (error) {
    throw new Error(JSON.stringify({ error: String(error), requests: requests.length, focusRefreshes,
      alerts: await page.locator('dialog[open] .ant-alert-message').allTextContents(), errors }));
  } finally {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    page.off('pageerror', onError); page.off('dialog', onDialog);
    await page.reload();
  }
}
