async (page) => {
  const base = 'http://127.0.0.1:8080';
  const root = '/api/v1/projects/10/episodes/20/assembly';
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  const media = `${base}/.runtime/timeline-fixture.mp4`;
  let failNext = false;
  const originals = [1, 2, 3].map(id => ({ id: String(id), shot_id: String(10 + id), media_id: String(100 + id), position: id, shot_position: id,
    included: true, muted: id === 3, trim_in_ms: 0, trim_out_ms: null, duration_ms: 3000,
    script: ['开场：清晨的街道，人物走进画面。', '转折：人物停下，回头望向镜头。', '收尾：镜头慢慢拉远。'][id - 1],
    poster: `${base}/.runtime/timeline-poster.jpg`, filmstrip: {url:`${base}/.runtime/timeline-filmstrip.jpg`,count:3,interval_ms:1000}, url: media, is_stale: false, issue: null, archived: false }));
  const retained = new Map(originals.map(c => [c.id, c]));
  let state = { assembly: { id: '100', row_version: '1', aspect: '16:9', resolution: '720p', current_media_id: null },
    source_hash: 'a'.repeat(64), context_hash: 'b'.repeat(64), clips: originals, sources: originals, jobs: [], changes: [] };
  await page.route('**/api/v1/projects/10/episodes/20/assembly**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method(), body = req.postDataJSON();
    requests.push({ path, method, body });
    const reply = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === root && method === 'GET') return reply(state);
    if (path === `${root}/initialize` && method === 'POST') return reply(state);
    if (path === root && method === 'PATCH') {
      if (failNext || body.row_version !== state.assembly.row_version) { failNext = false; return reply({error:{code:'assembly_version_conflict'}},409); }
      state.clips = body.clips.map((edit, i) => ({ ...retained.get(edit.id) ?? retained.get(edit.source_clip_id), ...edit, position: i + 1 }));
      state.clips.forEach(c => retained.set(c.id, c));
      state.assembly = { ...state.assembly, resolution: body.resolution, row_version: String(+state.assembly.row_version + 1) };
      state.context_hash = String(+state.assembly.row_version % 10).repeat(64);
      state.jobs = state.jobs.map(j => ({ ...j, is_stale: j.context_hash !== state.context_hash }));
      return reply(state);
    }
    if (path === `${root}/exports` && method === 'GET') return reply({items:state.jobs.filter(j=>j.kind==='export'),has_more:false});
    if ((path === `${root}/exports` || path === `${root}/previews`) && method === 'POST') {
      if (body.row_version !== state.assembly.row_version) throw new Error('Export skipped save barrier');
      const job = { id: String(900 + state.jobs.length), kind: path.endsWith('/previews') ? 'preview' : 'export', status:'succeeded', stage:'complete', progress:100,
        cancel_requested:false,error:null,created_at:'2026-09-29T00:00:00',finished_at:'2026-09-29T00:00:01',context_hash:state.context_hash,
        media_id:String(800+state.jobs.length),url:media,duration_ms:3000,is_stale:false,
        timeline:state.clips.map(c=>({clip_id:c.id,shot_id:c.shot_id,trim_in_ms:c.trim_in_ms,trim_out_ms:c.trim_out_ms??c.duration_ms,muted:c.muted})) };
      state.jobs = [job,...state.jobs]; return reply(job,202);
    }
    const job = state.jobs.find(j => path === `${root}/exports/${j.id}`);
    if (job) return reply(job);
    return reply({error:{code:'unexpected_request'}},501);
  });
  await page.setViewportSize({width:1440,height:1080});
  await page.goto(`${base}/.runtime/timeline-editor.html`);
  await page.getByRole('heading',{name:'成片合成与导出',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.assembly-screen video.is-active')?.readyState>=2);
  console.log(JSON.stringify({ready:true,errors}));
}
