/* 分镜制作：逐镜编辑，图片与视频各自保留输入和记录。 */
(function () {
  const W = window.WB, st = W.S.board, M = W.media, E = W.esc, I = W.icon;
  st.mode = 'image'; st.filter = 'all'; st.query = ''; st.settingsOpen = true;
  st.shots.forEach(shot => { shot.name = W.parseScenes(W.S.script.text).find(scene => scene.no === shot.scene)?.place || '镜头 ' + shot.no; M.init(shot, { shot: true, kind: '场景' }); });
  const current = () => st.shots[st.selected] || null;
  function visible() {
    return st.shots.filter(shot => (shot.name + shot.act + shot.no).includes(st.query) && (st.filter === 'all' || (st.filter === 'wait' ? st.mode === 'video' ? !shot.video : !shot.images.length : !!shot.error)));
  }
  function fields(shot) {
    return `<label class="field"><span class="field-label">镜头名称</span><input class="inp" data-media-field="name" value="${E(shot.name)}" maxlength="120"></label><label class="field"><span class="field-label">镜头脚本</span><textarea class="ta" rows="5" data-media-field="act" placeholder="描述这一镜发生的事">${E(shot.act)}</textarea></label>`;
  }
  function render() {
    const list = M.rail(visible(), { title: '当前镜头', query: st.query, filters: [['all', '全部'], ['wait', st.mode === 'video' ? '待制视频' : '待制图片'], ['bad', '失败']], selected: current()?.no, empty: st.query || st.filter !== 'all' ? '没有符合筛选的镜头' : '还没有镜头' });
    const shot = current();
    const voice = st.mode === 'video' && shot ? `<details class="voice-details"${st.voiceOpen ? ' open' : ''}><summary>对白与音色</summary><label class="field"><span class="field-label">对白</span><textarea class="ta" data-media-field="line" rows="3">${E(shot.line || '')}</textarea></label><label class="field"><span class="field-label">音色</span><select class="inp" data-media-field="voice"><option${shot.voice !== '暂不生成对白' ? ' selected' : ''}>使用角色音色</option><option${shot.voice === '暂不生成对白' ? ' selected' : ''}>暂不生成对白</option></select></label></details>` : '';
    return M.workspace({ list, title: '镜头', fields, beforePrompt: voice });
  }
  function add(act = '') {
    const number = Math.max(0, ...st.shots.map(shot => Number(shot.no.slice(1)) || 0)) + 1;
    const shot = { no: 'S' + String(number).padStart(2, '0'), name: '', act, status: 'wait', scene: '', hue: 180, dur: 5, count: 1, video: false, line: '' };
    M.init(shot, { shot: true }); st.shots.push(shot); st.selected = st.shots.length - 1; st.settingsOpen = true; st.railOpen = false; st.mobileSettings = innerWidth <= 980;
  }
  Object.assign(W.actions, {
    'board.mode': el => { const shot = current(); if (shot) shot.playing = false; st.mode = el.dataset.value; W.render(); },
    'board.prev': () => { if (current()) current().playing = false; st.selected = Math.max(0, st.selected - 1); W.render(); },
    'board.next': () => { if (current()) current().playing = false; st.selected = Math.min(st.shots.length - 1, st.selected + 1); W.render(); },
    'board.add': () => { add(); W.render(); document.querySelector('[data-media-field="name"]')?.focus(); },
    'board.extract': () => {
      const scenes = W.parseScenes(W.S.script.text);
      const unmade = scenes.filter(scene => !st.shots.some(shot => shot.scene === scene.no));
      st.extractScenes = unmade;
      W.modal({ title: '从剧本提取分镜', width: 520, body: unmade.length ? `<p class="hint">为尚未制作的场次创建镜头，已有镜头保持不变。</p><div class="extract-list">${unmade.map((scene, index) => `<label><input type="checkbox" data-shot-extract="${index}" checked><span>场 ${scene.no} · ${E(scene.place)}</span></label>`).join('')}</div>` : '<p class="hint">当前各场已有镜头。可以新增镜头继续细化，不会重复覆盖已有制作。</p>', foot: `<button class="btn" data-act="overlay.close">取消</button>${unmade.length ? '<button class="btn primary" data-act="board.extractApply">创建镜头</button>' : ''}` });
    },
    'board.extractApply': () => { [...document.querySelectorAll('[data-shot-extract]:checked')].forEach(input => { const scene = st.extractScenes[Number(input.dataset.shotExtract)]; add(scene.lines.filter(line => line.trim() && !/^人物[：:]/.test(line)).join('\n')); current().scene = scene.no; current().name = scene.place; }); st.mobileSettings = false; W.closeOverlays(); W.render(); W.save(); },
    'board.more': () => W.modal({ title: '镜头操作', width: 360, body: `<button class="btn block" data-act="board.batch">${I('layers', 'i-13')}批量生成${st.mode === 'video' ? '视频' : '图片'}</button><button class="btn block danger" data-act="board.delete">${I('trash', 'i-13')}删除当前镜头</button>`, foot: '<button class="btn" data-act="overlay.close">关闭</button>' }),
    'board.batch': () => W.modal({ title: '批量生成分镜' + (st.mode === 'video' ? '视频' : '图'), width: 540, body: `<p class="hint">选择本次生成范围。${st.mode === 'video' ? '没有主图的镜头暂不可选。' : '已有图片会保留在生成记录中。'}</p><div class="extract-list">${st.shots.map((shot, index) => `<label><input type="checkbox" data-batch-shot="${index}" ${st.mode === 'video' && !shot.images.length || shot.busy ? 'disabled' : ''} ${st.mode === 'video' ? !shot.video && shot.images.length ? 'checked' : '' : !shot.images.length ? 'checked' : ''}><span>${shot.no} · ${E(shot.name || '未命名镜头')}</span><span class="tag">${shot.busy ? '生成中' : st.mode === 'video' ? shot.video ? '已有视频' : '暂无视频' : shot.images.length ? '已有图片' : '暂无图片'}</span></label>`).join('')}</div>`, foot: '<button class="btn" data-act="overlay.close">取消</button><button class="btn primary" data-act="board.batchApply">开始生成</button>' }),
    'board.batchApply': () => { const selected = [...document.querySelectorAll('[data-batch-shot]:checked')].map(input => st.shots[Number(input.dataset.batchShot)]); if (!selected.length) return; W.closeOverlays(); selected.forEach(shot => M.generate(shot, st.mode)); W.render(); },
    'board.delete': () => W.modal({ title: '删除镜头', width: 440, body: `<p class="dialog-copy">删除「${E(current().name || current().no)}」？已有成片片段会保留，并提示来源变化。</p>`, foot: '<button class="btn" data-act="overlay.close">取消</button><button class="btn danger" data-act="board.deleteApply">删除</button>' }),
    'board.deleteApply': () => { st.shots.splice(st.selected, 1); st.selected = Math.min(st.selected, st.shots.length - 1); W.closeOverlays(); W.render(); W.save(); }
  });
  W.views.board = { render, current, select: no => { if (current()) current().playing = false; st.selected = st.shots.findIndex(shot => shot.no === no); }, mount: M.mount, leave: M.leave };
})();
