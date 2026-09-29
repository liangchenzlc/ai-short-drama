async (page) => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  await page.setViewportSize({width:1440,height:1080});
  await page.evaluate(()=>localStorage.removeItem('short-drama:assembly-layout:v1'));
  await page.reload();
  await page.getByRole('button',{name:'专注剪辑',exact:true}).waitFor();
  const before = await page.locator('.assembly-monitor').boundingBox();
  const collapsed = await page.locator('.episode-layout').evaluate(el=>el.classList.contains('is-collapsed'));
  await page.getByRole('button',{name:'专注剪辑',exact:true}).click();
  assert(await page.locator('.episode-sidebar').isHidden(), 'Focus did not hide workflow');
  assert(await page.locator('.episode-top').isHidden(), 'Focus did not hide episode header');
  const after = await page.locator('.assembly-monitor').boundingBox();
  assert(after.width > before.width + 100, 'Focus did not widen monitor');
  await page.getByRole('button',{name:'退出专注剪辑',exact:true}).press('Escape');
  assert(await page.locator('.episode-sidebar').isVisible(), 'Escape did not restore workflow');
  assert(await page.locator('.episode-top').isVisible(), 'Escape did not restore header');
  assert(await page.locator('.episode-layout').evaluate(el=>el.classList.contains('is-collapsed'))===collapsed, 'Sidebar preference changed');
  assert(await page.getByRole('button',{name:'专注剪辑',exact:true}).evaluate(el=>el===document.activeElement), 'Focus did not return to mode button');
  return {passed:true,normalMonitorWidth:before.width,focusedMonitorWidth:after.width,sidebarRestored:true};
}
