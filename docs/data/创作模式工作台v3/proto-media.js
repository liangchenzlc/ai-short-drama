/* 资产与分镜共用的图片组、参考输入与记录。所有生成仅为本地交互演示。 */
(function () {
  const W = window.WB;
  const I = W.icon, E = W.esc;
  const state = () => W.S[W.S.view];
  const current = () => W.views[W.S.view]?.current?.();
  const videoMode = () => W.S.view === 'board' && state().mode === 'video';

  function init(item, options = {}) {
    if (item.images) return;
    item.kind = item.kind || options.kind || '场景';
    item.images = item.status === 'wait' || item.status === 'bad' ? [] :
      Array.from({ length: item.count || 1 }, (_, i) => ({ kind: item.kind, hue: item.hue + i * 16, ratio: item.size?.includes('1365') ? '3 / 4' : item.size?.includes('1536') || options.shot ? '16 / 9' : '1 / 1' }));
    item.primary = 0;
    item.count = item.count || 1;
    item.ratio = '默认';
    item.model = item.model || 'Flux 1.1 Pro';
    item.videoModel = 'Kling 2.1';
    item.videoSize = '1080p';
    item.motion = item.move ? `${item.move}，保持人物外观与场景一致，动作自然。` : '';
    item.prompt = item.prompt || item.act || '';
    item.references = W.S.assets.list.filter(a => a.id !== item.id && a.status === 'ok').slice(0, Math.min(2, item.refs || (options.shot ? 2 : 0))).map(a => ({ name: a.name, kind: a.kind, hue: a.hue }));
    item.imageHistory = item.images.length ? [{ label: '当前图片组', at: item.at || '14:32', images: item.images.map(image => ({ ...image })), primary: 0 }] : [];
    item.videoHistory = item.video ? [{ label: '当前视频', at: '14:35', dur: item.dur }] : [];
    if (item.status === 'bad') item.error = '上次图片生成失败，请检查输入后重试。';
  }

  function picture(image, className = '') {
    return image.url ? `<img class="media-photo ${className}" src="${E(image.url)}" alt="${E(image.name || '当前图片')}">` : W.art(image.kind || '角色', image.hue || 0, { wide: image.kind === '场景' });
  }

  function rail(items, { title, query, filters, selected, empty }) {
    const st = state();
    return `<aside class="panel media-rail${st.railOpen ? ' is-open' : ''}" aria-label="${title}">
      <header class="head"><h2>${title}</h2><span class="list-count">${items.length}</span><div class="actions">
        <button class="icon-btn" data-act="${W.S.view === 'assets' ? 'asset.add' : 'board.add'}" title="${W.S.view === 'assets' ? '新增资产' : '新增镜头'}">${I('plus')}</button>
        <button class="icon-btn" data-act="${W.S.view === 'assets' ? 'asset.extract' : 'board.extract'}" title="从剧本提取">${I('spark')}</button>
        <button class="icon-btn mobile-only" data-act="media.rail" title="关闭列表">${I('x')}</button></div></header>
      <label class="media-search">${I('search', 'i-13')}<input type="search" data-media-search value="${E(query)}" placeholder="${W.S.view === 'assets' ? '搜索名称或描述' : '搜索镜头名称或脚本'}" aria-label="${title}搜索"></label>
      <div class="media-filters">${filters.map(([id, label]) => `<button class="chip${st.filter === id ? ' on' : ''}" data-act="media.filter" data-value="${id}" aria-pressed="${st.filter === id}">${label}</button>`).join('')}</div>
      <div class="media-list">${items.length ? items.map(item => `<button class="media-card" data-act="media.select" data-id="${E(item.id || item.no)}" aria-current="${selected === (item.id || item.no)}">
        <span class="media-thumb">${item.images.length ? picture(item.images[item.primary] || item.images[0]) : `<span class="thumb-empty">${I('image', 'i-20')}<span>${item.busy ? '生成中' : '暂无图片'}</span></span>`}</span>
        <span class="media-card-caption"><strong>${E(item.name || item.no)}</strong><span>${E(W.S.view === 'assets' ? item.kind : item.no)}</span></span>
        ${item.label ? `<span class="media-card-label">${E(item.label)}</span>` : ''}
      </button>`).join('') : `<div class="list-empty">${I('search', 'i-20')}<p>${empty}</p>${query || st.filter !== 'all' ? '<button class="btn" data-act="media.clearSearch">清空筛选</button>' : ''}</div>`}</div>
    </aside>`;
  }

  function images(item) {
    if (!item.images.length) return `<div class="media-empty">${I('image', 'i-20')}<h3>${item.busy ? '正在生成图片' : '还没有' + (W.S.view === 'assets' ? '资产图' : '分镜图')}</h3>
      <p>${item.busy ? '生成完成后会显示在这里。' : '添加参考图片，填写要求后生成，也可以上传图片。'}</p><button class="btn" data-act="media.upload">${I('upload', 'i-13')}上传图片</button></div>`;
    return `<div class="media-grid count-${item.images.length}">${item.images.map((image, index) => `<section class="media-image">
      <header><span>${index + 1}</span>${index === item.primary ? `<span class="primary-label">${I('check', 'i-12')}主图</span>` : ''}</header>
      <button class="media-frame" data-act="media.full" data-index="${index}" aria-label="放大第 ${index + 1} 张图片">${picture(image)}</button>
      <div class="media-image-tools"><button class="icon-btn" data-act="media.full" data-index="${index}" title="放大图片">${I('expand', 'i-13')}</button>
        <button class="icon-btn" data-act="media.imageMenu" data-index="${index}" title="图片工具">${I('pen', 'i-13')}</button>
        ${index !== item.primary ? `<button class="btn quiet sm" data-act="media.primary" data-index="${index}">设为主图</button>` : ''}
        <button class="icon-btn" data-act="media.download" data-index="${index}" title="下载图片">${I('download', 'i-13')}</button></div>
    </section>`).join('')}</div>`;
  }

  function video(item) {
    if (!item.video) return `<div class="media-empty">${I('film', 'i-20')}<h3>${item.busy === 'video' ? '正在生成分镜视频' : '还没有分镜视频'}</h3><p>${item.images.length ? '当前主图将作为视频参考，在生成设置中填写运动要求。' : '先生成或上传分镜图，再制作视频。'}</p>${!item.images.length ? '<button class="btn" data-act="board.mode" data-value="image">制作分镜图</button>' : ''}</div>`;
    return `<div class="media-player"><div class="video-stage">${picture(item.images[item.primary] || { kind: '角色', hue: item.hue })}<button class="player-play" data-act="media.play" aria-label="${item.playing ? '暂停视频' : '播放视频'}">${I(item.playing ? 'pause' : 'play', 'i-20')}</button></div>
      <div class="video-controls"><button class="icon-btn" data-act="media.play" title="${item.playing ? '暂停' : '播放'}">${I(item.playing ? 'pause' : 'play')}</button><span class="video-time">${W.tc(item.playhead || 0)} / ${W.tc(item.dur)}</span><input type="range" aria-label="视频播放位置" data-video-seek min="0" max="${item.dur}" step="0.1" value="${item.playhead || 0}"><button class="icon-btn" data-act="media.full" data-index="${item.primary}" title="全屏预览">${I('expand', 'i-13')}</button></div></div>`;
  }

  function references(item) {
    return `<div class="field"><div class="field-label"><span>参考图片</span><span>${item.references.length} / 16</span></div><div class="reference-list">${item.references.map((ref, i) => `<div class="media-reference">${picture(ref)}<span>图片${i + 1}</span><button data-act="media.removeRef" data-index="${i}" aria-label="移除图片${i + 1}">${I('x', 'i-11')}</button></div>`).join('')}
      ${item.references.length < 16 ? `<button class="reference-add" data-act="media.refs" aria-label="添加参考图片">${I('plus', 'i-17')}</button>` : ''}</div></div>`;
  }

  function prompt(item) {
    const isVideo = videoMode();
    return `<div class="prompt-box"><label class="sr-only" for="mediaPrompt">${isVideo ? '运动提示词' : '画面提示词'}</label><textarea id="mediaPrompt" data-media-field="${isVideo ? 'motion' : 'prompt'}" placeholder="${isVideo ? '描述人物动作、镜头运动和节奏' : '描述本次画面要求，输入 @ 引用参考图片'}">${E(isVideo ? item.motion : item.prompt)}</textarea>
      <div class="mention-menu" id="mentionMenu" hidden></div><div class="prompt-controls"><label class="model-select"><span class="sr-only">${isVideo ? '视频模型' : '图片模型'}</span>${I('cpu', 'i-13')}<select data-media-field="${isVideo ? 'videoModel' : 'model'}" aria-label="${isVideo ? '视频模型' : '图片模型'}">${(isVideo ? ['Kling 2.1', 'Seedance 1.0'] : ['Flux 1.1 Pro', '即梦']).map(model => `<option${model === (isVideo ? item.videoModel : item.model) ? ' selected' : ''}>${model}</option>`).join('')}</select></label>
      <button class="icon-btn" data-act="media.options" title="生成选项">${I('layers', 'i-15')}</button><button class="send-btn" data-act="media.generate" aria-label="${isVideo ? '生成分镜视频' : W.S.view === 'assets' ? '生成资产图' : '生成分镜图'}" title="${isVideo ? '生成分镜视频' : W.S.view === 'assets' ? '生成资产图' : '生成分镜图'}" ${item.busy || (isVideo && !item.images.length) ? 'disabled' : ''}>${I(item.busy ? 'clock' : 'send', 'i-15')}</button></div></div>`;
  }

  function workspace({ list, title, fields, beforePrompt = '' }) {
    const st = state(), item = current(), isVideo = videoMode();
    const panel = item ? `<section class="panel media-main"><header class="media-head"><button class="icon-btn mobile-only" data-act="media.rail" title="${W.S.view === 'assets' ? '资产列表' : '镜头列表'}">${I('list')}</button><h2>${E(item.name || item.no)}</h2>
      ${W.S.view === 'board' ? `<div class="seg media-tabs" role="tablist" aria-label="分镜媒体"><button role="tab" aria-selected="${!isVideo}" tabindex="${isVideo ? -1 : 0}" class="${!isVideo ? 'on' : ''}" data-act="board.mode" data-value="image">分镜图</button><button role="tab" aria-selected="${isVideo}" tabindex="${isVideo ? 0 : -1}" class="${isVideo ? 'on' : ''}" data-act="board.mode" data-value="video">分镜视频</button></div>` : ''}
      <div class="actions">${W.S.view === 'board' ? `<button class="icon-btn" data-act="board.prev" title="上一镜" ${st.selected === 0 ? 'disabled' : ''}>${I('left')}</button><button class="icon-btn" data-act="board.next" title="下一镜" ${st.selected === st.shots.length - 1 ? 'disabled' : ''}>${I('right')}</button>` : ''}
      <button class="icon-btn" data-act="media.history" title="生成记录">${I('history')}</button><button class="icon-btn" data-act="${W.S.view === 'assets' ? 'asset.more' : 'board.more'}" title="更多">${I('more')}</button><button class="icon-btn" data-act="media.settings" title="生成设置">${I('panel')}</button></div></header>
      <div class="media-body"><div class="media-preview">${isVideo ? video(item) : images(item)}${item.busy ? `<span class="media-running" role="status">${I('clock', 'i-13')}${item.busy === 'video' ? '视频生成中' : '图片生成中'}</span>` : ''}
      ${item.error && !isVideo ? `<div class="media-error" role="status">${I('alert', 'i-13')}<span>${E(item.error)}</span><button class="btn sm" data-act="media.generate">重试</button></div>` : ''}</div>
      <aside class="media-settings${st.settingsOpen === false ? ' is-hidden' : ''}${st.mobileSettings ? ' is-open' : ''}" aria-label="生成设置"><header class="head"><h2>生成设置</h2><div class="actions"><button class="icon-btn" data-act="media.settings" title="收起生成设置">${I('x', 'i-13')}</button></div></header>
        <div class="media-settings-body">${fields(item)}${isVideo ? `<div class="field"><span class="field-label">主图参考</span><div class="video-reference">${item.images.length ? picture(item.images[item.primary]) : I('image', 'i-20')}<span>${item.images.length ? '当前分镜主图' : '暂无主图'}</span></div></div>` : references(item)}${beforePrompt}${prompt(item)}</div></aside></div></section>` : `<section class="panel media-main"><header class="media-head"><button class="icon-btn mobile-only" data-act="media.rail" title="打开列表">${I('list')}</button><h2>${title}</h2></header><div class="media-empty"><h3>还没有${title}</h3><p>从剧本提取，或添加一个${W.S.view === 'assets' ? '资产' : '镜头'}开始制作。</p><button class="btn" data-act="${W.S.view === 'assets' ? 'asset.add' : 'board.add'}">${I('plus', 'i-13')}新增${W.S.view === 'assets' ? '资产' : '镜头'}</button></div></section>`;
    return `${st.railOpen || st.mobileSettings ? '<button class="media-sheet-backdrop" data-act="media.closeSheets" aria-label="关闭侧栏"></button>' : ''}${list}${panel}<input type="file" id="mediaUpload" accept="image/*" hidden>`;
  }

  function generate(item = current(), mode = videoMode() ? 'video' : 'image', editIndex) {
    if (!item || item.busy || (mode === 'video' && !item.images.length)) return;
    const view = W.S.view;
    item.busy = mode; item.error = '';
    const count = item.count;
    const ratio = ({ '1:1': '1 / 1', '3:4': '3 / 4', '16:9': '16 / 9', '9:16': '9 / 16' })[item.ratio] || item.images[0]?.ratio || '1 / 1';
    if (view === W.S.view) W.render();
    setTimeout(() => {
      const at = new Date().toTimeString().slice(0, 5);
      if (mode === 'video') { item.video = true; item.playhead = 0; item.videoHistory.unshift({ label: '视频', at, dur: item.dur }); }
      else {
        if (editIndex !== undefined && item.images[editIndex]) item.images[editIndex] = { kind: item.kind, hue: item.hue + (item.imageHistory.length + 1) * 15, ratio: item.images[editIndex].ratio || ratio };
        else { item.images = Array.from({ length: count }, (_, i) => ({ kind: item.kind, hue: item.hue + i * 19 + item.imageHistory.length * 4, ratio })); item.primary = 0; }
        item.status = 'ok'; item.imageHistory.unshift({ label: editIndex === undefined ? '图片组' : '图片编辑', at, images: item.images.map(image => ({ ...image })), primary: item.primary });
      }
      item.busy = null;
      if (W.S.view === view) { W.render(); W.save(); }
    }, 1400);
  }

  function full(item, index) {
    const image = item.images[index];
    if (!image) return;
    W.overlays().innerHTML = `<div class="lightbox" role="dialog" aria-modal="true" aria-label="图片预览"><div class="lb-head"><span>${E(item.name || item.no)} · ${index + 1} / ${item.images.length}</span><div class="right"><button class="icon-btn" data-act="media.full" data-index="${(index + item.images.length - 1) % item.images.length}" title="上一张">${I('left')}</button><button class="icon-btn" data-act="media.full" data-index="${(index + 1) % item.images.length}" title="下一张">${I('right')}</button><button class="icon-btn" data-act="media.download" data-index="${index}" title="下载图片">${I('download')}</button><button class="icon-btn" data-act="overlay.close" title="关闭预览">${I('x')}</button></div></div><div class="lb-body">${picture(image)}</div></div>`;
    W.overlays().querySelector('[data-act="overlay.close"]').focus();
  }

  Object.assign(W.actions, {
    'media.select': el => { W.views[W.S.view].select(el.dataset.id); state().railOpen = false; W.render(); },
    'media.filter': el => { state().filter = el.dataset.value; W.render(); },
    'media.clearSearch': () => { state().query = ''; state().filter = 'all'; W.render(); },
    'media.rail': () => { state().railOpen = !state().railOpen; state().mobileSettings = false; W.render(); },
    'media.settings': () => { const st = state(); if (innerWidth <= 980) { st.mobileSettings = !st.mobileSettings; st.railOpen = false; } else st.settingsOpen = st.settingsOpen === false; W.render(); },
    'media.closeSheets': () => { state().railOpen = state().mobileSettings = false; W.render(); },
    'media.primary': el => { current().primary = Number(el.dataset.index); W.render(); W.save(); },
    'media.full': el => full(current(), Number(el.dataset.index || 0)),
    'media.generate': () => generate(),
    'media.play': () => { const item = current(); item.playing = !item.playing; W.render(); },
    'media.upload': el => { state().uploadTarget = 'image'; state().toolImage = el.dataset.index === undefined ? undefined : Number(el.dataset.index); document.getElementById('mediaUpload').click(); },
    'media.removeRef': el => { const item = current(), index = Number(el.dataset.index); item.references.splice(index, 1); item.prompt = item.prompt.replace(/@图片(\d+)/g, (match, number) => Number(number) === index + 1 ? '' : Number(number) > index + 1 ? '@图片' + (Number(number) - 1) : match); W.render(); W.save(); },
    'media.refs': () => {
      W.modal({ title: '添加参考图片', body: `<button class="btn" data-act="media.uploadRef">${I('upload', 'i-13')}上传本地图片</button><h4 class="dialog-section-title">媒体资产库</h4><div class="reference-picker">${W.S.assets.list.filter(a => a.images.length).map(a => `<button data-act="media.pickRef" data-id="${a.id}">${picture(a.images[a.primary])}<span>${E(a.name)}${a.label ? ' · ' + E(a.label) : ''}</span></button>`).join('')}</div>`, foot: '<button class="btn" data-act="overlay.close">关闭</button>', width: 620 });
    },
    'media.uploadRef': () => { state().uploadTarget = 'reference'; document.getElementById('mediaUpload').click(); },
    'media.pickRef': el => { const asset = W.S.assets.list.find(a => a.id === el.dataset.id), item = current(); if (asset && item.references.length < 16) item.references.push({ ...asset.images[asset.primary], name: asset.name }); W.closeOverlays(); W.render(); W.save(); },
    'media.mention': el => { const ta = document.getElementById('mediaPrompt'), item = current(); const before = ta.value.slice(0, ta.selectionStart).replace(/@[^\s@]*$/, '@图片' + (Number(el.dataset.index) + 1)); item.prompt = before + ta.value.slice(ta.selectionEnd); ta.value = item.prompt; document.getElementById('mentionMenu').hidden = true; ta.focus(); ta.setSelectionRange(before.length, before.length); W.save(); },
    'media.options': () => {
      const item = current(), video = videoMode();
      W.modal({ title: '生成选项', width: 400, body: video ? `<label class="field"><span class="field-label">清晰度</span><select class="inp" data-media-field="videoSize"><option${item.videoSize === '1080p' ? ' selected' : ''}>1080p</option><option${item.videoSize === '720p' ? ' selected' : ''}>720p</option></select></label><label class="field"><span class="field-label">时长</span><select class="inp" data-media-field="dur">${[5, 6, 10].map(n => `<option value="${n}"${n === item.dur ? ' selected' : ''}>${n} 秒</option>`).join('')}</select></label>` : `<label class="field"><span class="field-label">生成数量</span><select class="inp" data-media-field="count">${[1, 2, 4].map(n => `<option value="${n}"${n === item.count ? ' selected' : ''}>${n} 张</option>`).join('')}</select></label><label class="field"><span class="field-label">画面比例</span><select class="inp" data-media-field="ratio">${['默认', '1:1', '3:4', '16:9', '9:16'].map(n => `<option${n === item.ratio ? ' selected' : ''}>${n}</option>`).join('')}</select></label>`, foot: '<button class="btn primary" data-act="overlay.close">完成</button>' });
    },
    'media.history': () => {
      const item = current(), isVideo = videoMode(), records = isVideo ? item.videoHistory : item.imageHistory;
      W.modal({ title: isVideo ? '视频生成记录' : '图片生成记录', width: 650, body: records.length ? records.map((record, i) => `<div class="media-history-row">${isVideo ? I('film', 'i-20') : `<div class="history-thumb">${picture(record.images[0])}</div>`}<span><strong>${i === 0 ? '当前' : record.label}</strong><small>${record.at} · ${isVideo ? record.dur + ' 秒' : record.images.length + ' 张图片'}</small></span><button class="btn sm" data-act="media.historyView" data-index="${i}">${isVideo ? '回看' : '查看'}</button>${!isVideo && i > 0 ? `<button class="btn sm" data-act="media.restore" data-index="${i}">恢复图片组</button>` : ''}</div>`).join('') : '<p class="hint">还没有生成记录。</p>', foot: '<button class="btn" data-act="overlay.close">关闭</button>' });
    },
    'media.historyView': el => { const item = current(), index = Number(el.dataset.index); if (videoMode()) W.modal({ title: '历史视频', body: `<div class="history-video">${picture(item.images[item.primary])}${I('play', 'i-20')}</div><p class="hint">${item.videoHistory[index].at} · ${item.videoHistory[index].dur} 秒</p>`, foot: '<button class="btn" data-act="media.history">返回记录</button>' }); else { const image = item.imageHistory[index].images[0]; W.modal({ title: '历史图片', body: `<div class="history-picture">${picture(image)}</div>`, foot: '<button class="btn" data-act="media.history">返回记录</button>', width: 720 }); } },
    'media.restore': el => { const item = current(), record = item.imageHistory[Number(el.dataset.index)]; item.images = record.images.map(image => ({ ...image })); item.primary = record.primary || 0; W.closeOverlays(); W.render(); W.save(); },
    'media.imageMenu': el => { state().toolImage = Number(el.dataset.index); W.modal({ title: '图片工具', width: 380, body: `<button class="btn block" data-act="media.useRef">用作本次参考</button><button class="btn block" data-act="media.upload" data-index="${state().toolImage}">上传并替换此图</button><button class="btn block" data-act="media.editImage">编辑图片</button>`, foot: '<button class="btn" data-act="overlay.close">关闭</button>' }); },
    'media.useRef': () => { const item = current(); if (item.references.length < 16) item.references.push({ ...item.images[state().toolImage], name: item.name || item.no }); W.closeOverlays(); W.render(); W.save(); },
    'media.editImage': () => W.modal({ title: '编辑图片', width: 600, body: `<div class="history-picture">${picture(current().images[state().toolImage])}</div><label class="field"><span class="field-label">修改要求</span><textarea class="ta" id="imageEditPrompt" placeholder="描述要修改的区域和内容"></textarea></label>`, foot: '<button class="btn" data-act="overlay.close">取消</button><button class="btn primary" data-act="media.editApply">按要求生成</button>' }),
    'media.editApply': () => { const prompt = document.getElementById('imageEditPrompt').value.trim(); if (!prompt) return; const item = current(), index = state().toolImage; item.imageEditPrompt = prompt; state().toolImage = undefined; W.closeOverlays(); generate(item, 'image', index); },
    'media.download': el => {
      const item = current(), image = item.images[Number(el.dataset.index || 0)], anchor = document.createElement('a');
      if (!image) return;
      if (image.url) anchor.href = image.url;
      else { const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"><rect width="1024" height="1024" fill="#202020"/><text x="512" y="512" text-anchor="middle" fill="#ddd" font-size="28">${E(item.name || item.no)} · 原型示意图</text></svg>`; anchor.href = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' })); }
      anchor.download = (item.name || item.no) + (image.url ? '.png' : '-示意.svg'); anchor.click(); if (!image.url) setTimeout(() => URL.revokeObjectURL(anchor.href), 1000);
    }
  });

  let videoTimer, previewObserver;
  function mount() {
    clearInterval(videoTimer);
    previewObserver?.disconnect();
    const item = current();
    const fitImages = () => {
      document.querySelectorAll('.media-frame').forEach(frame => {
      const image = item?.images[Number(frame.dataset.index)], art = frame.querySelector('.art');
      if (!image || !art) return;
      const parts = (image.ratio || '1 / 1').split('/').map(Number), ratio = parts[0] / parts[1];
      const width = Math.min(frame.clientWidth, frame.clientHeight * ratio);
      art.style.width = width + 'px'; art.style.height = width / ratio + 'px';
      });
      const stage = document.querySelector('.video-stage'), art = stage?.querySelector('.art');
      if (art) { const width = Math.min(stage.clientWidth, stage.clientHeight * 16 / 9); art.style.width = width + 'px'; art.style.height = width * 9 / 16 + 'px'; }
    };
    fitImages();
    const preview = document.querySelector('.media-preview');
    if (preview) { previewObserver = new ResizeObserver(fitImages); previewObserver.observe(preview); }
    const voice = document.querySelector('.voice-details');
    voice?.addEventListener('toggle', () => { state().voiceOpen = voice.open; });
    const search = document.querySelector('[data-media-search]');
    search?.addEventListener('input', () => { const start = search.selectionStart; state().query = search.value; W.render(); const next = document.querySelector('[data-media-search]'); next.focus(); if (next.type !== 'search') next.setSelectionRange(start, start); });
    document.getElementById('mediaUpload')?.addEventListener('change', event => {
      const file = event.target.files[0]; if (!file || !item) return;
      if (!file.type.startsWith('image/')) { W.toast('请选择图片文件', 'warn'); return; }
      const target = state().uploadTarget, index = state().toolImage;
      const reader = new FileReader(); reader.onload = () => {
        const image = { url: reader.result, name: file.name, kind: item.kind };
        if (target === 'reference') { if (item.references.length < 16) item.references.push(image); }
        else if (index !== undefined && item.images[index]) item.images[index] = image;
        else { item.images = [image]; item.primary = 0; }
        state().toolImage = undefined; item.error = ''; item.status = 'ok'; W.closeOverlays(); W.render(); W.save();
      }; reader.readAsDataURL(file);
    });
    if (item?.playing && videoMode()) videoTimer = setInterval(() => { if (W.S.view !== 'board') { clearInterval(videoTimer); item.playing = false; return; } item.playhead = (item.playhead || 0) + 0.25; if (item.playhead >= item.dur) { item.playhead = 0; item.playing = false; W.render(); return; } const time = document.querySelector('.video-time'), slider = document.querySelector('[data-video-seek]'); if (time) time.textContent = W.tc(item.playhead) + ' / ' + W.tc(item.dur); if (slider) slider.value = item.playhead; }, 250);
  }

  document.addEventListener('input', event => {
    if (!['assets', 'board'].includes(W.S.view)) return;
    const input = event.target, key = input.dataset.mediaField, item = current();
    if (!item) return;
    if (key) { item[key] = ['count', 'dur'].includes(key) ? Number(input.value) : input.value; W.save(); }
    if (input.hasAttribute('data-video-seek')) { item.playhead = Number(input.value); const time = document.querySelector('.video-time'); if (time) time.textContent = W.tc(item.playhead) + ' / ' + W.tc(item.dur); }
    if (input.id === 'mediaPrompt' && !videoMode()) { const menu = document.getElementById('mentionMenu'); const token = /@([^\s@]*)$/.exec(input.value.slice(0, input.selectionStart)); const matches = item.references.map((ref, index) => ({ ref, index })).filter(({ ref, index }) => !token || ('图片' + (index + 1) + ref.name).includes(token[1])); menu.hidden = !token || !matches.length; menu.innerHTML = matches.map(({ ref, index }) => `<button data-act="media.mention" data-index="${index}">图片${index + 1} · ${E(ref.name)}</button>`).join(''); }
  });
  document.addEventListener('change', event => {
    if (!['assets', 'board'].includes(W.S.view)) return;
    const key = event.target.dataset.mediaField;
    if (key && ['name', 'kind', 'desc', 'act'].includes(key)) W.render();
  });
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented) return;
    if (!['assets', 'board'].includes(W.S.view)) return;
    const tab = event.target.closest('[role="tab"]');
    if (tab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); state().mode = event.key === 'Home' ? 'image' : event.key === 'End' ? 'video' : state().mode === 'image' ? 'video' : 'image'; W.render(); document.querySelector('[role="tab"][aria-selected="true"]').focus(); }
    const menu = document.getElementById('mentionMenu');
    if (menu && !menu.hidden && (event.target.id === 'mediaPrompt' || event.target.closest('#mentionMenu'))) { const buttons = [...menu.querySelectorAll('button')], index = buttons.indexOf(document.activeElement); if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); buttons[(index + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]?.focus(); } if (event.key === 'Enter' && event.target.id === 'mediaPrompt') { event.preventDefault(); buttons[0]?.click(); } if (event.key === 'Escape') { menu.hidden = true; event.preventDefault(); return; } }
    if (event.key === 'Escape' && !W.overlays().children.length && (state().railOpen || state().mobileSettings)) W.actions['media.closeSheets']();
  });
  W.media = { init, rail, picture, images, prompt, workspace, generate, mount, leave() { clearInterval(videoTimer); previewObserver?.disconnect(); const item = current(); if (item) item.playing = false; } };
})();
