/* 成片合成：六轨剪辑示意，属性按选择展开，不常驻检查器。 */
(function () {
  const W = window.WB, st = W.S.assembly, E = W.esc, I = W.icon;
  const TRACKS = [['pic', '画面', 'film'], ['orig', '原声', 'wave'], ['vo', '对白', 'mic'], ['sub', '字幕', 'type'], ['mus', '配乐', 'wave'], ['sfx', '音效', 'bell']];
  st.undo = []; st.redo = []; st.locked = {}; st.muted = {}; st.zoom = 1; st.bin = 'shot'; st.focus = false;
  st.clips.filter(c => c.track === 'pic').slice(0, 3).forEach(c => st.clips.push({ ...c, id: c.id + '-orig', track: 'orig', name: c.no + ' 原声' }));
  const total = () => Math.max(1, ...st.clips.map(c => c.start + c.dur));
  const selected = () => st.clips.find(c => c.id === st.selected);
  const at = time => st.clips.find(c => c.track === 'pic' && time >= c.start && time < c.start + c.dur);
  const snapshot = () => JSON.stringify({ clips: st.clips, unsynced: st.unsynced });
  function checkpoint(label, before = snapshot()) { st.undo.push({ label, value: before }); if (st.undo.length > 30) st.undo.shift(); st.redo = []; }
  function restore(value) { const old = JSON.parse(value); st.clips = old.clips; st.unsynced = old.unsynced; st.selected = null; st.playhead = Math.min(st.playhead, total()); W.render(); W.save(); }
  function update() { W.render(); W.save(); }

  function rail() {
    return `<aside class="panel am-rail${st.binOpen ? ' is-open' : ''}"><header class="head"><h2>媒体</h2><div class="actions"><button class="icon-btn" data-act="am.upload" title="上传媒体">${I('upload', 'i-13')}</button><button class="icon-btn mobile-only" data-act="am.binToggle" title="关闭媒体">${I('x', 'i-13')}</button></div></header>
      <div class="bin-tabs">${[['shot', '视频'], ['mus', '配乐'], ['sfx', '音效']].map(([id, title]) => `<button class="chip${st.bin === id ? ' on' : ''}" data-act="am.bin" data-id="${id}" aria-pressed="${st.bin === id}">${title}</button>`).join('')}</div>
      <div class="bin-list">${W.D.bins.map((b, index) => ({ b, index })).filter(({ b }) => b.kind === st.bin).map(({ b, index }) => `<button class="bin-row" data-act="am.add" data-index="${index}" title="加入时间轴"><span class="th">${W.art(b.kind === 'shot' ? '场景' : '道具', b.hue || 200, { wide: true })}</span><span class="t"><strong>${E(b.name)}</strong><span>${b.dur} 秒</span></span><span class="add">${I('plus', 'i-13')}</span></button>`).join('') || '<p class="hint" style="padding:12px">还没有此类媒体。</p>'}</div></aside>`;
  }
  function monitor() {
    const clip = at(st.playhead), subtitle = st.clips.find(c => c.track === 'sub' && st.playhead >= c.start && st.playhead < c.start + c.dur);
    return `<section class="panel monitor"><header class="mon-head"><h2>剪辑预览</h2><span class="tag" id="currentShot">${clip?.no || '—'}</span><div class="right"><button class="icon-btn" data-act="am.full" title="全屏预览">${I('expand', 'i-13')}</button></div></header><div class="mon-body"><div class="screen" id="screen">${clip && !st.muted.pic ? W.art('场景', clip.hue, { wide: true }) : ''}<span class="sub" id="subLine" ${!subtitle || st.muted.sub ? 'hidden' : ''}>${E(subtitle?.name || '')}</span></div></div>
      <div class="transport"><button class="icon-btn" data-act="am.skip" data-d="-5" title="后退 5 秒">${I('back', 'i-13')}</button><button class="play" data-act="am.play" id="playBtn" aria-label="${st.playing ? '暂停预览' : '播放预览'}">${I(st.playing ? 'pause' : 'play', 'i-15')}</button><button class="icon-btn" data-act="am.skip" data-d="5" title="前进 5 秒">${I('forward', 'i-13')}</button><span class="tm" id="tm">${W.tc(st.playhead)} / ${W.tc(total())}</span><div class="right"><span>16:9</span></div></div></section>`;
  }
  function timeline() {
    const duration = total(), ticks = [];
    for (let t = 0; t < duration; t += 5) ticks.push(`<span class="tick" style="left:${t / duration * 100}%"></span><span class="lab" style="left:${t / duration * 100}%">${W.tc(t)}</span>`);
    return `<section class="panel timeline"><header class="tl-head"><h3>时间轴</h3><div class="timeline-actions"><button class="btn quiet sm" data-act="am.undo" ${!st.undo.length ? 'disabled' : ''} title="撤销（Ctrl+Z）">撤销</button><button class="btn quiet sm" data-act="am.redo" ${!st.redo.length ? 'disabled' : ''} title="重做（Ctrl+Shift+Z）">重做</button><button class="btn quiet sm" data-act="am.split">${I('scissors', 'i-12')}分割</button></div><div class="right"><button class="icon-btn" data-act="am.zoom" data-d="-0.25" title="缩小时间轴">${I('left', 'i-13')}</button><button class="btn quiet sm" data-act="am.fit">适应</button><button class="icon-btn" data-act="am.zoom" data-d="0.25" title="放大时间轴">${I('right', 'i-13')}</button></div></header>
      <div class="timeline-scroll"><div class="tl-body" style="min-width:${720 * st.zoom}px"><div class="tl-labels"><div class="tl-label ruler-label">轨道</div>${TRACKS.map(([key, name, icon]) => `<div class="tl-label${st.muted[key] ? ' is-muted' : ''}">${I(icon, 'i-12')}<span>${name}</span><button class="track-button${st.locked[key] ? ' on' : ''}" data-act="am.lock" data-track="${key}" aria-pressed="${!!st.locked[key]}" title="${st.locked[key] ? '解锁' : '锁定'}${name}轨道">锁</button><button class="track-button${st.muted[key] ? ' on' : ''}" data-act="am.mute" data-track="${key}" aria-pressed="${!!st.muted[key]}" title="${st.muted[key] ? '取消静音' : '静音'}${name}轨道">静</button></div>`).join('')}</div><div class="tl-lanes" id="lanes"><div class="tl-ruler" id="ruler">${ticks.join('')}</div>${TRACKS.map(([key]) => `<div class="tl-lane${st.muted[key] ? ' is-muted' : ''}${st.locked[key] ? ' is-locked' : ''}" data-track="${key}">${st.clips.filter(c => c.track === key).map(c => `<button class="clip ${key}${st.selected === c.id ? ' sel' : ''}" data-act="am.select" data-id="${c.id}" style="left:${c.start / duration * 100}%;width:${Math.max(.5, c.dur / duration * 100)}%" aria-label="${E(c.name)}，${c.start.toFixed(2)} 秒开始，时长 ${c.dur.toFixed(2)} 秒"><span class="nm">${E(c.name || c.no)}</span><span class="bar"></span>${c.unsynced ? '<span class="clip-source-warning" title="来源已更新">!</span>' : ''}</button>`).join('')}</div>`).join('')}<div class="playhead" id="playhead" style="left:${st.playhead / duration * 100}%"></div></div></div></div></section>`;
  }
  function inspector() {
    const clip = selected();
    if (!clip) return '';
    const locked = st.locked[clip.track];
    return `<aside class="panel am-insp"><header class="head"><h2>片段属性</h2><span class="tag">${TRACKS.find(([key]) => key === clip.track)?.[1]}</span><div class="actions"><button class="icon-btn" data-act="am.closeProperties" title="关闭片段属性">${I('x', 'i-13')}</button></div></header><div class="as-body"><label class="field"><span class="field-label">${clip.track === 'sub' ? '字幕内容' : '名称'}</span><input class="inp" data-clip="name" value="${E(clip.name)}" ${locked ? 'disabled' : ''}></label>${clip.no ? `<button class="btn quiet" data-act="am.openShot">${I('film', 'i-13')}打开来源 ${E(clip.no)}</button>` : ''}<div class="clip-times"><label class="field"><span class="field-label">开始（秒）</span><input class="inp" type="number" min="0" step="0.25" data-clip="start" value="${clip.start}" ${locked ? 'disabled' : ''}></label><label class="field"><span class="field-label">时长（秒）</span><input class="inp" type="number" min="0.25" step="0.25" data-clip="dur" value="${clip.dur}" ${locked ? 'disabled' : ''}></label></div>${clip.track !== 'sub' ? `<label class="field"><span class="field-label">音量 <span>${clip.volume ?? 100}%</span></span><input type="range" min="0" max="100" data-clip="volume" value="${clip.volume ?? 100}" ${locked ? 'disabled' : ''}></label>` : ''}${clip.track === 'pic' ? `<label class="field"><span class="field-label">画面适配</span><select class="inp" data-clip="fit" ${locked ? 'disabled' : ''}><option value="contain"${clip.fit !== 'cover' ? ' selected' : ''}>适应</option><option value="cover"${clip.fit === 'cover' ? ' selected' : ''}>填充</option></select></label>` : ''}${locked ? '<p class="hint">轨道已锁定，解锁后可编辑片段。</p>' : ''}<div class="divider"></div><button class="btn danger block" data-act="am.delete" ${locked ? 'disabled' : ''}>${I('trash', 'i-13')}删除片段</button></div></aside>`;
  }
  function render() {
    return `${st.binOpen || st.selected ? '<button class="assembly-sheet-backdrop" data-act="am.closeSheets" aria-label="关闭侧栏"></button>' : ''}${st.focus ? '' : rail()}<div class="am-main"><header class="assembly-head"><button class="icon-btn mobile-only" data-act="am.binToggle" title="媒体列表">${I('list')}</button><h2>成片合成</h2><div class="actions"><button class="btn" data-act="am.sync">${I('sync', 'i-13')}同步分镜视频${st.unsynced.length ? `<span class="source-count">${st.unsynced.length}</span>` : ''}</button><button class="icon-btn" data-act="am.history" title="编辑记录">${I('history', 'i-15')}</button><button class="icon-btn" data-act="am.focus" title="${st.focus ? '退出专注剪辑' : '专注剪辑'}">${I(st.focus ? 'panelOpen' : 'expand', 'i-15')}</button><button class="btn primary" data-act="am.export">${I('download', 'i-13')}导出</button></div></header>${monitor()}${timeline()}</div>${inspector()}<input type="file" id="assemblyUpload" accept="video/*,audio/*" hidden>`;
  }
  let raf, last = 0;
  function paint() {
    const playhead = document.getElementById('playhead'), time = document.getElementById('tm'), screen = document.getElementById('screen'), clip = at(st.playhead);
    if (playhead) playhead.style.left = st.playhead / total() * 100 + '%';
    if (time) time.textContent = W.tc(st.playhead) + ' / ' + W.tc(total());
    const tag = document.getElementById('currentShot'); if (tag) tag.textContent = clip?.no || '—';
    if (screen) { const art = screen.querySelector('.art'); if (art && clip) art.style.setProperty('--hue', (clip.hue || 0) + 'deg'); if (art) art.style.visibility = clip && !st.muted.pic ? 'visible' : 'hidden'; const sub = st.clips.find(c => c.track === 'sub' && st.playhead >= c.start && st.playhead < c.start + c.dur); const line = document.getElementById('subLine'); line.hidden = !sub || st.muted.sub; line.textContent = sub?.name || ''; }
  }
  function stop() { st.playing = false; cancelAnimationFrame(raf); const button = document.getElementById('playBtn'); if (button) { button.innerHTML = I('play', 'i-15'); button.setAttribute('aria-label', '播放预览'); } }
  function loop(now) { if (!st.playing || W.S.view !== 'assembly') { stop(); return; } st.playhead += Math.min(.1, (now - last) / 1000); last = now; if (st.playhead >= total()) { st.playhead = total(); stop(); } paint(); if (st.playing) raf = requestAnimationFrame(loop); }
  function play() { if (st.playing) return; if (st.playhead >= total()) st.playhead = 0; st.playing = true; last = performance.now(); const button = document.getElementById('playBtn'); if (button) { button.innerHTML = I('pause', 'i-15'); button.setAttribute('aria-label', '暂停预览'); } raf = requestAnimationFrame(loop); }
  function fitScreen() { const body = document.querySelector('.mon-body'), screen = document.querySelector('.screen'); if (!body || !screen) return; const width = Math.max(0, Math.min(body.clientWidth - 24, (body.clientHeight - 24) * 16 / 9)); screen.style.width = width + 'px'; screen.style.height = width * 9 / 16 + 'px'; }

  function drag(event) {
    if (event.button !== 0) return;
    const clipNode = event.target.closest('.clip'), ruler = event.target.closest('.tl-ruler');
    if (!clipNode && !ruler) return;
    const lanes = document.getElementById('lanes'), rect = lanes.getBoundingClientRect(), duration = total();
    const clip = clipNode && st.clips.find(c => c.id === clipNode.dataset.id);
    if (clip && st.locked[clip.track]) return;
    const before = snapshot(), startX = event.clientX, start = clip?.start;
    let moved = false;
    const move = e => {
      if (clip) { if (Math.abs(e.clientX - startX) < 3 && !moved) return; moved = true; clip.start = Math.max(0, Math.round((start + (e.clientX - startX) / rect.width * duration) * 4) / 4); clipNode.style.left = clip.start / duration * 100 + '%'; }
      else { st.playhead = Math.round(Math.max(0, Math.min(duration, (e.clientX - rect.left) / rect.width * duration)) * 1000) / 1000; paint(); }
    };
    if (ruler) move(event);
    const up = () => { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', up); if (clip) { st.selected = clip.id; if (moved) checkpoint('移动片段', before); update(); } };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', up);
  }
  function exportDialog() {
    W.modal({ title: '导出成片', width: 500, body: `<label class="field"><span class="field-label">画幅</span><select class="inp"><option>自动</option><option>16:9</option><option>9:16</option></select></label><label class="field"><span class="field-label">清晰度</span><select class="inp"><option>1080p</option><option>720p</option></select></label><label class="field"><span class="field-label">字幕</span><select class="inp"><option>烧录进画面</option><option>不烧录字幕</option></select></label><div class="export-summary"><span>帧率 30 fps</span><span>时长 ${W.tc(total())}</span></div><p class="hint" id="exportState" role="status"></p>`, foot: '<button class="btn" data-act="overlay.close">取消</button><button class="btn primary" data-act="am.doExport">开始导出</button>' });
  }
  Object.assign(W.actions, {
    'am.select': el => { st.selected = el.dataset.id; update(); },
    'am.closeProperties': () => { st.selected = null; W.render(); },
    'am.closeSheets': () => { st.selected = null; st.binOpen = false; W.render(); },
    'am.binToggle': () => { st.binOpen = !st.binOpen; st.selected = null; W.render(); },
    'am.bin': el => { st.bin = el.dataset.id; W.render(); },
    'am.focus': () => { st.focus = !st.focus; W.render(); },
    'am.play': () => st.playing ? stop() : play(),
    'am.skip': el => { st.playhead = Math.max(0, Math.min(total(), st.playhead + Number(el.dataset.d))); paint(); },
    'am.fit': () => { st.zoom = 1; W.render(); },
    'am.zoom': el => { st.zoom = Math.max(.75, Math.min(3, st.zoom + Number(el.dataset.d))); W.render(); },
    'am.lock': el => { const key = el.dataset.track; st.locked[key] = !st.locked[key]; W.render(); },
    'am.mute': el => { const key = el.dataset.track; st.muted[key] = !st.muted[key]; W.render(); },
    'am.undo': () => { const entry = st.undo.pop(); if (!entry) return; st.redo.push({ label: entry.label, value: snapshot() }); restore(entry.value); },
    'am.redo': () => { const entry = st.redo.pop(); if (!entry) return; st.undo.push({ label: entry.label, value: snapshot() }); restore(entry.value); },
    'am.delete': () => { const clip = selected(); if (!clip || st.locked[clip.track]) return; checkpoint('删除片段'); st.clips = st.clips.filter(c => c.id !== clip.id); st.selected = null; update(); },
    'am.split': () => { const clip = selected() || at(st.playhead); if (!clip || st.locked[clip.track]) return; const left = st.playhead - clip.start; if (left < .25 || clip.dur - left < .25) { W.toast('将播放头移到片段内部再分割', 'warn'); return; } checkpoint('分割片段'); st.clips.push({ ...clip, id: clip.id + '-split-' + Date.now(), start: st.playhead, dur: clip.dur - left }); clip.dur = left; update(); },
    'am.add': el => { const b = W.D.bins[Number(el.dataset.index)], track = b.kind === 'shot' ? 'pic' : b.kind; if (st.locked[track]) { W.toast('请先解锁目标轨道', 'warn'); return; } checkpoint('媒体入轨'); const start = Math.max(0, ...st.clips.filter(c => c.track === track).map(c => c.start + c.dur)); st.clips.push({ id: 'clip-' + Date.now(), track, no: b.no, name: b.name, start, dur: b.dur, hue: b.hue || 200 }); st.binOpen = false; update(); },
    'am.sync': () => W.modal({ title: '同步分镜视频', width: 520, body: st.unsynced.length ? `<p class="dialog-copy">${st.unsynced.join('、')} 的来源已更新。</p><p class="hint">更新对应视频来源，保留当前剪辑位置、时长和声音轨道。</p>` : '<p class="hint">当前没有待同步的视频来源。</p>', foot: `<button class="btn" data-act="overlay.close">关闭</button>${st.unsynced.length ? '<button class="btn primary" data-act="am.syncApply">同步更新</button>' : ''}` }),
    'am.syncApply': () => { checkpoint('同步分镜视频'); st.clips.filter(c => c.track === 'pic' && st.unsynced.includes(c.no)).forEach(c => { c.unsynced = false; c.hue = W.S.board.shots.find(shot => shot.no === c.no)?.hue ?? c.hue; }); st.unsynced = []; W.closeOverlays(); update(); },
    'am.history': () => W.modal({ title: '编辑记录', width: 480, body: st.undo.length ? st.undo.slice().reverse().map(entry => `<div class="media-history-row"><strong>${E(entry.label)}</strong></div>`).join('') : '<p class="hint">本次打开后还没有编辑记录。编辑后可通过撤销与重做恢复。</p>', foot: '<button class="btn" data-act="overlay.close">关闭</button>' }),
    'am.openShot': () => { const clip = selected(), index = W.S.board.shots.findIndex(shot => shot.no === clip?.no); if (index >= 0) W.S.board.selected = index; W.S.board.mode = 'video'; W.go('board'); },
    'am.full': () => { const clip = at(st.playhead); W.modal({ title: '剪辑预览', width: 1100, body: `<div class="history-picture">${clip ? W.art('场景', clip.hue, { wide: true }) : '<p>此处没有画面片段</p>'}</div>`, foot: '<button class="btn" data-act="overlay.close">关闭</button>' }); },
    'am.export': exportDialog,
    'am.doExport': el => { el.disabled = true; document.getElementById('exportState').textContent = '正在合成…'; setTimeout(() => { W.closeOverlays(); W.toast('导出流程演示完成，未生成视频文件'); }, 1400); },
    'am.upload': () => document.getElementById('assemblyUpload').click()
  });
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented) return;
    if (W.S.view !== 'assembly' || event.target.matches('input, textarea, select') || W.overlays().children.length) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); W.actions[event.shiftKey ? 'am.redo' : 'am.undo'](); }
    if (event.key === 'Escape') { st.selected = null; st.binOpen = false; if (st.focus) st.focus = false; W.render(); }
    if (event.key === ' ' && !event.target.closest('button')) { event.preventDefault(); W.actions['am.play'](); }
    const node = event.target.closest('.clip');
    if (node && ['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); const clip = st.clips.find(c => c.id === node.dataset.id); if (st.locked[clip.track]) return; checkpoint('移动片段'); clip.start = Math.max(0, clip.start + (event.key === 'ArrowLeft' ? -.25 : .25)); update(); document.querySelector(`[data-id="${clip.id}"]`)?.focus(); }
  });
  let resizeObserver;
  W.views.assembly = { render, leave: stop, mount() {
    resizeObserver?.disconnect(); fitScreen(); const body = document.querySelector('.mon-body'); if (body) { resizeObserver = new ResizeObserver(fitScreen); resizeObserver.observe(body); }
    document.getElementById('lanes').addEventListener('pointerdown', drag);
    document.querySelectorAll('[data-clip]').forEach(input => input.addEventListener('change', () => { const clip = selected(); if (!clip || st.locked[clip.track]) return; const key = input.dataset.clip; const value = ['start', 'dur', 'volume'].includes(key) ? Number(input.value) : input.value; if (typeof value === 'number' && (!Number.isFinite(value) || value < (key === 'dur' ? .25 : 0))) { input.value = clip[key]; return; } checkpoint('修改片段属性'); clip[key] = value; update(); }));
    document.getElementById('assemblyUpload').addEventListener('change', event => { const file = event.target.files[0]; if (!file) return; W.D.bins.push({ name: file.name, kind: file.type.startsWith('video/') ? 'shot' : 'mus', dur: 5, hue: 190 }); st.bin = file.type.startsWith('video/') ? 'shot' : 'mus'; W.render(); });
  } };
})();
