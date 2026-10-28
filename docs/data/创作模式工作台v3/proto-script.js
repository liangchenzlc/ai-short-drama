/* 剧本创作 */
(function () {
  const WB = window.WB, S = WB.S, D = WB.D;
  const st = S.script;

  const HEAD = /^\s*场\s*([0-9]+(?:[-－][0-9]+)?)\s*(?:[·.、:：]\s*)?(.*)$/;
  const CAST = /^\s*人物\s*[：:]\s*(.+)$/;
  const DIALOGUE = /^(\s*)([^\s△▲（(：:]{1,10})((?:[（(][^）)]*[）)])?)([：:])([\s\S]*)$/;
  const ACTION = /^\s*[△▲]/;

  let refs = {};

  function stats() { return WB.stats(st.text, st.brief, D.targetSeconds); }

  /* ---------- 高亮层 ---------- */
  function sceneHeadHtml(line) {
    const parts = line.split('·');
    const rest = parts.slice(1).map((p, i) => `<span class="${i === 0 ? 'when' : 'when'}">·${WB.esc(p)}</span>`).join('');
    return `<div class="ln is-scene"><span class="no">${WB.esc(parts[0])}</span>${rest}</div>`;
  }

  function dialogueHtml(line) {
    const m = line.match(DIALOGUE);
    if (!m) return `<div class="ln">${WB.esc(line) || '\u200b'}</div>`;
    const [, indent, who, paren, colon, rest] = m;
    const isOs = /OS|VO|画外/i.test(paren || '');
    return `<div class="ln">${indent ? WB.esc(indent) : ''}<span class="who">${WB.esc(who)}</span>${paren ? `<span class="paren${isOs ? ' os' : ''}">${WB.esc(paren)}</span>` : ''}<span class="colon">${WB.esc(colon)}</span><span class="line">${WB.esc(rest)}</span></div>`;
  }

  function highlight(text) {
    const scenes = WB.parseScenes(text);
    const byLine = new Map();
    scenes.forEach(s => { for (let i = s.start; i < s.end; i++) byLine.set(i, s); });
    return text.split('\n').map((line, i) => {
      if (!line.trim()) return `<div class="ln is-empty">\u200b</div>`;
      const scene = byLine.get(i);
      if (scene && i === scene.start) return sceneHeadHtml(line);
      if (CAST.test(line)) {
        const idx = line.search(/[：:]/);
        return `<div class="ln is-cast"><span class="k">${WB.esc(line.slice(0, idx + 1))}</span>${WB.esc(line.slice(idx + 1))}</div>`;
      }
      if (ACTION.test(line)) return `<div class="ln is-action"><span class="mark">${WB.esc(line.slice(0, 1))}</span>${WB.esc(line.slice(1))}</div>`;
      if (DIALOGUE.test(line)) return dialogueHtml(line);
      return `<div class="ln">${WB.esc(line)}</div>`;
    }).join('');
  }

  /* ---------- 片段 ---------- */
  function sceneItems(s) {
    if (!s.scenes.length) return `<p class="hint" style="padding:8px">还没有识别到场次。按「场 01 · 日 · 内 · 地点」写一行场头，这里就会列出来。</p>`;
    return s.scenes.map((scene, i) => `
      <button class="scene-item" data-act="script.jump" data-i="${i}" aria-current="${i === st.active}">
        <span class="no">${WB.esc(scene.no)}</span>
        <span class="t"><strong>${WB.esc(scene.place || '未写地点')}</strong>
          <span>${WB.esc(scene.when)} · ${WB.esc(scene.io)}${scene.cast.length ? ` · ${scene.cast.slice(0, 3).join(' ')}` : ''}</span></span>
      </button>`).join('');
  }

  function briefPanel() {
    const names = WB.registeredNames(st.brief);
    return `<div class="sd-brief"><button class="brief-toggle" data-act="script.briefToggle" aria-expanded="${!!st.briefOpen}">${WB.icon('book', 'i-13')}故事设定${WB.icon(st.briefOpen ? 'down' : 'right', 'i-12')}</button>
      ${st.briefOpen ? `<button class="brief-line${st.brief.logline ? '' : ' is-empty'}" data-act="script.brief" data-sec="logline">${WB.esc(st.brief.logline || '一句话卖点')}</button>
      <button class="brief-row" data-act="script.brief" data-sec="outline">${WB.icon('book', 'i-13')}故事大纲
        <span class="meta">${st.brief.outline.replace(/\s/g, '').length} 字</span>${WB.icon('right', 'i-11 chev')}</button>
      <button class="brief-row" data-act="script.brief" data-sec="characters">${WB.icon('users', 'i-13')}人物小传
        <span class="meta">${names.length} 人</span>${WB.icon('right', 'i-11 chev')}</button>` : ''}
    </div>`;
  }

  function docFoot(s) {
    return `<footer class="doc-foot" id="docFoot">
      ${s.scenes.length ? `
        ${WB.icon('clock', 'i-12')}<span>估算时长 <b>${WB.fmtLoose(s.seconds)}</b></span>
        <span class="right"><span class="mono">${s.chars.toLocaleString()} 字</span><span>${s.scenes.length} 场</span></span>`
        : `<span>开始写作后显示字数与场次数。</span>`}
    </footer>`;
  }

  function aiPanel() {
    const s = stats();
    const chars = st.prompt.replace(/\s/g, '').length;
    return `<aside class="panel sd-ai" aria-label="AI 创作台">
      <header class="head"><h2>AI 创作台</h2><span class="tag">文本</span>
        <div class="actions"><button class="icon-btn" data-act="script.ai" title="收起">${WB.icon('x', 'i-13')}</button></div>
      </header>
      <div class="ai-body">
        <button class="src-card" data-act="script.novel">
          ${WB.icon('book', 'i-17')}
          <span><span class="t1">小说原文 · ${D.novel.replace(/\s/g, '').length.toLocaleString()} 字</span>
            <span class="t2">作为本次生成的输入</span></span>
          <span class="go">查看</span>
        </button>

        <div class="field">
          <div class="label" style="display:flex">创作要求<span style="margin-left:auto;color:#5d5d66" class="mono">${chars}</span></div>
          <textarea class="ta" id="aiPrompt" rows="8" placeholder="例如：按小说第 12–18 章改编，每场结尾留钩子，台词口语化。">${WB.esc(st.prompt)}</textarea>
        </div>

        <div style="display:flex;flex-wrap:wrap;gap:4px">
          ${['压缩支线', '强化钩子', '台词口语化'].map(c => `<button class="chip${st.chips.has(c) ? ' on' : ''}" data-act="script.chip" data-c="${c}">${st.chips.has(c) ? WB.icon('check', 'i-11') : ''}${c}</button>`).join('')}
          <button class="chip" data-act="script.scope">从场 ${s.scenes[st.active]?.no || '—'} 续写</button>
        </div>

        ${st.generating ? `<div class="ai-run" role="status">${WB.icon('clock', 'i-12')}<span>正在生成，完成后可对比修改</span></div>` : ''}

        <div class="ai-send">
          <button class="model" data-act="script.model">${WB.icon('cpu', 'i-11')}ScriptBird${WB.icon('down', 'i-11')}</button>
          <button class="send-btn" data-act="script.send" ${st.generating ? 'disabled' : ''} title="生成（Ctrl+Enter）">${WB.icon(st.generating ? 'clock' : 'send', 'i-13')}</button>
        </div>
        <p class="hint">生成后先预览；采用后才替换当前稿。${st.scope !== '全篇' ? ` 本次范围：${WB.esc(st.scope)}。` : ''}</p>

        <div class="lastrun">${WB.icon('check', 'i-11')}
          <span>${st.versions[0] ? `上次生成 · 成功 · ${st.versions[0].at}` : '还没有生成记录'}</span>
          <button data-act="script.history">全部记录</button>
        </div>
      </div>
    </aside>`;
  }

  /* ---------- 对比 ---------- */
  function blocks(text) {
    const scenes = WB.parseScenes(text);
    const lines = text.split('\n');
    return scenes.map(s => ({ scene: s, block: lines.slice(s.start, s.end).join('\n').replace(/\s+$/, '') }));
  }

  function lineDiff(a, b) {
    const m = a.length, n = b.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i -= 1) {
      for (let j = n - 1; j >= 0; j -= 1) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const out = [];
    let i = 0, j = 0;
    while (i < m && j < n) {
      if (a[i] === b[j]) { out.push({ t: 'same', text: a[i] }); i += 1; j += 1; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: 'del', text: a[i] }); i += 1; }
      else { out.push({ t: 'add', text: b[j] }); j += 1; }
    }
    while (i < m) { out.push({ t: 'del', text: a[i] }); i += 1; }
    while (j < n) { out.push({ t: 'add', text: b[j] }); j += 1; }
    return out;
  }

  function fold(lines) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      if (lines[i].t !== 'same') { out.push(lines[i]); i += 1; continue; }
      let j = i;
      while (j < lines.length && lines[j].t === 'same') j += 1;
      const run = lines.slice(i, j);
      if (run.length <= 2) out.push(...run);
      else { out.push(run[0]); out.push({ t: 'more', text: `… ${run.length - 1} 行未改动` }); }
      i = j;
    }
    return out;
  }

  function buildDiff(baseText, candText) {
    const base = blocks(baseText), next = blocks(candText);
    const mapN = new Map(next.map(b => [b.scene.no, b]));
    const seen = new Set();
    const entries = [];
    base.forEach(b => {
      const n = mapN.get(b.scene.no);
      seen.add(b.scene.no);
      if (!n) { entries.push({ status: 'removed', base: b }); return; }
      if (n.block === b.block) { entries.push({ status: 'same', base: b, next: n }); return; }
      const oldLines = b.block.split('\n'), newLines = n.block.split('\n');
      const lines = fold(lineDiff(oldLines.slice(1), newLines.slice(1)));
      entries.push({
        status: 'modified', base: b, next: n, lines,
        changes: lines.filter(l => l.t === 'del' || l.t === 'add').length
      });
    });
    next.forEach(n => { if (!seen.has(n.scene.no)) entries.push({ status: 'added', next: n, newBody: n.block.split('\n').slice(1) }); });
    return {
      entries,
      modified: entries.filter(e => e.status === 'modified').length,
      added: entries.filter(e => e.status === 'added').length,
      removed: entries.filter(e => e.status === 'removed').length,
      same: entries.filter(e => e.status === 'same').length
    };
  }

  function changedEntries(diff) { return diff.entries.filter(e => e.status !== 'same'); }

  function cmpRail(diff) {
    return `<aside class="panel rail-script">
      <header class="head"><h2>变更场次</h2>
        <div class="actions">
          <button class="chip${st.onlyChanges ? '' : ' on'}" data-act="script.onlyChanges" data-v="0">全部 ${diff.entries.length}</button>
          <button class="chip${st.onlyChanges ? ' on' : ''}" data-act="script.onlyChanges" data-v="1">仅变更</button>
        </div>
      </header>
      <div class="scene-list" style="padding:6px">
        ${diff.entries.filter(e => !st.onlyChanges || e.status !== 'same').map((e, i) => {
          const sc = e.next?.scene || e.base?.scene;
          const stTag = e.status === 'same' ? '<span class="tag plain">未改动</span>'
            : e.status === 'added' ? '<span class="tag ok">新增</span>' : '<span class="tag warn">已改写</span>';
          return `<button class="scene-item" data-act="script.cmpJump" data-idx="${i}" aria-current="${i === 0}">
            <span class="no">${WB.esc(sc.no)}</span>
            <span class="t"><strong>${WB.esc(sc.place || '未写地点')}</strong>
              <span>${WB.esc(sc.when)} · ${WB.esc(sc.io)}${sc.cast.length ? ' · ' + sc.cast.slice(0, 3).join(' ') : ''}</span></span>
            ${stTag}</button>`;
        }).join('')}
      </div>
      <div class="sd-rail-foot"><button class="btn block" data-act="script.abandon">${WB.icon('x', 'i-12')}放弃本次</button></div>
    </aside>`;
  }

  function cmpMain(diff) {
    let idx = -1;
    const body = diff.entries.filter(e => !st.onlyChanges || e.status !== 'same').map(e => {
      idx += 1;
      const sc = e.next?.scene || e.base?.scene;
      const head = `<div class="cmp-row" id="cmp-${idx}">
        <span class="no">${WB.esc(sc.no)}</span>
        <span class="pl">${WB.esc(sc.when)} · ${WB.esc(sc.io)} · ${WB.esc(sc.place || '')}</span>
        ${e.status === 'added' ? '<span class="tag ok">新增</span>' : e.status === 'modified' ? `<span class="tag warn">已改写</span>` : '<span class="tag plain">未改动</span>'}
      </div>`;
      if (e.status === 'same') return head;
      if (e.status === 'added') {
        return head + `<div class="diff-block">${e.newBody.map(l => `<div class="diff-line add"><span class="g">+</span><span class="txt">${WB.esc(l) || '\u200b'}</span></div>`).join('')}</div>`;
      }
      if (e.status === 'removed') {
        return head + `<div class="diff-block">${e.base.block.split('\n').slice(1).map(l => `<div class="diff-line del"><span class="g">−</span><span class="txt">${WB.esc(l) || '\u200b'}</span></div>`).join('')}</div>`;
      }
      return head + `<div class="diff-block">
        ${e.lines.map(l => l.t === 'same'
          ? `<div class="diff-line same"><span class="g"></span><span class="txt">${WB.esc(l.text) || '\u200b'}</span></div>`
          : l.t === 'more'
            ? `<div class="diff-line more"><span class="g"></span><span class="txt">${WB.esc(l.text)}</span></div>`
            : `<div class="diff-line ${l.t}"><span class="g">${l.t === 'del' ? '−' : '+'}</span><span class="txt">${WB.esc(l.text) || '\u200b'}</span></div>`).join('')}
      </div>`;
    }).join('');
    return `<main class="panel sd-doc">
      <header class="doc-head"><h2>本次生成</h2><span class="tag warn">待采用</span>
        <div class="doc-meta"><span class="sep"></span><span>${diff.modified} 改写 · ${diff.added} 新增 · ${diff.removed} 删除</span></div>
        <div class="doc-acts"><button class="btn quiet" data-act="script.abandon">${WB.icon('x', 'i-12')}放弃</button></div>
      </header>
      <div class="ed-wrap" style="background:#141414"><div class="sd-scroll" style="overflow:auto;display:block;padding:0 0 8px">${body}</div></div>
      <footer class="cmp-actions">
        <span class="hint">只替换勾选的场次，其余保持当前稿；旧稿仍在生成记录里。</span>
        <button class="btn primary" style="margin-left:auto" data-act="script.adopt">${WB.icon('check', 'i-12')}采用并继续编辑（${st.accepted.size}）</button>
      </footer>
    </main>`;
  }

function cmpPanel(diff) {
  const changed = changedEntries(diff);
  return `<aside class="panel sd-ai cmp-panel"><header class="head"><h2>选择采用的场次</h2></header><div class="ai-body"><div class="cmp-pick">${changed.map((entry, index) => {
    const scene = entry.next?.scene || entry.base?.scene;
    return `<button class="pick${st.accepted.has(index) ? ' on' : ''}" data-act="script.accept" data-i="${index}" aria-pressed="${st.accepted.has(index)}"><span class="box">${st.accepted.has(index) ? WB.icon('check', 'i-11') : ''}</span><span class="pn">场 ${WB.esc(scene.no)}</span><span class="pd">${entry.status === 'added' ? '新增' : entry.status === 'removed' ? '删除' : '已改写'}</span></button>`;
  }).join('')}</div></div></aside>`;
}

  function startPanel() {
    return `<div class="start-panel" id="startPanel">
      <div class="start-head"><h3>开始写剧本</h3><p>直接写下第一场，或选择已有来源。</p></div>
      <div class="start-grid">
        <button class="start-card" data-act="script.start" data-k="novel">${WB.icon('book', 'i-17')}
          <strong>从小说改编</strong><span>已导入 ${D.novel.replace(/\s/g, '').length} 字，先生成前 3 场</span></button>
        <button class="start-card" data-act="script.start" data-k="brief">${WB.icon('pen', 'i-17')}
          <strong>从卖点起稿</strong><span>先写一句话卖点与人物，再让模型起结构</span></button>
        <button class="start-card" data-act="script.start" data-k="import">${WB.icon('upload', 'i-17')}
          <strong>导入剧本</strong><span>粘贴或导入 txt，保留原有场次格式</span></button>
        <button class="start-card" data-act="script.start" data-k="blank">${WB.icon('type', 'i-17')}
          <strong>空白剧本</strong><span>自己写第一场，自动识别场头与对白</span></button>
      </div>
      <p class="start-foot">也可以直接写第一行：<code>场 01 · 日 · 内 · 地点</code></p>
    </div>`;
  }

  /* ---------- 页面 ---------- */
  function render() {
    const s = stats();
    if (st.mode === 'compare' && st.diff) {
      return '<div class="script-compare">' + cmpRail(st.diff) + cmpMain(st.diff) + cmpPanel(st.diff) + '</div>';
    }
    const rail = st.railOpen
      ? `<aside class="panel rail-script${st.mobileRail ? ' is-open' : ''}">
          <header class="head"><h2>剧本结构</h2>
            <div class="actions"><button class="icon-btn" data-act="script.rail" title="收起结构">${WB.icon('panel', 'i-13')}</button></div>
          </header>
          <div class="sd-scroll">${briefPanel()}<div class="scene-list-head"><h3>场次</h3><button class="icon-btn" data-act="script.addScene" title="新增场次">${WB.icon('plus', 'i-13')}</button></div><div class="scene-list" id="sceneList">${sceneItems(s)}</div></div>
          <div class="sd-rail-foot"><button class="btn quiet block" data-act="script.start" data-k="novel">${WB.icon('spark', 'i-12')}从小说拆场</button></div>
        </aside>`
      : `<div class="panel rail-stub"><button class="icon-btn" data-act="script.rail" title="展开结构">${WB.icon('panelOpen', 'i-15')}</button></div>`;

    return `${st.mobileRail ? '<button class="script-sheet-backdrop" data-act="script.mobileRail" aria-label="关闭场次目录"></button>' : ''}${st.focus ? '' : rail}
      <main class="panel sd-doc">
        <header class="doc-head"><button class="icon-btn mobile-only" data-act="script.mobileRail" title="场次目录">${WB.icon('list', 'i-15')}</button><h2>剧本正文</h2>
          <div class="doc-acts">
            <button class="btn${st.aiOpen ? ' on' : ''}" data-act="script.ai">${WB.icon('spark', 'i-12')}AI 生成</button>
            <button class="btn quiet" data-act="script.history">${WB.icon('history', 'i-13')}记录</button>
            <button class="btn quiet" data-act="script.export">${WB.icon('download', 'i-13')}导出</button>
            <button class="icon-btn" data-act="script.focus" title="专注模式">${WB.icon('expand', 'i-13')}</button>
          </div>
        </header>
        <div class="toolbar" role="toolbar" aria-label="剧本格式">
          <button class="tool-btn" data-act="script.addScene">${WB.icon('film', 'i-11')}场次</button>
          <button class="tool-btn" data-act="script.insert" data-k="action">△ 动作</button>
          <button class="tool-btn" data-act="script.insert" data-k="dialogue">对白</button>
          <button class="tool-btn" data-act="script.insert" data-k="os">OS</button>
          <button class="tool-btn" data-act="script.insert" data-k="vo">VO</button>
          <span class="spacer"></span>
        </div>
        ${st.text.trim() ? '' : startPanel()}
        <div class="ed-wrap" id="edWrap">
          <div class="ed-hl-clip" aria-hidden="true"><div class="ed-hl" id="edHl">${highlight(st.text)}</div></div>
          <textarea class="ed-input${st.text.trim() ? '' : ' is-empty'}" id="ed" spellcheck="false" aria-label="剧本正文" placeholder="场 01 · 日 · 内 · 地点&#10;人物：角色&#10;&#10;△ 写下这一场的第一个动作。">${WB.esc(st.text)}</textarea>
        </div>
        ${docFoot(s)}
      </main>
      ${st.focus || !st.aiOpen ? '' : '<button class="script-form-backdrop" data-act="script.ai" aria-label="关闭创作台"></button>' + aiPanel()}`;
  }

  /* ---------- 落笔 ---------- */
  function refresh() {
    const s = stats();
    if (refs.hl) refs.hl.innerHTML = highlight(st.text);
    if (st.text.trim()) document.getElementById('startPanel')?.remove();
    const list = document.getElementById('sceneList');
    if (list) list.innerHTML = sceneItems(s);
    const meta = document.getElementById('docMeta');
    if (meta) meta.textContent = `${s.scenes.length} 场 · ${s.chars.toLocaleString()} 字`;
    const foot = document.getElementById('docFoot');
    if (foot) foot.outerHTML = docFoot(s);
    const aiChars = document.querySelector('#aiPrompt + *');
    void aiChars;
  }

  function hideSel() {
    const bar = document.getElementById('selBar');
    if (bar) bar.remove();
  }

  function showSel() {
    const ta = document.getElementById('ed');
    if (!ta) return;
    hideSel();
    const a = ta.selectionStart, b = ta.selectionEnd;
    if (b - a < 2) return;
    const wrap = document.getElementById('edWrap');
    if (!wrap) return;
    const snippet = ta.value.slice(a, b).replace(/\s+/g, ' ').trim();
    const bar = document.createElement('div');
    bar.className = 'sel-bar';
    bar.id = 'selBar';
    bar.innerHTML = `<span class="sb" style="max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">「${WB.esc(snippet.slice(0, 22))}${snippet.length > 22 ? '…' : ''}」</span>
      ${['更狠', '更短', '更口语', '加动作'].map(k => `<button class="sb" data-act="script.rewrite" data-k="${k}">${k}</button>`).join('')}`;
    bar.addEventListener('mousedown', e => e.preventDefault());
    wrap.appendChild(bar);
    bar.style.left = `${Math.max(10, (wrap.clientWidth - bar.offsetWidth) / 2)}px`;
    bar.style.top = `${Math.max(10, wrap.clientHeight - 54)}px`;
  }

  function insertSnippet(kind) {
    const ta = document.getElementById('ed');
    if (!ta) return;
    const s = stats();
    const nextNo = String((Number(s.scenes[s.scenes.length - 1]?.no.replace(/\D/g, '')) || s.scenes.length) + 1).padStart(2, '0');
    const map = {
      scene: `场 ${nextNo} · 日 · 内 · 地点\n人物：角色\n\n△ `,
      action: '△ ',
      dialogue: '角色（情绪）：台词',
      os: '角色（OS）：',
      vo: '角色（VO）：'
    };
    const snip = map[kind] || '';
    const start = ta.selectionStart, end = ta.selectionEnd;
    const before = st.text.slice(0, start);
    const lead = kind === 'scene' && before && !before.endsWith('\n\n') ? (before.endsWith('\n') ? '\n' : '\n\n') : '';
    st.text = before + lead + snip + st.text.slice(end);
    ta.value = st.text;
    refresh(); WB.save();
    const pos = start + lead.length + snip.length;
    ta.focus();
    ta.setSelectionRange(pos, pos);
    ta.scrollTop = ta.scrollHeight;
    refs.hl.style.transform = `translateY(${-ta.scrollTop}px)`;
  }



  function briefDialog(section) {
    const secs = [
      ['logline', '一句话卖点', '被扫地出门的豪门弃妇，带着离婚协议走进前夫家的董事会。'],
      ['outline', '故事大纲', '按开端、发展、反转、结局写清主要情节点…'],
      ['characters', '人物小传', '一行一个人物：林晚：28 岁，被退婚的豪门长女，狠得下心。']
    ];
    WB.modal({
      title: '故事设定',
      width: 620,
      body: `<p class="hint">故事设定暂存本机浏览器，不包含在正文和导出中。</p>
        ${secs.map(([k, label, ph]) => `<label class="field"><span class="label">${label}</span>
          <textarea class="ta" id="brief-${k}" rows="${k === 'logline' ? 2 : k === 'outline' ? 6 : 5}" placeholder="${ph}">${WB.esc(st.brief[k])}</textarea></label>`).join('')}`,
      foot: `<button class="btn" data-act="overlay.close">取消</button>
        <button class="btn primary" data-act="script.briefSave">保存</button>`
    });
    setTimeout(() => document.getElementById(`brief-${section}`)?.focus(), 40);
  }

  function historyDialog() {
    WB.modal({
      title: '生成记录',
      width: 600,
      body: `<div style="display:grid;gap:2px">${st.versions.map((v, i) => `
        <div style="display:flex;align-items:center;gap:10px;padding:9px 8px;border-radius:7px;background:${i === 0 ? '#202020' : 'transparent'}">
          <span class="mono" style="width:24px;font-size:10.5px;color:#8b8b94">v${v.v}</span>
          <span style="min-width:0;flex:1">
            <b style="display:block;font-size:11.5px;font-weight:500;color:#dcdce2">${WB.esc(v.label)}</b>
            <span style="display:block;font-size:10px;color:#6f6f79;margin-top:1px">${WB.esc(v.note)}</span></span>
          <span class="mono" style="font-size:10px;color:#6f6f79">${v.at}</span>
          <button class="btn sm" data-act="script.diffToast">查看差异</button>
        </div>`).join('')}</div>`,
      foot: `<span class="hint" style="margin-right:auto">采用不会覆盖历史版本，旧稿始终可查。</span>
        <button class="btn" data-act="overlay.close">关闭</button>`
    });
  }

  function novelDrawer() {
    WB.drawer({
      title: '小说原文',
      body: `<div style="display:flex;gap:6px"><button class="btn sm" data-act="script.import">${WB.icon('upload', 'i-12')}导入 TXT</button>
        <span class="tag">${D.novel.replace(/\s/g, '').length.toLocaleString()} 字</span></div>
        <div style="white-space:pre-wrap;font-size:12px;line-height:1.95;color:#c9c9d1">${WB.esc(D.novel)}</div>
        <p class="hint">原型中为只读摘录；真实页面支持 UTF-8 / GB18030，替换前保留本机副本。</p>
        <input type="file" id="novelFile" accept=".txt,text/plain" hidden>`
    });
  }

  /* ---------- 动作 ---------- */
  Object.assign(WB.actions, {
    'script.rail': () => { if (innerWidth <= 760) st.mobileRail = !st.mobileRail; else st.railOpen = !st.railOpen; WB.render(); },
    'script.mobileRail': () => { st.mobileRail = !st.mobileRail; st.railOpen = true; st.aiOpen = false; WB.render(); },
    'script.briefToggle': () => { st.briefOpen = !st.briefOpen; WB.render(); },
    'script.focus': () => { st.focus = !st.focus; if (st.focus) st.aiOpen = false; WB.render(); },
    'script.ai': () => { st.aiOpen = !st.aiOpen; st.mobileRail = false; WB.render(); setTimeout(() => document.getElementById('aiPrompt')?.focus(), 30); },
    'script.jump': el => {
      const i = Number(el.dataset.i);
      st.active = i;
      const ta = document.getElementById('ed');
      const scene = stats().scenes[i];
      if (ta && scene) {
        const pos = st.text.split('\n').slice(0, scene.start).join('\n').length + (scene.start ? 1 : 0);
        const mirror = document.createElement('div');
        mirror.className = 'ed-hl';
        mirror.style.cssText = `position:absolute;visibility:hidden;width:${ta.clientWidth}px;min-height:0;height:auto;inset:0 auto auto 0`;
        mirror.textContent = st.text.slice(0, pos);
        const marker = document.createElement('span'); marker.textContent = '\u200b'; mirror.appendChild(marker);
        document.getElementById('edWrap').appendChild(mirror);
        const offset = marker.offsetTop; mirror.remove();
        ta.focus();
        ta.scrollTop = Math.max(0, offset - 24);
        ta.setSelectionRange(pos, pos);
        refs.hl.style.transform = `translateY(${-ta.scrollTop}px)`;
      }
      document.querySelectorAll('.scene-item').forEach(n => n.setAttribute('aria-current', String(Number(n.dataset.i) === i)));
      const list = document.getElementById('sceneList');
      if (list) list.innerHTML = sceneItems(stats());
    },
    'script.jumpScene': el => WB.actions['script.jump'](el),
    'script.brief': el => briefDialog(el.dataset.sec || 'logline'),
    'script.briefSave': () => {
      ['logline', 'outline', 'characters'].forEach(k => {
        const node = document.getElementById(`brief-${k}`);
        if (node) st.brief[k] = node.value;
      });
      WB.closeOverlays(); WB.save(); WB.render(); WB.toast('故事设定已保存（本机）');
    },
    'script.chip': el => {
      const c = el.dataset.c;
      st.chips.has(c) ? st.chips.delete(c) : st.chips.add(c);
      WB.render();
    },
    'script.scope': () => {
      const s = stats();
      st.scope = st.scope === '全篇' ? `从场 ${s.scenes[st.active]?.no || '01'} 起` : '全篇';
      WB.render();
    },
    'script.model': () => WB.toast('模型选择：ScriptBird / 长文本模型（演示）'),
    'script.send': () => startGeneration(),
    'script.history': () => historyDialog(),
    'script.diffToast': () => WB.toast('差异视图：本次仅原型展示（v3 与 v4 有 4 处差异）'),
    'script.export': () => {
      const blob = new Blob([st.text], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${D.project}-剧本.txt`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      WB.toast('已导出 txt（纯文本，当前稿）');
    },
    'script.novel': () => {
      novelDrawer();
      setTimeout(() => {
        const input = document.getElementById('novelFile');
        if (input) input.onchange = () => {
          const f = input.files[0];
          if (!f) return;
          const r = new FileReader();
          r.onload = () => { D.novel = String(r.result || ''); WB.closeOverlays(); WB.render(); WB.toast(`已载入 ${f.name}`); };
          r.readAsText(f);
        };
      }, 20);
    },
    'script.import': () => document.getElementById('novelFile')?.click(),
    'script.addScene': () => insertSnippet('scene'),
    'script.start': el => {
      const k = el.dataset.k;
      if (k === 'novel') {
        st.text = D.script.split('场 04')[0].replace(/\s+$/, '') + '\n';
        WB.render(); WB.save();
        WB.toast('已按小说生成前 3 场，剩下的在创作台继续');
      } else if (k === 'brief') {
        briefDialog('logline');
      } else if (k === 'import') {
        WB.modal({
          title: '导入剧本',
          width: 560,
          body: `<p class="hint">粘贴剧本正文，场头按「场 01 · 日 · 内 · 地点」写就能自动分场。</p>
            <textarea class="ta" id="importText" rows="12" placeholder="场 01 · 日 · 内 · 地点&#10;人物：林晚 顾霆琛&#10;&#10;△ 动作…&#10;林晚（不抬眼）：台词…"></textarea>`,
          foot: `<button class="btn" data-act="overlay.close">取消</button><button class="btn primary" data-act="script.importDo">导入</button>`
        });
      } else {
        insertSnippet('scene');
        WB.toast('已插入第一场，接着写人物和对白');
      }
    },
    'script.importDo': () => {
      const node = document.getElementById('importText');
      const text = (node?.value || '').trim();
      if (!text) { WB.toast('还没有内容', 'warn'); return; }
      st.text = text + '\n';
      WB.closeOverlays(); WB.render(); WB.save();
      WB.toast(`已导入 ${text.split('\n').length} 行`);
    },
    'script.insert': el => { insertSnippet(el.dataset.k); WB.closeOverlays(); },
    'script.rewrite': el => {
      const ta = document.getElementById('ed');
      const sel = ta ? ta.value.slice(ta.selectionStart, ta.selectionEnd).trim() : '';
      const k = el.dataset.k;
      const map = { '更狠': '更狠、更短，删掉解释', '更短': '压缩到 15 字以内', '更口语': '改成口语，不要书面语', '加动作': '加一个具体动作，别写心理' };
      st.prompt = `${st.prompt ? st.prompt.trim() + '\n' : ''}把「${sel.slice(0, 24)}${sel.length > 24 ? '…' : ''}」${map[k]}。`;
      st.aiOpen = true;
      hideSel();
      WB.render();
      WB.toast(`已写入创作要求：这句${k}`, 'warn');
    },
    'script.onlyChanges': el => { st.onlyChanges = el.dataset.v === '1'; WB.render(); },
    'script.cmpJump': el => {
      const node = document.getElementById(`cmp-${el.dataset.idx}`);
      if (node) node.scrollIntoView({ block: 'start', behavior: 'smooth' });
    },
    'script.accept': el => {
      const i = Number(el.dataset.i);
      st.accepted.has(i) ? st.accepted.delete(i) : st.accepted.add(i);
      WB.render();
    },
    'script.abandon': () => {
      st.mode = 'edit'; st.diff = null; st.accepted.clear();
      WB.render(); WB.toast('已放弃本次生成，当前稿未变');
    },
    'script.adopt': () => {
      const diff = st.diff;
      if (!diff) return;
      if (!st.accepted.size) { WB.toast('至少勾选一场再采用', 'warn'); return; }
      let changed = 0;
      const out = [];
      diff.entries.forEach(e => {
        if (e.status === 'same') { out.push(e.base.block); return; }
        if (e.status === 'added') {
          const idx = changedEntries(diff).findIndex(x => x === e);
          if (st.accepted.has(idx)) { out.push(e.next.block); changed += 1; }
          return;
        }
        const idx = changedEntries(diff).findIndex(x => x === e);
        if (st.accepted.has(idx)) {
          if (e.status === 'removed') { changed += 1; return; }
          out.push(e.next.block); changed += 1;
        } else out.push(e.base.block);
      });
      st.text = out.join('\n\n') + '\n';
      st.mode = 'edit'; st.diff = null; st.accepted.clear(); st.versions[0].label = '已采用';
      WB.render(); WB.save();
      WB.toast(`已采用 ${changed} 场，当前稿 v4`);
    }
  });

function startGeneration() {
    if (st.generating) return;
    if (!st.prompt.trim()) { WB.toast('先写一句创作要求', 'warn'); return; }
    st.generating = true;
    WB.render();
    setTimeout(() => {
      st.generating = false;
      let candidate = st.text;
      D.candidateOps.forEach(op => { candidate = candidate.replace(op.from, op.to); });
      candidate = candidate.replace(/\s+$/, '') + '\n\n' + D.candidateAppend + '\n';
      st.diff = buildDiff(st.text, candidate);
      st.mode = 'compare';
      st.accepted = new Set(changedEntries(st.diff).map((_, i) => i));
      st.onlyChanges = false;
      st.aiOpen = false;
      WB.render();
    }, 1500);
  }

  let docDown = null;

  /* ---------- 挂载 ---------- */
  WB.views.script = {
    render,
    mount() {
      refs = {};
      if (docDown) document.removeEventListener('mousedown', docDown);
      docDown = e => {
        if (e.target.closest('.sel-bar')) return;
        if (e.target.id === 'ed') return;
        hideSel();
      };
      document.addEventListener('mousedown', docDown);
      const ta = document.getElementById('ed');
      if (ta) {
        refs.ta = ta;
        refs.hl = document.getElementById('edHl');
        const wrap = document.getElementById('edWrap');
        refs.wrap = wrap;
        ta.scrollTop = st.scrollTop || 0;
        refs.hl.style.transform = `translateY(${-ta.scrollTop}px)`;
        ta.setSelectionRange(st.selectionStart || 0, st.selectionEnd || 0);
        ta.addEventListener('select', () => { st.selectionStart = ta.selectionStart; st.selectionEnd = ta.selectionEnd; });
        ta.addEventListener('input', () => {
          st.text = ta.value;
          ta.classList.toggle('is-empty', !st.text.trim());
          refresh();
          WB.save();
        });
        ta.addEventListener('scroll', () => {
          st.scrollTop = ta.scrollTop;
          if (refs.hl) refs.hl.style.transform = `translateY(${-ta.scrollTop}px)`;
          hideSel();
        });
        ta.addEventListener('mouseup', () => setTimeout(showSel, 10));
        ta.addEventListener('keyup', e => { if (e.shiftKey || e.key.startsWith('Arrow')) showSel(); });
        ta.addEventListener('keydown', e => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); startGeneration(); }
          if (e.key === 'Tab') {
            e.preventDefault();
            const a = ta.selectionStart;
            st.text = st.text.slice(0, a) + '  ' + st.text.slice(ta.selectionEnd);
            ta.value = st.text;
            ta.setSelectionRange(a + 2, a + 2);
            refresh();
          }
        });
        if (st.pendingFocus) {
          const p = st.pendingFocus;
          st.pendingFocus = null;
          setTimeout(() => { ta.focus(); ta.setSelectionRange(p, p); }, 20);
        }
      }
      const prompt = document.getElementById('aiPrompt');
      if (prompt) {
        prompt.addEventListener('input', () => { st.prompt = prompt.value; WB.save(); });
      }
    }
  };
})();
