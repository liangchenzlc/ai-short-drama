async (page) => {
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  const root='http://127.0.0.1:8080/api/v1/projects/10/episodes/20/assembly';
  const initial=await page.evaluate(async root=>(await fetch(root)).json(),root);
  // Reproduce a track ordered 1, 36, 2: the media bin must remain 1, 2, 36.
  const clips=initial.clips.slice(0,3).map((clip,i)=>({...clip,shot_position:[1,36,2][i]}));
  const fixture={...initial,clips,sources:clips};
  const route=route=>route.request().method()==='GET'?route.fulfill({contentType:'application/json',body:JSON.stringify(fixture)}):route.fallback();
  await page.route(root,route);
  await page.setViewportSize({width:1440,height:1080});
  await page.reload();
  await page.getByRole('button',{name:'添加镜头 36',exact:true}).waitFor();
  const labels=await page.locator('.assembly-source-meta strong').allTextContents();
  assert(JSON.stringify(labels)===JSON.stringify(['镜头 01','镜头 02','镜头 36']),'Media bin did not sort numerically');
  assert((await page.locator('.assembly-track-clip').nth(1).getAttribute('aria-label')).includes('镜头 36'),'Track order changed');
  const second=page.locator('.assembly-track-clip').nth(1);
  await second.click({position:{x:45,y:18}});
  await page.waitForFunction(()=>document.querySelectorAll('.assembly-track-clip.is-selected').length===1&&document.querySelectorAll('.assembly-track-clip')[1].getAttribute('aria-pressed')==='true');
  assert(await second.locator('.assembly-track-selected').isVisible(),'Selection text missing');
  assert(await page.locator('.assembly-track-selected').count()===1,'Previous selection label remained');
  const indicator=await second.evaluate(el=>{
    const css=getComputedStyle(el,'::after'); return {border:css.borderTopWidth,z:css.zIndex,pointer:css.pointerEvents};
  });
  assert(indicator.border==='3px'&&indicator.z==='2'&&indicator.pointer==='none','Selection border does not overlay thumbnails safely');
  await page.waitForFunction(()=>document.querySelector('video.is-active')?.readyState>=2);
  await page.locator('.assembly-timeline').scrollIntoViewIfNeeded();
  await page.screenshot({path:'.impeccable/review/timeline-selection-desktop-viewport.png'});
  await page.screenshot({path:'.impeccable/review/timeline-selection-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'适应全部',exact:true}).click();
  assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),'Mobile overflow');
  await page.screenshot({path:'.impeccable/review/timeline-selection-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1080});
  await page.reload();
  await page.getByRole('button',{name:'添加镜头 36',exact:true}).waitFor();
  assert(JSON.stringify(await page.locator('.assembly-source-meta strong').allTextContents())===JSON.stringify(labels),'Refresh lost source order');
  await page.unroute(root,route);
  return {passed:true,sourceOrder:[1,2,36],trackOrder:[1,36,2],selectionOverlay:indicator};
}
