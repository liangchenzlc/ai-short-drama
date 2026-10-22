'use strict';

const $ = (id) => document.getElementById(id);
const icon = (name, small = false) => `<svg class="icon${small ? ' small' : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const btn = (label, action, extra = '', attrs = '') => `<button class="button ${extra}" type="button" data-action="${action}" ${attrs}>${label}</button>`;
const kinds = { character: '角色', scene: '场景', prop: '道具' };
const initialScript = `<h3 id="scene-1">场次 01 · 雨夜街巷 <small>外景 / 夜 / 林岚</small></h3>
<p class="action">深夜。雨刚停，轨道旁的积水反射着蓝紫色灯光。最后一班电车驶离街口，远处传来闷响。</p>
<p class="action">林岚独自走进空旷的街巷。她停在一盏闪烁的街灯下，从大衣内袋取出一张旧照片。</p>
<p class="dialogue"><strong>林岚：</strong>“照片里的地方，就是这里。”</p>
<p class="action">照片背面写着一句话：别在午夜之后回头。</p>
<h3 id="scene-2">场次 02 · 城市街口 <small>外景 / 夜 / 林岚、周野</small></h3>
<p class="action">一辆深色轿车在街口停下。周野推门下车，手里握着一台旧式相机。两人隔着车流对视。</p>
<p class="dialogue"><strong>周野：</strong>“你也收到那封信了？”</p>
<p class="dialogue"><strong>林岚：</strong>“你怎么知道？”</p>
<p class="action">周野没有回答。他把相机递给林岚。取景器里，是同一条街巷，画面中央多了一个背对镜头的人。</p>
<h3 id="scene-3">场次 03 · 回到街巷 <small>外景 / 夜 / 林岚、周野</small></h3>
<p class="action">路灯依次熄灭。林岚慢慢抬头，玻璃橱窗里映出两人的身影，以及站在他们身后的第三个人。</p>
<p class="dialogue"><strong>林岚：</strong>“别回头。”</p>
<p class="action">画面切黑。一声快门响起。</p>`;

const initialNovel = `第一章：雨夜来信\n\n最后一班电车驶过街口时，林岚刚好走到那盏坏掉的街灯下面。雨停了，路面还湿着，橱窗上的蓝色招牌把积水照得像一片浅海。\n\n三天前，她收到一封没有寄件人的信。信里只有一张旧照片，背面写着一句话：别在午夜之后回头。照片里的街巷她从没去过，却觉得每个细节都熟悉。\n\n她从大衣里取出照片。墙上的裂痕，轨道的弯折，街角被遮住一半的店招，一切都对上了。\n\n“照片里的地方，就是这里。”她轻声说。\n\n一辆深色轿车在街口停下。车门打开，周野走了下来。他手里的旧式相机在灯光下泛着金属的暗光。\n\n“你也收到那封信了？”\n\n林岚没有回答。她看着周野，忽然意识到，照片背后的字迹和他留在借阅卡上的签名一模一样。\n\n“你怎么知道？”\n\n周野把相机递过来。林岚凑近取景器，眼前是同一条街巷，只是画面中央多了一个人，背对着她，安静地站在街灯下。\n\n路灯开始依次熄灭。她抬起头，橱窗里映出她和周野的身影，以及他们身后的第三个人。\n\n“别回头。”\n\n黑暗里，一声快门响起。`;

function asset(id, name, kind, image, adopted, description) {
  return { id, name, kind, image, adopted, description, version: 1, latest: null, showLatest: false,
    records: [], refs: [], params: { model: '个人图片模型 A', prompt: description, aspect: kind === 'character' ? '3:4' : '16:9', resolution: '2K', count: '1 张', negative: '', seed: '' } };
}

const assets = [
  asset('a1', '雨夜街巷', 'scene', 'street', true, '空旷的城市街巷，湿润路面反射蓝紫色灯光，旧电车轨道延伸至远处。写实电影质感，保持安静、克制的悬疑氛围。'),
  asset('a2', '城市街口', 'scene', 'city', true, '高楼之间的城市街口，傍晚车流与湿润路面，纵深构图。作为人物碰面的场景，延续冷色调与写实电影质感。'),
  asset('a3', '熄灯后的街巷', 'scene', 'street', false, '雨夜街巷的熄灯版本，橱窗映出三个人影，保留轨道、街灯和建筑位置，让镜头前后保持空间连续。'),
  asset('a4', '林岚', 'character', 'woman', true, '女主角，独自调查匿名来信。沉着而敏感，角色造型以深色大衣和简洁轮廓为主；此处照片仅为人物示意。'),
  asset('a5', '周野', 'character', 'man', true, '男主角，旧相机的持有者。神情克制，话语简短，角色造型延续现代都市悬疑风格；照片仅为人物示意。'),
  asset('a6', '神秘来客', 'character', 'man', false, '只以远处背影出现的人物，身份不明。深色外衣，避免展示正脸，轮廓需要能在橱窗反射里辨认。'),
  asset('a7', '旧式相机', 'prop', 'camera', true, '故事的关键道具。一台黑色相机，金属边缘与使用痕迹清晰，质感真实，适合特写；图库图片仅为道具示意。'),
  asset('a8', '匿名来信', 'prop', null, false, '没有寄件人的纸质信封，内有一张旧照片。信封略有折痕，照片背面保留手写字的位置。'),
];

const shotDescriptions = [
  ['街巷空镜', 'street', '全景', '固定', 5, '雨后的街巷空无一人，蓝紫色霓虹映在湿润地面，电车轨道指向街口。用环境先建立悬念。'],
  ['林岚走入街巷', 'street', '中景', '缓慢跟拍', 4, '林岚从画面右侧进入，沿轨道向街灯走去。保持人物与街巷的空间关系。'],
  ['照片背面的字', 'camera', '特写', '轻微推进', 3, '林岚手持旧照片，翻到背面，露出“别在午夜之后回头”的字迹。'],
  ['周野在街口出现', 'city', '中景', '横移', 5, '轿车在街口停下，周野走出车门，两人隔着车流对视。'],
  ['递出旧式相机', 'camera', '近景', '固定', 4, '周野把相机递给林岚，她犹豫片刻后接过。两人的手和相机构成画面主体。'],
  ['取景器里的来客', 'street', '主观镜头', '缓慢推进', 6, '从取景器看向街灯，一个陌生人背对镜头站在远处，环境与前镜保持一致。'],
  ['橱窗中的第三人', 'street', '近景', '固定', 4, '林岚抬头，橱窗映出她、周野与身后第三人的轮廓。真实人物不回头。'],
  ['快门声后切黑', 'street', '特写', '固定', 5, '林岚低声说“别回头”，随后街灯熄灭，画面切黑，以快门声结束本集。'],
];
const shots = shotDescriptions.map(([name, image, size, movement, duration, description], i) => ({
  id: `s${i + 1}`, name, image, adopted: i < 4, position: i + 1, description, size, movement, duration,
  version: 1, latest: null, latestVideo: null, showLatest: false, showLatestVideo: false, video: false, records: [],
  refs: i === 0 ? ['a1'] : ['a1', 'a4'],
  params: { model: '个人图片模型 A', videoModel: '个人视频模型 A', prompt: description, videoPrompt: description, aspect: '16:9', resolution: '2K', layout: '单图', duration: String(duration), count: '1 张', negative: '', seed: '' },
}));

const stageInfo = {
  source: ['小说改编', '下一步：素材生成'],
  assets: ['素材生成', '下一步：分镜制作'],
  storyboard: ['分镜制作', '查看成片阶段'],
};
const state = {
  stage: ['source', 'assets', 'storyboard'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'assets',
  assetId: 'a1', shotId: 's1', category: 'scene', query: '', settingsClosed: window.innerWidth <= 700,
  autoCollapsed: window.innerWidth <= 700,
  settingsTab: 'settings', assistant: false, assistantDraft: '', messages: [],
  mediaTab: 'image', demo: 'normal', zoom: 100, confirmed: true, scriptHtml: initialScript, novel: initialNovel,
  source: { params: { model: '个人文本模型 A', aspect: '16:9', style: '写实悬疑', instructions: '保留匿名来信的悬念，将心理描写转化为动作与对白。结尾停在快门声，避免解释第三人的身份。' }, latest: null, showLatest: false, records: [] },
  busy: new Set(), adoption: null, pickedReferences: [], referencePurpose: 'refs', toastTimer: null, saveTimer: null,
};

function item() { return state.stage === 'assets' ? assets.find((a) => a.id === state.assetId) : shots.find((s) => s.id === state.shotId); }
function currentKey() { return state.stage === 'source' ? 'source' : `${state.stage}:${item()?.id}`; }
function badge(label, kind = '', check = false) { return `<span class="badge ${kind}">${check ? icon('check', true) : ''}${esc(label)}</span>`; }
function toast(message) { clearTimeout(state.toastTimer); $('toast').textContent = message; $('toast').hidden = false; state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3800); }
function openModal(title, subtitle, body, footer = '') {
  if ($('modal').open) $('modal').close();
  $('modalTitle').textContent = title; $('modalSubtitle').textContent = subtitle;
  $('modalBody').innerHTML = body;
  $('modalFooter').innerHTML = footer || btn('关闭', 'close-modal');
  $('modal').showModal();
  $('modalFooter').querySelector('button')?.focus();
}
function field(label, name, value, options, help = '') {
  const input = options ? `<select data-param="${name}" aria-label="${label}">${options.map((v) => `<option${v === value ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select>` : `<textarea data-param="${name}" aria-label="${label}" rows="4">${esc(value)}</textarea>`;
  return `<label class="field"><span>${label}</span>${input}${help ? `<p class="field-help">${help}</p>` : ''}</label>`;
}
function textField(label, name, value, type = 'text') { return `<label class="field"><span>${label}</span><input type="${type}" data-param="${name}" aria-label="${label}" value="${esc(value)}"></label>`; }


function settingsHost() { return state.stage === 'source' ? $('adaptationPanel') : $('generationSettings'); }
function modalPanel() {
  if (state.assistant && window.innerWidth < 1000) return $('inspector');
  return !state.settingsClosed && window.innerWidth <= 700 ? settingsHost() : null;
}
function syncPanels() {
  const area = $('workArea');
  area.classList.toggle('source-stage', state.stage === 'source');
  area.classList.toggle('settings-hidden', state.settingsClosed);
  area.classList.toggle('assistant-open', state.assistant);
  $('adaptationPanel').hidden = state.stage !== 'source' || state.settingsClosed;
  if ($('generationSettings')) $('generationSettings').hidden = state.settingsClosed;
  document.querySelector('.media-workspace')?.classList.toggle('settings-hidden', state.settingsClosed);
  $('inspector').hidden = !state.assistant;
  $('mainPanel').classList.toggle('compact-preview', $('mainPanel').clientWidth < 680);
  $('assistantToggle').setAttribute('aria-expanded', String(state.assistant));
  const panel = modalPanel();
  $('panelScrim').hidden = !panel;
  [$('adaptationPanel'), $('generationSettings'), $('inspector')].filter(Boolean).forEach((el) => {
    if (el === panel) { el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); }
    else { el.removeAttribute('role'); el.removeAttribute('aria-modal'); }
  });
}
function mediaWorkspace(content) {
  return `<div class="media-workspace"><div class="media-preview">${content}</div><aside class="generation-settings settings-panel" id="generationSettings" aria-label="生成设置与对象信息"></aside></div>`;
}

function render() {
  const info = stageInfo[state.stage];
  $('stageTitle').textContent = info[0]; $('continueButton').innerHTML = `${info[1]}${icon('next', true)}`;
  $('continueButton').dataset.action = 'continue';
  document.querySelectorAll('[data-stage]').forEach((el) => { if (el.dataset.stage === state.stage) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current'); });
  const toggle = btn(`${icon('panel', true)}${state.settingsClosed ? '展开设置' : '收起设置'}`, 'toggle-inspector', '', `aria-expanded="${!state.settingsClosed}" aria-controls="${state.stage === 'source' ? 'adaptationPanel' : 'generationSettings'}"`);
  if (state.stage === 'source') $('stageActions').innerHTML = btn(`${icon('book', true)}查看原文`, 'source') + btn(`${icon('history', true)}生成记录`, 'history') + '<span class="toolbar-divider"></span>' + toggle;
  else if (state.stage === 'assets') $('stageActions').innerHTML = btn(`${icon('spark', true)}从剧本提取`, 'extract') + btn(`${icon('plus', true)}新增素材`, 'add') + '<span class="toolbar-divider"></span>' + toggle;
  else $('stageActions').innerHTML = btn(`${icon('spark', true)}提取分镜`, 'extract') + btn(`${icon('plus', true)}新增镜头`, 'add') + '<span class="toolbar-divider"></span>' + toggle;
  renderList(); renderMain(); renderInspector();
  if (state.assistant) renderAssistant(); else $('inspector').innerHTML = '';
  syncPanels(); renderConflict();
}

function renderList() {
  if (state.stage === 'source') { $('objectPanel').innerHTML = ''; return; }
  const isAsset = state.stage === 'assets';
  const collection = isAsset ? assets : shots;
  let rows = collection.filter((a) => (!isAsset || state.category === 'all' || a.kind === state.category) && `${a.name}${a.description}`.includes(state.query));
  const selectedId = isAsset ? state.assetId : state.shotId;
  const filters = isAsset ? `<div class="category-tabs" aria-label="素材分类">${[['all', '全部'], ['character', '角色'], ['scene', '场景'], ['prop', '道具']].map(([key, label]) => `<button type="button" data-action="category" data-value="${key}" aria-pressed="${state.category === key}">${label} ${key === 'all' ? assets.length : assets.filter((a) => a.kind === key).length}</button>`).join('')}</div>` : `<div class="category-tabs" aria-label="镜头筛选">${[['all', '全部镜头'], ['todo', '待制作']].map(([key, label]) => `<button type="button" data-action="shot-filter" data-value="${key}" aria-pressed="${(state.shotFilter ?? 'all') === key}">${label}</button>`).join('')}</div>`;
  if (!isAsset && state.shotFilter === 'todo') rows = rows.filter((s) => !s.adopted);
  let content = rows.map((a) => `<button class="object-card" type="button" data-action="select" data-id="${a.id}" aria-pressed="${a.id === selectedId}" aria-label="${isAsset ? a.name : `分镜 ${String(a.position).padStart(2, '0')} ${a.name}`}"><div class="card-image">${a.adopted && a.image ? `<img src="assets/${a.image}.jpg" alt="${esc(a.name)}示意图" loading="lazy">` : `<div class="card-empty">${icon('image')}</div>`}${!isAsset ? `<span class="card-order">${String(a.position).padStart(2, '0')}</span><span class="card-duration">${a.duration} 秒</span>` : ''}${a.id === selectedId ? `<span class="card-selected">${icon('check')}</span>` : ''}</div><div class="card-copy"><strong>${!isAsset ? `${String(a.position).padStart(2, '0')} · ` : ''}${esc(a.name)}</strong><div class="card-meta">${isAsset ? `<span>${kinds[a.kind]}</span>` : `<span>${esc(a.size)}</span>`}${badge(a.adopted ? (isAsset ? '已采用' : '图已采用') : '待制作', a.adopted ? 'success' : '')}${!isAsset ? `<span>${a.video ? '视频已采用' : '视频未制作'}</span>` : ''}</div></div></button>`).join('');
  if (state.demo === 'loading') content = `<div role="status" aria-label="正在加载列表">${'<div class="skeleton-card"><div></div><span></span><span></span></div>'.repeat(3)}</div>`;
  else if (state.demo === 'empty') content = `<div class="state-message">${icon('image')}<strong>还没有${isAsset ? '素材' : '镜头'}</strong><p>从已确认剧本提取，或手动新增。</p>${btn(isAsset ? '新增第一个素材' : '新增第一个镜头', 'add', 'small')}</div>`;
  else if (state.demo === 'error') content = `<div class="state-message">${icon('alert')}<strong>列表加载失败</strong><p>网络连接中断，请重新加载。</p>${btn('重新加载', 'recover', 'small')}</div>`;
  else if (!rows.length) content = '<div class="state-message"><strong>没有匹配的条目</strong><p>试试其他关键词或分类。</p></div>';
  $('objectPanel').innerHTML = `<div class="object-header"><div class="object-title"><h2>${isAsset ? '本集素材' : '本集镜头'}<span class="count">${collection.length}</span></h2>${isAsset ? btn('导入素材', 'library', 'small text-button') : `<span class="view-label">${shots.reduce((n, s) => n + s.duration, 0)} 秒</span>`}</div><div class="search-field">${icon('search')}<label class="sr-only" for="objectSearch">${isAsset ? '搜索素材' : '搜索镜头'}</label><input id="objectSearch" type="search" placeholder="${isAsset ? '搜索名称或描述' : '搜索镜头内容'}" value="${esc(state.query)}"></div>${filters}</div><div class="list-scroll" id="objectList">${content}</div>`;
}

function renderMain() {
  let placeholder = '';
  if (state.demo === 'loading') placeholder = '<div class="state-message" role="status"><span class="spinner"></span><strong>正在加载作品</strong></div>';
  else if (state.demo === 'error') placeholder = `<div class="state-message" role="alert">${icon('alert')}<strong>作品加载失败</strong><p>当前输入已保留。</p>${btn('重新加载', 'recover')}</div>`;
  else if (state.demo === 'empty') placeholder = `<div class="state-message">${icon(state.stage === 'source' ? 'book' : 'image')}<strong>${state.stage === 'source' ? '从一个故事开始' : '选择一个对象开始制作'}</strong>${btn(state.stage === 'source' ? '导入小说原文' : '新增对象', state.stage === 'source' ? 'source' : 'add')}</div>`;
  if (placeholder) { $('mainPanel').innerHTML = state.stage === 'source' ? placeholder : mediaWorkspace(placeholder); renderInspector(); syncPanels(); return; }
  if (state.stage === 'source') { renderScript(); return; }
  const a = item();
  const isAsset = state.stage === 'assets';
  const latest = isAsset || state.mediaTab !== 'video' ? a.showLatest && a.latest : a.showLatestVideo && a.latestVideo;
  const adopted = state.mediaTab === 'video' && !isAsset ? a.video : a.adopted;
  const hasImage = latest || adopted;
  const imageName = latest?.image ?? a.image;
  const headingBadge = latest ? badge('新结果 · 未采用', 'warning') : badge(adopted ? '当前采用' : '尚未制作', adopted ? 'success' : '', adopted);
  const navigation = !isAsset ? `<button class="icon-button" type="button" data-action="previous-object" aria-label="上一个镜头" ${a.position === 1 ? 'disabled' : ''}>${icon('back')}</button><button class="icon-button" type="button" data-action="next-object" aria-label="下一个镜头" ${a.position === shots.length ? 'disabled' : ''}>${icon('next')}</button>` : '';
  const tabs = isAsset ? `<div class="tabs"><button type="button" data-action="current" aria-pressed="${!latest}">当前采用</button>${a.latest ? `<button type="button" data-action="latest" aria-pressed="${!!latest}">最新生成</button>` : ''}</div>` : `<div class="tabs">${[['info', '镜头信息'], ['image', '分镜图'], ['video', '分镜视频']].map(([key, label]) => `<button type="button" data-action="media-tab" data-value="${key}" aria-pressed="${state.mediaTab === key}">${label}</button>`).join('')}</div>`;
  let content;
  if (!isAsset && state.mediaTab === 'info') {
    content = `<div class="shot-info-editor">${field('镜头脚本', 'description', a.description)}<div class="field-pair">${field('景别', 'size', a.size, ['全景', '中景', '近景', '特写', '主观镜头'])}${field('运镜', 'movement', a.movement, ['固定', '缓慢跟拍', '轻微推进', '横移', '缓慢推进'])}</div>${textField('脚本时长（秒）', 'shotDuration', a.duration, 'number')}<div class="setting-rule"></div><span class="field-label">关联素材</span>${references(a.refs)}</div>`;
  } else if (!hasImage || !imageName) {
    content = `<div class="state-message">${icon(!isAsset && state.mediaTab === 'video' ? 'play' : 'image')}<strong>${!isAsset && state.mediaTab === 'video' ? '这个镜头还没有视频' : '还没有采用的图片'}</strong>${!isAsset && state.mediaTab === 'video' && !a.adopted ? '<p>先生成并采用分镜图。</p>' : ''}${btn('打开生成设置', 'show-inspector')}</div>`;
  } else {
    const image = `<img ${!isAsset && state.mediaTab === 'video' ? 'class="video-poster"' : 'id="previewImage"'} src="assets/${imageName}.jpg" alt="${esc(a.name)} · 图库画面示意" style="transform:scale(${state.zoom / 100});${latest ? 'filter:brightness(1.12) contrast(1.03)' : ''}">`;
    const video = !isAsset && state.mediaTab === 'video';
    content = `<div class="preview-viewport">${image}<span class="preview-caption">${video ? '视频播放器布局示意' : '图库示意图'} · ${esc(latest?.params?.aspect ?? a.params.aspect)}</span>${video ? `<div class="video-simulation">${btn(icon('play'), 'play-video', '', 'aria-label="演示播放状态"')}<p id="videoPlaybackLabel">静态原型 · 播放交互示意</p></div><div class="video-timeline"><span id="videoTime">00:00 / 00:${String(a.params.duration).padStart(2, '0')}</span><input type="range" min="0" max="100" value="0" aria-label="视频播放进度" id="videoRange"></div>` : `<div class="preview-tools"><button class="icon-button" type="button" data-action="zoom-out" aria-label="缩小预览">${icon('minus')}</button><span id="zoomLabel">${state.zoom}%</span><button class="icon-button" type="button" data-action="zoom-in" aria-label="放大预览">${icon('plus')}</button><button class="icon-button" type="button" data-action="zoom-fit" aria-label="适合窗口">${icon('image')}</button><button class="icon-button" type="button" data-action="fullscreen" aria-label="全屏预览">${icon('expand')}</button></div>`}</div>`;
  }
  const resultBar = latest && (isAsset || state.mediaTab !== 'info') ? `<div class="result-bar"><strong>本次结果 · 未采用</strong>${btn(`${icon('check', true)}采用此结果`, 'adopt', 'primary')}</div>` : '';
  const info = !isAsset && state.mediaTab === 'info' ? '' : `<div class="preview-info"><div class="info-title"><h3>${isAsset ? '素材描述' : '镜头脚本'}</h3>${btn('编辑', 'edit-info', 'small text-button')}</div><p>${esc(a.description)}</p>${isAsset ? '' : `<div class="reference-row">${a.refs.map(referenceChip).join('')}<button class="reference-add" type="button" data-action="references">${icon('plus', true)}关联素材</button></div>`}</div>`;
  $('mainPanel').innerHTML = `<header class="panel-heading"><h2>${!isAsset ? `${String(a.position).padStart(2, '0')} · ` : ''}${esc(a.name)}</h2><div class="heading-actions">${headingBadge}${navigation}<button class="icon-button" type="button" data-action="history" aria-label="查看生成记录" title="生成记录">${icon('history')}</button></div></header><div class="view-tabs">${tabs}<span class="view-label">${isAsset ? kinds[a.kind] : `${esc(a.size)} · ${a.duration} 秒`}</span></div>${mediaWorkspace(content + resultBar + info)}`;
  renderInspector(); syncPanels();
}

function renderScript() {
  const latest = state.source.showLatest && state.source.latest;
  const text = latest ? latest.html : state.scriptHtml;
  const words = text.replace(/<[^>]*>/g, '').replace(/\s/g, '').length;
  $('mainPanel').innerHTML = `<header class="panel-heading"><h2>本集剧本</h2><div class="heading-actions">${badge(latest ? '新结果 · 未采用' : state.confirmed ? '已确认定稿' : '草稿 · 待确认', latest ? 'warning' : state.confirmed ? 'success' : '')}${btn(`${icon('download', true)}导出`, 'download', 'small text-button')}</div></header><div class="script-toolbar"><span>${words} 字</span><span>3 个场次</span>${state.source.latest ? `<div class="tabs"><button type="button" data-action="current" aria-pressed="${!latest}">当前稿</button><button type="button" data-action="latest" aria-pressed="${!!latest}">最新生成</button></div>` : ''}<div class="scene-shortcuts">${[1, 2, 3].map((n) => `<button type="button" data-action="scene" data-value="${n}">场次 ${String(n).padStart(2, '0')}</button>`).join('')}</div></div><div class="script-scroll"><article class="script-document" id="scriptEditor" ${latest ? '' : 'contenteditable="true" role="textbox" aria-multiline="true"'} aria-label="${latest ? '新剧本结果预览' : '本集剧本正文'}" spellcheck="false">${text}</article></div>${latest ? `<div class="result-bar"><strong>新剧本 · 未采用</strong>${btn('采用并编辑', 'adopt', 'primary')}</div>` : `<footer class="script-confirm-bar"><strong id="scriptConfirmTitle">${state.confirmed ? '剧本已确认' : '草稿待确认'}</strong>${btn(state.confirmed ? '进入素材生成' : '确认当前剧本', state.confirmed ? 'continue' : 'confirm-script', 'primary', 'id="scriptConfirmButton"')}</footer>`}`;
}

function referenceChip(id) { const a = assets.find((x) => x.id === id); return a ? `<span class="reference-item">${a.image ? `<img src="assets/${a.image}.jpg" alt="">` : icon('image', true)}${esc(a.name)}</span>` : ''; }
function references(ids, purpose = 'refs') { return `<div class="reference-row">${ids.map(referenceChip).join('')}<button class="reference-add" type="button" data-action="references" data-purpose="${purpose}">${icon('plus', true)}选择素材</button></div>`; }

function renderInspector() {
  const host = settingsHost();
  if (!host) return;
  const source = state.stage === 'source';
  const a = source ? state.source : item();
  const p = a.params;
  const infoTab = state.stage === 'assets' && state.settingsTab === 'info';
  const video = !source && state.stage === 'storyboard' && state.mediaTab === 'video';
  let content;
  if (source) {
    content = field('文本模型', 'model', p.model, ['个人文本模型 A', '个人文本模型 B']) + `<div class="field-pair">${field('本集画幅', 'aspect', p.aspect, ['16:9', '9:16'])}${field('视觉风格', 'style', p.style, ['写实悬疑', '电影写实', '动画风格'])}</div><div class="setting-rule"></div>${field('改编要求', 'instructions', p.instructions)}<details class="advanced-settings"><summary>原文摘要</summary><p class="field-help">匿名来信引导林岚进入雨夜街巷，周野带来一台相机，取景器里出现第三个人。</p></details>`;
  } else if (infoTab) {
    content = textField(state.stage === 'assets' ? '素材名称' : '镜头名称', 'name', a.name) + field(state.stage === 'assets' ? '素材描述' : '镜头脚本', 'description', a.description);
    content += `<div class="field-pair">${field('素材类型', 'kind', kinds[a.kind], ['角色', '场景', '道具'])}<label class="field"><span>使用范围</span><input value="本集 / 项目共享" readonly></label></div>`;
  } else {
    content = field(video ? '视频模型' : '图片模型', video ? 'videoModel' : 'model', video ? p.videoModel : p.model, video ? ['个人视频模型 A', '个人视频模型 B'] : ['个人图片模型 A', '个人图片模型 B']);
    if (state.stage === 'storyboard') {
      content += video ? `<span class="field-label">生成参考<small>已采用的分镜图</small></span><div class="reference-row">${a.adopted ? `<span class="reference-item"><img src="assets/${a.image}.jpg" alt="已采用分镜图示意">分镜 ${String(a.position).padStart(2, '0')} · 当前采用</span>` : '<p class="field-help">请先采用一张分镜图。</p>'}</div><p class="field-help">用作视频参考，不强制作为视频首帧。</p><div class="setting-rule"></div>` : `<span class="field-label">关联参考<small>已采用素材</small></span>${references(a.refs)}<div class="setting-rule"></div>`;
    }
    content += field(video ? '视频要求' : '画面要求', video ? 'videoPrompt' : 'prompt', video ? p.videoPrompt : p.prompt);
    const first = video ? field('视频时长', 'duration', p.duration, [...new Set([p.duration, '4', '5', '6', '8'])]) : state.stage === 'storyboard' ? field('画面布局', 'layout', p.layout, ['单图', '四宫格', '五宫格', '九宫格']) : field('图片比例', 'aspect', p.aspect, ['16:9', '9:16', '3:4', '1:1']);
    content += `<div class="field-pair">${first}${field(video ? '清晰度' : '分辨率', 'resolution', p.resolution, video ? ['2K', '1080p', '720p'] : ['2K', '1K', '4K'])}</div>`;
    if (!video) content += field('生成张数', 'count', p.count, ['1 张', '2 张', '4 张']);
    if (state.stage === 'assets') content += `<span class="field-label">参考图片<small>最多 16 张</small></span>${a.refs.length ? references(a.refs) : `<button class="reference-upload" type="button" data-action="references">${icon('plus', true)}选择参考图片</button>`}`;
    content += video ? '<p class="field-help">视频时长独立于脚本时长。</p>' : `<details class="advanced-settings"><summary>高级参数</summary>${field('避免出现的内容', 'negative', p.negative)}${textField('随机种子', 'seed', p.seed)}</details>`;
  }
  const generating = state.busy.has(currentKey());
  const unavailable = state.demo === 'loading' || state.demo === 'error' || state.demo === 'empty' || state.demo === 'conflict';
  const disabled = unavailable || generating || (video && !a.adopted) || (source && !state.novel.trim());
  const actionLabel = infoTab ? '保存修改' : source ? '生成剧本' : video ? '生成分镜视频' : state.stage === 'assets' ? '生成素材图片' : '生成分镜图';
  host.innerHTML = `<header class="inspector-heading"><h2>${source ? '剧本改编设置' : infoTab ? '素材信息' : '生成设置'}</h2><button class="icon-button" type="button" data-action="toggle-inspector" aria-label="${source ? '收起改编设置' : '收起生成设置'}">${icon('close')}</button></header>${state.stage === 'assets' ? `<div class="inspector-tabs tabs"><button type="button" data-action="inspector-tab" data-value="settings" aria-pressed="${!infoTab}">生成参数</button><button type="button" data-action="inspector-tab" data-value="info" aria-pressed="${infoTab}">素材信息</button></div>` : ''}<div class="inspector-scroll">${content}</div><footer class="inspector-footer">${btn(`${generating ? '<span class="spinner" style="width:14px;height:14px"></span>' : icon(infoTab ? 'check' : 'spark', true)}${generating ? '正在生成…' : actionLabel}`, infoTab ? 'save-info' : 'generate', 'primary', disabled ? 'disabled aria-busy="' + generating + '"' : '')}</footer>`;
}

function renderAssistant() {
  const context = state.stage === 'source' ? '本集剧本' : item().name;
  const messages = state.messages.length ? state.messages : [{ role: 'assistant', text: '可以一起推敲当前作品。告诉我想调整的细节。' }];
  $('inspector').innerHTML = `<header class="inspector-heading"><h2>AI 创作助手</h2><div class="heading-actions"><button class="icon-button" type="button" data-action="assistant-new" aria-label="新对话">${icon('plus')}</button><button class="icon-button" type="button" data-action="assistant-history" aria-label="助手对话历史">${icon('history')}</button><button class="icon-button" type="button" data-action="assistant" aria-label="关闭 AI 创作助手">${icon('close')}</button></div></header><div class="assistant-body" id="assistantMessages"><span class="assistant-context">${icon('book', true)}引用：${esc(context)}</span>${messages.map((m) => `<div class="assistant-message ${m.role === 'user' ? 'user' : ''}">${m.text.split('\n\n').map((p) => `<p>${esc(p).replace(/\n/g, '<br>')}</p>`).join('')}${m.role === 'assistant' ? btn(`${icon('copy', true)}复制建议`, 'copy-advice', 'small text-button') : ''}</div>`).join('')}</div><div class="assistant-composer"><label class="sr-only" for="assistantInput">向 AI 助手提问</label><textarea id="assistantInput" placeholder="与助手讨论当前作品…\nEnter 发送，Shift + Enter 换行">${esc(state.assistantDraft)}</textarea><div class="composer-tools"><select aria-label="助手文本模型"><option>个人文本模型 A</option><option>个人文本模型 B</option></select>${btn(icon('send'), 'assistant-send', 'primary send-button', 'aria-label="发送消息"')}</div><p class="assistant-disclaimer">对话仅你可见 · 此处回复为交互示意</p></div>`;
}

function renderConflict() {
  const conflict = state.demo === 'conflict';
  $('conflictNotice').hidden = !conflict;
  $('conflictNotice').innerHTML = `${icon('alert')}<span>保存版本冲突。你的输入已保留，核对前暂停生成与阶段切换。</span>${btn('下载草稿', 'download', 'small')}${btn('核对版本', 'resolve-conflict', 'small')}`;
  if (conflict) $('saveState').innerHTML = `${icon('alert', true)}演示 · 版本冲突`;
  else $('saveState').innerHTML = '<span class="status-dot"></span>演示 · 已保存';
}

function setStage(stage) {
  if (state.demo === 'conflict') { toast('请先核对保存冲突，当前输入已保留。'); return; }
  if (!stageInfo[stage]) return;
  state.stage = stage; state.query = ''; state.settingsTab = 'settings'; state.zoom = 100;
  location.hash = stage; render();
  document.querySelector(`[data-stage="${stage}"]`).scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function generation() {
  const key = currentKey();
  if (state.busy.has(key) || state.demo !== 'normal') return;
  const stage = state.stage; const a = stage === 'source' ? state.source : item();
  const media = stage === 'storyboard' && state.mediaTab === 'video' ? 'video' : 'image';
  const params = { ...a.params };
  state.busy.add(key); renderInspector();
  toast('演示任务已开始，正在生成。你可以继续查看其他对象。');
  setTimeout(() => {
    const result = { image: a.image ?? 'camera', params, version: (a.records.length ?? 0) + 2, editVersion: a.version ?? 1, type: stage === 'source' ? 'script' : media };
    if (stage === 'source') { result.html = initialScript.replace('“别回头。”', '“现在，无论听到什么，都别回头。”'); a.latest = result; a.showLatest = true; }
    else if (media === 'video') { a.latestVideo = result; a.showLatestVideo = true; }
    else { a.latest = result; a.showLatest = true; }
    a.records.unshift(result); state.busy.delete(key);
    if (state.stage === stage && (stage === 'source' || item().id === a.id)) { renderMain(); renderInspector(); }
    toast('生成演示完成。结果可预览，尚未替换当前作品。');
  }, 1300);
}

function requestAdoption() {
  const source = state.stage === 'source'; const a = source ? state.source : item();
  const video = !source && state.stage === 'storyboard' && state.mediaTab === 'video';
  const result = video ? a.latestVideo : a.latest;
  if (!result) return;
  state.adoption = { source, a, result, video, stage: state.stage };
  const stale = !source && result.editVersion !== a.version;
  openModal(source ? '采用并编辑这份剧本？' : `采用${video ? '这段视频' : '这张图片'}？`, '明确采用后才更新当前作品', `<div class="modal-prose"><p>${source ? '新结果将成为本集当前编辑剧本；采用后需要重新确认定稿。原稿会留在生成记录里。' : `将更新<strong>${esc(a.name)}</strong>的当前${video ? '视频' : '图片'}。${state.stage === 'assets' ? '引用此素材的项目作品会使用新图片。' : '用于后续制作与剪辑，其他镜头保持原样。'}`}</p>${stale ? '<p class="badge warning">生成后描述发生变化，请确认此结果仍适用。</p>' : ''}<p class="muted">生成参数与历史继续仅你可见。</p></div>`, btn('暂不采用', 'close-modal') + btn(source ? '采用并编辑' : '确认采用', 'confirm-adopt', 'primary'));
}

function adopt() {
  const pending = state.adoption; if (!pending) return;
  if (pending.source) { state.source.records.push({ type: 'script', html: state.scriptHtml, version: '原稿', params: { ...state.source.params } }); state.scriptHtml = pending.result.html; state.source.showLatest = false; state.confirmed = false; }
  else if (pending.video) { pending.a.video = true; pending.a.showLatestVideo = false; }
  else { pending.a.image = pending.result.image; pending.a.adopted = true; pending.a.showLatest = false; }
  pending.result.adopted = true; state.adoption = null; $('modal').close(); renderList(); renderMain(); renderInspector(); toast('已采用此结果。');
}

function history() {
  const source = state.stage === 'source'; const a = source ? state.source : item();
  const records = a.records;
  const current = `<div class="history-row">${!source && a.image ? `<img src="assets/${a.image}.jpg" alt="当前作品示意图">` : ''}<div class="history-copy"><strong>${source ? '当前编辑剧本' : `当前${state.mediaTab === 'video' ? '视频' : '图片'}`}${badge(source ? state.confirmed ? '已确认' : '草稿' : a.adopted ? '已采用' : '尚未采用', source && state.confirmed || a.adopted ? 'success' : '')}</strong><p>${source ? '保留在工作区中，后续修改需要重新确认。' : esc(a.name)}</p></div>${btn('返回当前稿', 'history-current', 'small')}</div>`;
  const list = records.map((r, i) => `<div class="history-row">${!source ? `<img src="assets/${r.image}.jpg" alt="历史结果示意图">` : ''}<div class="history-copy"><strong>${source ? '剧本' : r.type === 'video' ? '视频' : '图片'} · 版本 ${esc(r.version)}${badge(r.adopted ? '已采用过' : '未采用', r.adopted ? 'success' : '')}</strong><p>${esc(r.params.model)} · ${esc(r.params.aspect ?? '16:9')}<br>演示记录，未调用真实模型</p></div>${btn('预览', 'history-preview', 'small', `data-index="${i}"`)}</div>`).join('');
  openModal(source ? '剧本生成记录' : `${a.name} · 生成记录`, '生成记录仅你可见', current + (records.length ? list : '<div class="note-box">暂无生成记录。</div>'));
}

function downloadDraft() {
  const content = state.stage === 'source' ? `本集小说\n${state.novel}\n\n本集剧本\n${state.scriptHtml.replace(/<[^>]*>/g, '\n')}` : JSON.stringify(state.stage === 'assets' ? assets : shots, null, 2);
  const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = `雾港来信-第01集-${state.stage}-原型草稿.txt`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('已下载本次原型草稿。');
}

function newObject() {
  const isAsset = state.stage === 'assets';
  openModal(isAsset ? '新增素材' : '新增镜头', '新增后选中对象，继续编辑与生成', `<label class="field"><span>${isAsset ? '素材' : '镜头'}名称</span><input id="newName" aria-label="新增对象名称" placeholder="输入名称" maxlength="80"></label>${isAsset ? '<label class="field"><span>素材类型</span><select id="newKind" aria-label="新增素材类型"><option value="character">角色</option><option value="scene">场景</option><option value="prop">道具</option></select></label>' : ''}<label class="field"><span>描述</span><textarea id="newDescription" aria-label="新增对象描述" placeholder="写下外观或镜头内容" rows="4"></textarea></label><p class="field-help" id="newObjectError" role="alert"></p>`, btn('取消', 'close-modal') + btn(isAsset ? '新增素材' : '新增镜头', 'create-object', 'primary'));
}

function extraction() {
  const isAsset = state.stage === 'assets';
  openModal(isAsset ? '从剧本提取素材' : '从剧本提取分镜', '读取已确认剧本，完成后核对提取结果', `${field('文本模型', 'extractionModel', '个人文本模型 A', ['个人文本模型 A', '个人文本模型 B'])}${field(isAsset ? '提取要求' : '分镜要求', 'extractionInstructions', isAsset ? '提取主要角色、场景和关键道具，避免重复。' : '按叙事顺序组织镜头，保持空间与视线连续。')}<div class="note-box">${state.confirmed ? '当前剧本已确认。' : '请先回到小说改编确认当前剧本。'}提取只整理${isAsset ? '素材文字' : '镜头脚本'}，不会自动生成媒体。</div>`, btn('取消', 'close-modal') + btn('演示提取与核对', 'extract-preview', 'primary', state.confirmed ? '' : 'disabled'));
}

function pickReferences(purpose = 'refs') {
  const a = item(); state.referencePurpose = purpose;
  state.pickedReferences = [...(purpose === 'library' ? [] : a.refs)];
  openModal(purpose === 'library' ? '从项目素材库导入' : '选择已采用的参考素材', '此处使用演示素材；生成记录与模型配置不随素材导入', `<div class="asset-picker">${assets.filter((x) => x.adopted).map((x) => `<div class="picker-item"><img src="assets/${x.image}.jpg" alt="${esc(x.name)}示意图"><label><input type="checkbox" data-pick="${x.id}" ${state.pickedReferences.includes(x.id) ? 'checked' : ''}>${esc(x.name)}</label></div>`).join('')}</div><p class="field-help">${purpose === 'library' ? '已存在的同名素材会跳过，避免重复引用。' : '最多 16 张；此原型以已采用素材图片演示选择。'}</p>`, btn('取消', 'close-modal') + btn(purpose === 'library' ? '确认导入' : '确认选择', 'confirm-references', 'primary'));
}

function sourceDrawer() { $('novelEditor').value = state.novel; $('novelWordCount').textContent = `${state.novel.length} 字`; $('sourceDialog').showModal(); }
function saveFeedback() { if (state.demo === 'conflict') return; clearTimeout(state.saveTimer); $('saveState').textContent = '演示 · 正在保存…'; state.saveTimer = setTimeout(() => { $('saveState').innerHTML = '<span class="status-dot"></span>演示 · 已保存'; }, 650); }
function selectObject(id) { if (state.demo === 'conflict') { toast('请先核对保存冲突。'); return; } if (state.stage === 'assets') state.assetId = id; else state.shotId = id; state.zoom = 100; renderList(); renderMain(); renderInspector(); if (state.assistant) renderAssistant(); }
function closeAssistant() { state.assistant = false; render(); $('assistantToggle').focus(); }

document.addEventListener('click', (event) => {
  const stageButton = event.target.closest('[data-stage]'); if (stageButton) { setStage(stageButton.dataset.stage); return; }
  const closeButton = event.target.closest('[data-close]'); if (closeButton) { closeButton.closest('dialog').close(); return; }
  const target = event.target.closest('[data-action]'); if (!target || target.disabled) return;
  const action = target.dataset.action;
  switch (action) {
    case 'close-modal': $('modal').close(); break;
    case 'source': sourceDrawer(); break;
    case 'continue': if (state.stage === 'source') setStage('assets'); else if (state.stage === 'assets') setStage('storyboard'); else openModal('成片合成阶段', '本次设计范围为小说改编、素材生成与分镜制作', '<div class="modal-prose"><p>成片阶段沿用项目现有剪辑与导出页面。这里保留顶部第四步的位置，供三个页面之间的流程衔接评审。</p></div>'); break;
    case 'assembly': openModal('成片合成阶段', '本次设计范围为小说改编、素材生成与分镜制作', '<div class="modal-prose"><p>成片阶段沿用项目现有剪辑与导出页面。这里保留顶部第四步的位置，供三个页面之间的流程衔接评审。</p></div>', btn('回到分镜制作', 'close-modal')); break;
    case 'project': openModal('返回项目详情', '静态原型入口', '<div class="modal-prose"><p>正式页面在此返回项目详情。原型保留本次三个阶段的输入，你可以关闭此窗口继续评审。</p></div>'); break;
    case 'episodes': openModal('切换分集', '本次原型展示第 01 集', '<div class="history-row"><div class="history-copy"><strong>第 01 集 · 雨夜来信</strong><p>当前分集</p></div>' + badge('正在编辑', 'success') + '</div><p class="field-help">正式页面切换前等待保存回执，失败时保留当前输入。</p>'); break;
    case 'toggle-inspector': state.settingsClosed = !state.settingsClosed; state.autoCollapsed = false; if (!state.settingsClosed && window.innerWidth <= 700) state.assistant = false; render(); if (state.settingsClosed) $('stageActions').querySelector('[data-action="toggle-inspector"]')?.focus(); else if (window.innerWidth <= 700) settingsHost()?.querySelector('button')?.focus(); break;
    case 'show-inspector': state.settingsClosed = false; state.autoCollapsed = false; if (window.innerWidth <= 700) state.assistant = false; state.settingsTab = 'settings'; render(); if (window.innerWidth <= 700) settingsHost()?.querySelector('button')?.focus(); break;
    case 'assistant': if (state.assistant) closeAssistant(); else { state.assistant = true; render(); $('assistantInput').focus(); } break;
    case 'category': state.category = target.dataset.value; renderList(); break;
    case 'shot-filter': state.shotFilter = target.dataset.value; renderList(); break;
    case 'select': selectObject(target.dataset.id); break;
    case 'previous-object': case 'next-object': { const index = shots.findIndex((s) => s.id === state.shotId); selectObject(shots[index + (action === 'next-object' ? 1 : -1)].id); break; }
    case 'inspector-tab': state.settingsTab = target.dataset.value; renderInspector(); break;
    case 'media-tab': state.mediaTab = target.dataset.value; state.zoom = 100; renderMain(); renderInspector(); break;
    case 'edit-info': if (state.stage === 'storyboard') { state.mediaTab = 'info'; renderMain(); } else { state.settingsTab = 'info'; state.settingsClosed = false; if (window.innerWidth <= 700) state.assistant = false; render(); } break;
    case 'generate': generation(); break;
    case 'adopt': requestAdoption(); break;
    case 'confirm-adopt': adopt(); break;
    case 'current': case 'latest': { const a = state.stage === 'source' ? state.source : item(); if (state.stage === 'storyboard' && state.mediaTab === 'video') a.showLatestVideo = action === 'latest'; else a.showLatest = action === 'latest'; renderMain(); break; }
    case 'history': history(); break;
    case 'history-current': { const a = state.stage === 'source' ? state.source : item(); a.showLatest = false; a.showLatestVideo = false; $('modal').close(); renderMain(); break; }
    case 'history-preview': { const a = state.stage === 'source' ? state.source : item(); const r = a.records[Number(target.dataset.index)]; if (r.type === 'video') { a.latestVideo = r; a.showLatestVideo = true; state.mediaTab = 'video'; } else { a.latest = r; a.showLatest = true; if (state.stage === 'storyboard') state.mediaTab = 'image'; } $('modal').close(); renderMain(); renderInspector(); break; }
    case 'zoom-in': case 'zoom-out': case 'zoom-fit': state.zoom = action === 'zoom-fit' ? 100 : Math.max(50, Math.min(200, state.zoom + (action === 'zoom-in' ? 10 : -10))); if ($('previewImage')) $('previewImage').style.transform = `scale(${state.zoom / 100})`; if ($('zoomLabel')) $('zoomLabel').textContent = `${state.zoom}%`; break;
    case 'fullscreen': $('fullImage').src = $('previewImage').src; $('imageDialog').showModal(); break;
    case 'confirm-script': state.confirmed = true; renderMain(); toast('当前剧本已确认。'); break;
    case 'scene': $('scriptEditor').querySelector(`#scene-${target.dataset.value}`)?.scrollIntoView({ block: 'start' }); break;
    case 'download': downloadDraft(); break;
    case 'add': newObject(); break;
    case 'create-object': { const name = $('newName').value.trim(); if (!name) { $('newObjectError').textContent = '请填写名称。'; $('newName').focus(); break; } const description = $('newDescription').value.trim(); if (state.stage === 'assets') { const a = asset(`a${Date.now()}`, name, $('newKind').value, null, false, description); assets.push(a); state.assetId = a.id; state.category = a.kind; } else { const s = { ...shots[0], id: `s${Date.now()}`, name, description, position: shots.length + 1, adopted: false, latest: null, latestVideo: null, showLatest: false, video: false, records: [], refs: [], params: { ...shots[0].params, prompt: description, videoPrompt: description } }; shots.push(s); state.shotId = s.id; } state.demo = 'normal'; $('demoState').value = 'normal'; state.query = ''; $('modal').close(); render(); toast('已新增，继续编辑或生成。'); break; }
    case 'extract': extraction(); break;
    case 'extract-preview': { const isAsset = state.stage === 'assets'; openModal('核对提取结果', '文字提取演示，不会自动生成图片或视频', `<div class="modal-prose"><p>已识别${isAsset ? '角色、场景和关键道具' : '三个场次与八个镜头'}。本次示例与工作区内容一致，采用时会跳过重复项。</p><p>${isAsset ? '林岚、周野、神秘来客；雨夜街巷、城市街口、熄灯后的街巷；旧式相机、匿名来信。' : shotDescriptions.map((x, i) => `${String(i + 1).padStart(2, '0')} ${x[0]}`).join('；') + '。'}</p></div>`, btn('取消', 'close-modal') + btn('确认提取结果', 'confirm-extraction', 'primary')); break; }
    case 'confirm-extraction': $('modal').close(); toast('已核对提取结果；同名示例已存在，未重复加入。'); break;
    case 'library': pickReferences('library'); break;
    case 'references': pickReferences(target.dataset.purpose ?? 'refs'); break;
    case 'confirm-references': if (state.referencePurpose === 'library') toast('已核对项目素材，同名条目已存在。'); else { item().refs = [...state.pickedReferences]; toast('已更新参考素材。'); } $('modal').close(); renderMain(); renderInspector(); break;
    case 'save-info': renderList(); renderMain(); saveFeedback(); toast('演示修改已保存，当前图片保持原采用结果。'); break;
    case 'recover': state.demo = 'normal'; $('demoState').value = 'normal'; render(); break;
    case 'resolve-conflict': openModal('核对保存版本', '先保留草稿，再明确选择处理方式', `<div class="version-comparison"><div><h3>你的当前草稿</h3><pre>${esc(state.stage === 'source' ? state.scriptHtml.replace(/<[^>]*>/g, '') : item().description)}</pre></div><div><h3>服务端版本 · 演示</h3><pre>${esc(state.stage === 'source' ? initialScript.replace(/<[^>]*>/g, '') : '这里展示其他窗口保存的描述。请逐项核对后决定。')}</pre></div></div>`, btn('继续保留草稿', 'close-modal') + btn('下载草稿', 'download') + btn('载入服务端版本', 'reload-version')); break;
    case 'reload-version': openModal('确认载入服务端版本？', '将替换当前编辑输入；请先下载需要保留的草稿', '<p class="modal-prose">此选择仅用于演示冲突处理。确认后载入示例原稿并恢复编辑。</p>', btn('取消', 'close-modal') + btn('确认载入', 'confirm-reload', 'primary')); break;
    case 'confirm-reload': if (state.stage === 'source') state.scriptHtml = initialScript; else item().description = '已载入服务端示例描述。'; state.demo = 'normal'; $('demoState').value = 'normal'; $('modal').close(); render(); break;
    case 'import-novel': $('novelFile').click(); break;
    case 'confirm-import': state.novel = state.pendingNovel; $('novelEditor').value = state.novel; $('novelWordCount').textContent = `${state.novel.length} 字`; $('modal').close(); saveFeedback(); break;
    case 'assistant-new': state.messages = []; state.assistantDraft = ''; renderAssistant(); break;
    case 'assistant-history': openModal('项目对话历史', '私人对话，切换阶段保留同一项目对话', '<div class="history-row"><div class="history-copy"><strong>雾港来信 · 当前对话</strong><p>当前作品按消息引用；无需为每个素材重新创建会话。</p></div>' + badge('当前', '') + '</div>'); break;
    case 'assistant-send': sendAssistant(); break;
    case 'copy-advice': { const text = target.closest('.assistant-message').querySelector('p').textContent; if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast('已复制建议。'), () => openModal('复制建议', '选中文本后按 Ctrl+C', `<textarea class="standalone-input" rows="5" aria-label="待复制建议" readonly>${esc(text)}</textarea>`)); else openModal('复制建议', '选中文本后按 Ctrl+C', `<textarea class="standalone-input" rows="5" readonly>${esc(text)}</textarea>`); break; }
    case 'play-video': { const playing = target.getAttribute('aria-pressed') === 'true'; target.setAttribute('aria-pressed', String(!playing)); $('videoPlaybackLabel').textContent = playing ? '已暂停 · 播放交互示意' : '播放中 · 静态画面示意'; break; }
  }
});

function sendAssistant() {
  const text = $('assistantInput').value.trim(); if (!text) { $('assistantInput').focus(); return; }
  state.messages.push({ role: 'user', text }); state.assistantDraft = '';
  state.messages.push({ role: 'assistant', text: '建议先通过空间和光线建立悬念，再让人物进入画面。\n\n雨夜街巷可以使用低机位全景，保持蓝紫色反光；相机特写则用更近的景别，让“别回头”的信息成为叙事重点。\n\n这是原型示意回复。你可以复制建议，在生成设置里调整要求。' });
  renderAssistant(); $('assistantMessages').scrollTop = $('assistantMessages').scrollHeight; $('assistantInput').focus();
}

document.addEventListener('input', (event) => {
  const el = event.target;
  if (el.id === 'objectSearch') { state.query = el.value; const selection = el.selectionStart; renderList(); const next = $('objectSearch'); next.focus(); if (selection !== null && next.type !== 'search') next.setSelectionRange(selection, selection); }
  else if (el.id === 'novelEditor') { state.novel = el.value; $('novelWordCount').textContent = `${el.value.length} 字`; saveFeedback(); }
  else if (el.id === 'assistantInput') state.assistantDraft = el.value;
  else if (el.id === 'scriptEditor' || el.closest('#scriptEditor')) {
    state.scriptHtml = $('scriptEditor').innerHTML; state.confirmed = false; saveFeedback();
    const status = $('mainPanel').querySelector('.heading-actions .badge'); if (status) { status.className = 'badge'; status.textContent = '草稿 · 待确认'; }
    const wordCount = $('mainPanel').querySelector('.script-toolbar > span'); if (wordCount) wordCount.textContent = `${$('scriptEditor').textContent.replace(/\s/g, '').length} 字`;
    if ($('scriptConfirmTitle')) $('scriptConfirmTitle').textContent = '草稿待确认';
    if ($('scriptConfirmButton')) { $('scriptConfirmButton').textContent = '确认当前剧本'; $('scriptConfirmButton').dataset.action = 'confirm-script'; }
  } else if (el.id === 'videoRange') { const duration = Number(item().params.duration); $('videoTime').textContent = `00:${String(Math.round(duration * Number(el.value) / 100)).padStart(2, '0')} / 00:${String(duration).padStart(2, '0')}`; }
});

document.addEventListener('change', (event) => {
  const el = event.target;
  if (el.id === 'demoState') { state.demo = el.value; render(); return; }
  if (el.dataset.pick) { if (el.checked) { if (!state.pickedReferences.includes(el.dataset.pick)) state.pickedReferences.push(el.dataset.pick); } else state.pickedReferences = state.pickedReferences.filter((id) => id !== el.dataset.pick); return; }
  if (el.dataset.param && !el.closest('dialog')) {
    const a = state.stage === 'source' ? state.source : item(); const key = el.dataset.param;
    if (['name', 'description', 'size', 'movement', 'kind', 'shotDuration'].includes(key)) {
      if (key === 'shotDuration') { const n = Number(el.value); if (!Number.isFinite(n) || n <= 0 || n > 120) { el.value = a.duration; toast('脚本时长请输入 1–120 秒。'); return; } a.duration = n; }
      else if (key === 'kind') a.kind = Object.keys(kinds).find((k) => kinds[k] === el.value);
      else a[key] = el.value;
      a.version += 1; renderList();
    } else a.params[key] = el.value;
    saveFeedback();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.target.id === 'assistantInput' && event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendAssistant(); }
  const panel = modalPanel();
  if (event.key === 'Tab' && panel && !document.querySelector('dialog[open]')) {
    const controls = [...panel.querySelectorAll('button:not(:disabled), input, select, textarea')].filter((el) => el.getClientRects().length);
    const first = controls[0]; const last = controls[controls.length - 1];
    if (first && (!panel.contains(document.activeElement) || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last))) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
  }
  if (event.key === 'Escape' && panel && !document.querySelector('dialog[open]')) { if (state.assistant) closeAssistant(); else { state.settingsClosed = true; render(); $('stageActions').querySelector('[data-action="toggle-inspector"]')?.focus(); } }
});

document.addEventListener('paste', (event) => {
  if (!event.target.closest('#scriptEditor')) return;
  event.preventDefault(); const selection = window.getSelection(); if (!selection?.rangeCount) return;
  const range = selection.getRangeAt(0); range.deleteContents(); const text = document.createTextNode(event.clipboardData.getData('text/plain')); range.insertNode(text); range.setStartAfter(text); range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
  $('scriptEditor').dispatchEvent(new Event('input', { bubbles: true }));
});

$('novelFile').addEventListener('change', async (event) => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 TXT 文本文件。');
    if (file.size > 2 * 1024 * 1024) throw new Error('原型支持导入 2 MiB 以内的 TXT。');
    state.pendingNovel = await file.text();
    openModal('用导入文件替换原文？', '本集剧本保持独立，原文输入将被替换', `<p class="modal-prose">即将导入 ${esc(file.name)}，共 ${state.pendingNovel.length} 字。</p>`, btn('取消', 'close-modal') + btn('确认导入', 'confirm-import', 'primary'));
  } catch (error) { toast(`导入失败：${error.message}`); }
  finally { $('novelFile').value = ''; }
});

$('panelScrim').addEventListener('click', () => { if (state.assistant) closeAssistant(); else { state.settingsClosed = true; render(); $('stageActions').querySelector('[data-action="toggle-inspector"]')?.focus(); } });
document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => { if (event.target !== dialog) return; const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); }));
window.addEventListener('hashchange', () => { const stage = location.hash.slice(1); if (stageInfo[stage] && stage !== state.stage) setStage(stage); });
let previousViewportWidth = window.innerWidth;
window.addEventListener('resize', () => {
  const width = window.innerWidth;
  if (previousViewportWidth > 700 && width <= 700) { state.settingsClosed = true; state.autoCollapsed = true; render(); }
  else if (previousViewportWidth <= 700 && width > 700 && state.autoCollapsed) { state.settingsClosed = false; state.autoCollapsed = false; render(); }
  else syncPanels();
  previousViewportWidth = width;
});
new ResizeObserver(() => { $('mainPanel').classList.toggle('compact-preview', $('mainPanel').clientWidth < 680); }).observe($('mainPanel'));
render();
