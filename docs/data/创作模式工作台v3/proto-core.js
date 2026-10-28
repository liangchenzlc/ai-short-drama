/* 核心：解析、状态、路由、浮层 */
window.WB = (function () {
  const D = window.DATA;
  const icon = (name, cls = '') => `<svg class="i ${cls}"><use href="#i-${name}"/></svg>`;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* ---------- 剧本解析：与设计口径一致，纯前端即时算 ---------- */
  const HOOK = /(却|竟然|竟|原来|其实|难道|凭什么|怎么会|不可能|等着|代价|后悔|秘密|真相|除非|否则|从今天起|从现在起|三个月|说完了|认错人)/;
  const HEAD = /^\s*场\s*([0-9]+(?:[-－][0-9]+)?)\s*(?:[·.、:：]\s*)?(.*)$/;
  const CAST = /^\s*人物\s*[：:]\s*(.+)$/;
  const DIALOGUE = /^\s*([^\s△▲（(：:]{1,10})(?:[（(]([^）)]*)[）)])?\s*[：:]\s*(.*)$/;
  const ACTION = /^\s*[△▲]/;

  function parseScenes(text) {
    const lines = text.split('\n');
    const scenes = [];
    let cur = null;
    lines.forEach((line, i) => {
      const head = line.match(HEAD);
      if (head) {
        const parts = head[2].split(/\s*·\s*/).filter(Boolean);
        const when = parts[0] || '';
        const where = parts[1] || '';
        const place = parts.slice(2).join(' · ') || '';
        cur = {
          no: head[1].replace('－', '-'), when, io: where, place, placeText: place,
          start: i, end: lines.length, cast: [], hook: false, lines: []
        };
        scenes.push(cur);
        if (scenes.length > 1) scenes[scenes.length - 2].end = i;
        return;
      }
      if (!cur) return;
      cur.lines.push(line);
      const cast = line.match(CAST);
      if (cast) {
        cur.cast = cast[1].split(/[、,，/;；\s]+/).map(s => s.trim()).filter(s => s && !/^\d+$/.test(s));
      }
      if (HOOK.test(line)) cur.hook = true;
    });
    scenes.forEach(s => {
      const body = s.lines.filter(l => l.trim() && !CAST.test(l));
      if (body.length && /[？?]\s*$/.test(body[body.length - 1]) && body[body.length - 1].length > 6) s.hook = true;
    });
    return scenes;
  }

  function countChars(text) { return (text.replace(/\s/g, '') || '').length; }

  function registeredNames(brief) {
    return (brief.characters || '').split('\n').map(l => l.split(/[：:]/)[0].trim()).filter(Boolean);
  }

  function stats(text, brief, target) {
    const scenes = parseScenes(text);
    const chars = countChars(text);
    const seconds = Math.max(1, Math.round(chars / 4.6));
    const hooks = scenes.filter(s => s.hook).length;
    const names = registeredNames(brief);
    const issues = [];
    scenes.forEach((s, i) => {
      s.cast.forEach(name => { if (names.length && !names.includes(name)) issues.push({ sceneIndex: i, name }); });
    });
    return { scenes, chars, seconds, hooks, issues, target: target || D.targetSeconds, cast: [...new Set(scenes.flatMap(s => s.cast))] };
  }

  function fmt(sec) {
    const s = Math.max(0, Math.round(sec));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }
  function fmtLoose(sec) {
    const s = Math.max(0, Math.round(sec));
    return `${Math.floor(s / 60)}'${String(s % 60).padStart(2, '0')}"`;
  }
  function tc(sec) {
    const s = Math.max(0, sec);
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  }

  /* ---------- 占位画面 ---------- */
  function art(kind, hue, opts = {}) {
    const k = kind === '角色' || kind === 'char' ? 'char' : (kind === '场景' || kind === 'scene') ? 'scene' : 'prop';
    const fit = opts.wide ? 'wide' : 'tall';
    const inner = k === 'char'
      ? `<svg viewBox="0 0 100 170" preserveAspectRatio="xMidYMax meet"><circle cx="50" cy="27" r="17" fill="rgba(255,255,255,.17)"/><path d="M50 45c13 0 21 5 25 14l7 19v92H18V78l7-19c4-9 12-14 25-14z"/></svg>`
      : k === 'scene'
        ? `<svg viewBox="0 0 160 90"><rect x="94" y="12" width="50" height="44" fill="none" stroke="rgba(255,255,255,.22)" stroke-width="1.2"/><path d="M119 12v44M94 34h50" stroke="rgba(255,255,255,.16)" stroke-width="1"/><rect x="12" y="56" width="68" height="11" rx="3"/><rect x="16" y="45" width="60" height="10" rx="3" opacity=".55"/><rect x="116" y="66" width="28" height="4" rx="2"/><rect x="0" y="80" width="160" height="12" opacity=".22"/></svg>`
        : opts.prop === 'ring'
          ? `<svg viewBox="0 0 100 100"><circle cx="50" cy="52" r="17" fill="none" stroke="rgba(255,255,255,.34)" stroke-width="4"/><circle cx="50" cy="52" r="17" fill="none" stroke="rgba(255,255,255,.1)" stroke-width="1"/></svg>`
          : `<svg viewBox="0 0 100 100"><rect x="30" y="16" width="40" height="56" rx="2"/><path d="M37 32h26M37 40h26M37 48h16" stroke="#141519" stroke-width="1.4" opacity=".55"/><rect x="52" y="58" width="12" height="6" rx="1" fill="rgba(250,152,152,.55)"/></svg>`;
    return `<div class="art ${k} ${fit}" style="--hue:${hue || 0}deg">${inner}</div>`;
  }

  /* ---------- 状态 ---------- */
  const S = {
    view: 'script',
    save: '已保存 14:52',
    script: {
      text: D.script,
      brief: { ...D.brief },
      aiOpen: false,
      railOpen: true,
      focus: false,
      active: 0,
      prompt: '按小说第 12–18 章改编，保留主线；每场结尾留钩子；台词口语化，单句不超过 20 字。',
      chips: new Set(['强化钩子']),
      scope: '全篇',
      mode: 'edit',
      candidate: null,
      accepted: new Set(),
      onlyChanges: true,
      generating: false,
      progress: 0,
      versions: [
        { v: 4, label: '本次生成', at: '14:33', note: '3 处改写 · 1 场新增' },
        { v: 3, label: '手动编辑', at: '14:32', note: '微调场 07 诊断台词' },
        { v: 2, label: '由小说生成', at: '14:05', note: '9 场 · 1,286 字' },
        { v: 1, label: '导入大纲', at: '13:40', note: '初始稿' }
      ]
    },
    assets: {
      list: D.assets.map(a => ({ ...a })),
      selectedId: 'a1',
      kind: 'all',
      query: '',
      count: 2,
      multi: false,
      picked: new Set(),
      generating: false,
      progress: 0
    },
    board: {
      shots: D.shots.map(s => ({ ...s })),
      view: 'grid',
      filter: 'all',
      selected: 0,
      queue: [],
      running: false,
      paused: false
    },
    assembly: {
      clips: D.clips.map(c => ({ ...c })),
      playhead: 12,
      playing: false,
      selected: null,
      unsynced: ['S05', 'S12'],
      exporting: false
    }
  };

  /* ---------- 浮层 ---------- */
  const overlays = () => document.getElementById('overlays');
  let overlayTrigger = null;
  function closeOverlays() {
    overlays().innerHTML = '';
    if (overlayTrigger?.isConnected) overlayTrigger.focus({ preventScroll: true });
  }
  function modal({ title, body, foot, width }) {
    overlays().innerHTML = `<div class="overlay" data-close="1"><div class="dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}" style="${width ? `width:${width}px` : ''}">
      <div class="dialog-head"><h3>${esc(title)}</h3><button class="icon-btn" data-act="overlay.close" aria-label="关闭">${icon('x', 'i-13')}</button></div>
      <div class="dialog-body">${body}</div>
      ${foot ? `<div class="dialog-foot">${foot}</div>` : ''}
    </div></div>`;
    overlays().querySelector('button, input, select, textarea')?.focus();
    return overlays();
  }
  function drawer({ title, body }) {
    overlays().innerHTML = `<div class="overlay" data-close="1" style="background:rgb(4 4 6 / 40%)"></div>
      <aside class="drawer" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header class="drawer-head"><h3>${esc(title)}</h3><button class="icon-btn" data-act="overlay.close" aria-label="关闭">${icon('x', 'i-13')}</button></header>
      <div class="drawer-body">${body}</div></aside>`;
    overlays().querySelector('button')?.focus();
  }
  let toastTimer = [];
  function toast(text, kind = '') {
    const host = document.querySelector('.toasts');
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `${icon(kind === 'warn' ? 'alert' : 'check', 'i-13')}<span>${esc(text)}</span>`;
    host.appendChild(el);
    const t = setTimeout(() => el.remove(), 2600);
    toastTimer.push(t);
  }
  function save() {
    S.save = `已保存 ${new Date().toTimeString().slice(0, 5)}`;
    const el = document.querySelector('.save');
    if (el) { el.classList.add('busy'); el.lastChild.textContent = '保存中…'; }
    setTimeout(() => {
      const node = document.querySelector('.save');
      if (node) { node.classList.remove('busy'); node.lastChild.textContent = S.save; }
    }, 420);
  }

  /* ---------- 渲染 ---------- */
  function topbar() {
    const steps = [['script', '剧本创作'], ['assets', '资产生成'], ['board', '分镜制作'], ['assembly', '成片合成']];
    const idx = steps.findIndex(s => s[0] === S.view);
    document.getElementById('topbar').innerHTML = `
      <div class="tb-left">
        <button class="icon-btn" data-act="overlay.close" title="返回项目">${icon('back', 'i-17')}</button>
        <span class="brand">${icon('film', 'i-13')}</span>
        <span class="pname">${esc(D.project)}</span>
        <span class="mode">创作模式</span>
      </div>
      <nav class="steps">${steps.map(([id, label], i) => `${i ? '<span class="step-link"></span>' : ''}
        <button class="step ${i === idx ? 'on' : ''}" ${i === idx ? 'aria-current="step"' : ''} data-act="go" data-view="${id}">
          <span class="step-no">${String(i + 1).padStart(2, '0')}</span>${label}</button>`).join('')}</nav>
      <div class="tb-right">
        <button class="ghost" data-act="notes">${icon('info', 'i-13')}设计说明</button>
        <span class="save"><i></i>${esc(S.save)}</span>
        <button class="ghost" data-act="assistant">${icon('spark', 'i-13')}AI 创作助手</button>
      </div>`;
  }

  function render() {
    topbar();
    const host = document.getElementById('view');
    host.className = 'view view-' + S.view;
    host.innerHTML = WB.views[S.view].render();
    WB.views[S.view].mount && WB.views[S.view].mount();
  }
  function go(view) {
    WB.views[S.view]?.leave?.();
    S.view = view;
    closeOverlays();
    render();
  }

  /* ---------- 说明 ---------- */
  const NOTES = {
  "script": {
    "title": "剧本创作 · 本版说明",
    "body": "<h4>场次目录与连续正文</h4><ul><li>参考 Laper 公开演示的剧本文档组织，保留本项目中文格式与编辑方式。</li><li>故事设定折叠，场次导航优先；格式工具、选文改写、记录、导出和候选对比继续保留。</li><li>移除固定目标时长、常驻钩子计数和重复统计，时长只作估算。</li></ul><h4>原型边界</h4><ul><li>生成只演示候选与采用交互，不请求模型；设定仅在本次页面内保存。</li><li>本版只修改四流程，AI 创作助手保持原入口和行为。</li></ul>"
  },
  "assets": {
    "title": "资产生成 · 本版说明",
    "body": "<h4>沿用熟悉的制作路径</h4><ul><li>素材生成统一改称资产生成，仍是一张卡对应一个角色、场景或道具。</li><li>恢复缩略图、参考图片、图片组、主图、生成选项、图片工具和记录。</li><li>删除常驻角度区、对照区和最近生成条；不新增资产层级。</li></ul><h4>可评审的交互</h4><ul><li>搜索、分类、新增、提取、上传、参考选择与 @ 引用、1/2/4 张出图、主图与记录。</li><li>生成及内置画面为本地示意；模型名称是演示选项，不表示已连接供应商。刷新会恢复样例。</li></ul>"
  },
  "board": {
    "title": "分镜制作 · 本版说明",
    "body": "<h4>默认逐镜制作</h4><ul><li>一套镜头列表、当前画面与生成区，完整区分分镜图和分镜视频。</li><li>生图保留参考、主图、模型与图片工具；视频使用当前主图，保留运动要求与折叠的对白音色。</li><li>批量生成只在更多中按需打开，不再常驻队列、统计和网格／表格切换。</li></ul><h4>原型边界</h4><ul><li>生成、历史和播放器进度为本地交互演示，不产生真实视频或声音。</li><li>图组直接保存、视频自动成为当前结果，文本候选继续明确采用。</li></ul>"
  },
  "assembly": {
    "title": "成片合成 · 本版说明",
    "body": "<h4>保留现有编辑器能力</h4><ul><li>画面、原声、对白、字幕、配乐、音效六轨；不再常驻导出检查清单。</li><li>同步、历史、专注与导出集中在顶部，片段属性随选择出现。</li><li>可选择、拖动、分割、调整时间与音量，锁定或静音轨道，并撤销／重做。</li></ul><h4>原型边界</h4><ul><li>时间轴、同步和导出是本地流程示意，未连接媒体处理；导出不生成视频文件。</li><li>同步演示保留当前剪辑位置与时长，不整条重排。</li></ul>"
  }
};

  /* ---------- 事件 ---------- */
  const actions = {
    'go': el => go(el.dataset.view),
    'overlay.close': () => closeOverlays(),
    'notes': () => {
      const note = NOTES[S.view];
      drawer({ title: note.title, body: note.body + `<div class="divider" style="margin-top:8px"></div>
        <button class="btn block" data-act="reset">${icon('refresh', 'i-12')}重置演示数据</button>` });
    },
    'assistant': () => toast('AI 创作助手：详情见上一轮方案，原型里只做入口占位'),
    'reset': () => { location.reload(); },
    'toast': el => toast(el.dataset.text || '演示动作', el.dataset.kind || '')
  };

  document.addEventListener('click', e => {
    const target = e.target.closest('[data-act]');
    if (target) {
      if (!target.closest('#overlays')) overlayTrigger = target;
      const fn = actions[target.dataset.act];
      if (fn) { e.preventDefault(); fn(target, e); }
    } else if (e.target.matches('[data-close="1"]')) {
      closeOverlays();
      return;
    }
    // 点空白处收起浮出菜单（不影响对话框/抽屉）
    const host = overlays();
    if (host.querySelector('.menu') && !host.querySelector('.overlay') && !e.target.closest('.menu')) {
      if (!target || !/Menu$/.test(target.dataset.act || '')) host.innerHTML = '';
    }
  });
  document.addEventListener('keydown', e => {
    const dialog = overlays().querySelector('[role="dialog"]');
    if (e.key === 'Escape' && overlays().children.length) { e.preventDefault(); closeOverlays(); return; }
    if (e.key === 'Tab' && dialog) {
      const nodes = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')].filter(node => node.getClientRects().length);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  });

  return {
    D, S, icon, esc, art, parseScenes, stats, registeredNames, fmt, fmtLoose, tc,
    render, go, modal, drawer, toast, save, closeOverlays, overlays, actions, NOTES,
    views: {}
  };
})();
