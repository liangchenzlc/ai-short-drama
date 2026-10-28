/* 资产生成：缩略图列表 / 图片组 / 原有生成设置。 */
(function () {
  const W = window.WB, st = W.S.assets, M = W.media, E = W.esc, I = W.icon;
  st.filter = 'all'; st.query = ''; st.settingsOpen = true;
  st.list.forEach(item => M.init(item));
  const current = () => st.list.find(item => item.id === st.selectedId) || null;
  const visible = () => st.list.filter(item => (st.filter === 'all' || item.kind === st.filter) && (item.name + item.desc + item.label).includes(st.query));

  function fields(item) {
    return `<div class="asset-name-type"><label class="field"><span class="field-label">资产名称</span><input class="inp" data-media-field="name" value="${E(item.name)}" maxlength="120"></label><label class="field"><span class="field-label">类型</span><select class="inp" data-media-field="kind">${['角色', '场景', '道具'].map(kind => `<option${kind === item.kind ? ' selected' : ''}>${kind}</option>`).join('')}</select></label></div>
      <label class="field"><span class="field-label">资产描述</span><textarea class="ta" rows="5" data-media-field="desc" placeholder="描述人物外观、场景或道具特征">${E(item.desc)}</textarea></label>`;
  }
  function render() {
    const list = M.rail(visible(), { title: '当前资产', query: st.query, filters: [['all', '全部'], ['角色', '角色'], ['场景', '场景'], ['道具', '道具']], selected: st.selectedId, empty: st.query || st.filter !== 'all' ? '没有符合筛选的资产' : '还没有资产' });
    return M.workspace({ list, title: '资产', fields });
  }
  function add(name = '未命名资产', kind = '角色') {
    const item = { id: 'a-' + Date.now() + '-' + st.list.length, name, kind, desc: '', prompt: '', status: 'wait', hue: 28, count: 1, label: '' };
    M.init(item); st.list.push(item); st.selectedId = item.id; st.mobileSettings = innerWidth <= 980; st.railOpen = false; st.settingsOpen = true;
    return item;
  }
  function extract() {
    const scenes = W.parseScenes(W.S.script.text);
    const candidates = [...new Set(scenes.flatMap(scene => scene.cast))].map(name => ({ name, kind: '角色' })).concat([...new Set(scenes.map(scene => scene.place).filter(Boolean))].map(name => ({ name, kind: '场景' }))).filter(item => !st.list.some(asset => asset.name === item.name && asset.kind === item.kind));
    st.extractCandidates = candidates;
    W.modal({ title: '从剧本提取资产', width: 520, body: candidates.length ? `<p class="hint">选择要加入的角色和场景。</p><div class="extract-list">${candidates.map((item, index) => `<label><input type="checkbox" data-extract="${index}" checked><span>${E(item.name)}</span><span class="tag">${item.kind}</span></label>`).join('')}</div>` : '<p class="hint">没有发现新的角色或场景。已有资产保持不变。</p>', foot: `<button class="btn" data-act="overlay.close">取消</button>${candidates.length ? '<button class="btn primary" data-act="asset.extractApply">加入资产</button>' : ''}` });
  }
  Object.assign(W.actions, {
    'asset.add': () => { add(); W.render(); document.querySelector('[data-media-field="name"]')?.focus(); },
    'asset.extract': extract,
    'asset.extractApply': () => { [...document.querySelectorAll('[data-extract]:checked')].forEach(input => { const item = st.extractCandidates[Number(input.dataset.extract)]; add(item.name, item.kind); }); st.mobileSettings = false; W.closeOverlays(); W.render(); W.save(); },
    'asset.more': () => W.modal({ title: '资产操作', width: 360, body: `<button class="btn block" data-act="media.upload">${I('upload', 'i-13')}上传图片</button><button class="btn block danger" data-act="asset.delete">${I('trash', 'i-13')}删除资产</button>`, foot: '<button class="btn" data-act="overlay.close">关闭</button>' }),
    'asset.delete': () => {
      const item = current();
      if (!item) return;
      const used = ['a1', 'a4', 'a8', 'a9'].includes(item.id);
      W.modal({ title: used ? '暂不能删除' : '删除资产', width: 440, body: `<p class="dialog-copy">${used ? '当前资产已被分镜使用。请先在分镜中解除关联，再删除资产。' : `删除「${E(item.name)}」后，将无法在当前作品中继续使用。`}</p>`, foot: `<button class="btn" data-act="overlay.close">${used ? '知道了' : '取消'}</button>${used ? '' : '<button class="btn danger" data-act="asset.deleteApply">删除</button>'}` });
    },
    'asset.deleteApply': () => { st.list = st.list.filter(item => item.id !== st.selectedId); st.selectedId = st.list[0]?.id || ''; W.closeOverlays(); W.render(); W.save(); }
  });
  W.views.assets = { render, current, select: id => { st.selectedId = id; }, mount: M.mount, leave: M.leave };
})();
