/* 原型数据：全部为演示用假数据，结构与当前项目接口字段保持同名 */
window.DATA = {
  project: '退婚当天，我成了前夫的上司',
  targetSeconds: 300,
  brief: {
    logline: '被扫地出门的豪门弃妇，带着离婚协议走进前夫家的董事会，成了他的顶头上司。',
    outline: '开端：离婚当天净身出户，沈亦舟的车停在民政局门口；\n发展：一纸任命把林晚推上沈氏总裁的位置，顾霆琛成了她的下游合作方；\n反转：苏蔓在宴会上当众揭穿林晚母亲的身份，林晚反手甩出股权书；\n结局：林晚拿回母亲留下的股份，也拿回了自己的名字。',
    characters: '林晚：28 岁，被退婚的豪门长女，狠得下心，说话不超过三句。\n顾霆琛：32 岁，林晚的前夫，习惯用沉默压人，直到失去才开口。\n沈亦舟：35 岁，沈氏掌权人，伯乐，也是棋手。\n苏蔓：26 岁，顾霆琛的现任，靠揭别人伤疤活着。\n周医生：45 岁，说话只说结论。'
  },
  novel: '林晚把离婚协议推到桌对面的时候，大厅的白灯管还在嗡嗡响。\n\n顾霆琛没有看那两张纸，他只看着她，像看一份突然作废的合同。三年前他把这个人从民政局领回家，三年后同一个地方，她连笔都自己带了。\n\n「想清楚了？」他问。\n\n「顾总，我从来没这么清楚过。」\n\n红章落下，声音很轻。走出门口的时候，台阶下停着一辆黑色轿车，车窗降下来，沈亦舟侧过脸看她，说上车。\n\n林晚说，沈总，我们不顺路。\n\n沈亦舟笑了一下：从现在起，顺路。\n\n——摘自小说第 12 章',
  script: `场 01 · 日 · 内 · 民政局 · 大厅
人物：林晚 顾霆琛

△ 大厅的白灯管嗡嗡作响。林晚把离婚协议推到桌对面，指尖压着签字栏。
△ 顾霆琛没看协议，只看她。
顾霆琛（指节敲桌）：想清楚了？
林晚（不抬眼）：顾总，我从来没这么清楚过。
△ 红章落下，声音很轻。

场 02 · 日 · 外 · 民政局 · 门口
人物：林晚 沈亦舟

△ 黑色轿车停在台阶下。车窗降下，沈亦舟侧过脸。
沈亦舟：上车。
林晚（顿了一秒）：沈总，我们不顺路。
沈亦舟（笑）：从现在起，顺路。

场 03 · 日 · 内 · 沈氏集团 · 顶层
人物：林晚 沈亦舟 董事 3

△ 长桌两侧的董事起身。沈亦舟把任命书推到桌子中央。
沈亦舟：从今天起，沈氏总裁是林晚。
△ 满座哗然。林晚站在门口，风衣还没脱。
林晚（OS）：三年前你们把我赶出去，今天我从这道门进来。
林晚（平静）：各位，久等。

场 04 · 日 · 内 · 沈氏集团 · 会议室
人物：林晚 顾霆琛

△ 门被推开，顾霆琛站在光里。
顾霆琛（压低）：林晚，你在玩什么？
林晚（合上文件）：顾总，注意称呼。

场 05 · 夜 · 内 · 顾家 · 客厅
人物：顾霆琛 苏蔓

△ 苏蔓把手机推过去，屏幕上是今天的财经头条。
苏蔓（试探）：霆琛，她回来，是为了你吗？
△ 顾霆琛盯着屏幕，很久没说话。
顾霆琛（把手机扣在桌上）：她是为了让我看见。

场 06 · 夜 · 外 · 城市 · 天台
人物：林晚

△ 风很大。林晚把戒指摘下来，抬手的瞬间停住。
林晚（OS）：这枚戒指，我要亲手还给他。
△ 她把戒指攥进掌心，转身下楼。

场 07 · 日 · 内 · 医院 · 走廊
人物：林晚 周医生

△ 消毒水的味道。林晚把报告单捏出褶。
周医生：指标在掉，最多三个月。
△ 林晚没说话，把报告单折成四折塞进口袋。

场 08 · 夜 · 内 · 宴会厅 · 后台
人物：苏蔓 沈亦舟

△ 苏蔓补口红，镜子里多了个人。
沈亦舟（站在门口）：今晚你最好别开口。
苏蔓（笑）：沈总怕什么？

场 09 · 夜 · 内 · 宴会厅
人物：林晚 苏蔓 沈亦舟 顾霆琛

△ 水晶灯下，苏蔓举起酒杯，全场安静。
苏蔓（高声）：林晚的母亲，是沈家的保姆。
△ 满场哗然。林晚把杯子放下。
林晚：说完了？那我说说你的。`,

  candidateOps: [
    { no: '03', from: '林晚（OS）：三年前你们把我赶出去，今天我从这道门进来。', to: '林晚（OS）：三年前，你们把我从这道门赶出去。今天我走回来，坐你们的位置。', why: '台词更狠，删掉解释性对白' },
    { no: '05', from: '顾霆琛（把手机扣在桌上）：她是为了让我看见。', to: '顾霆琛（把手机扣在桌上）：她不是为了让我看见。她是来拿回东西的。', why: '把结论改成动作指向' },
    { no: '07', from: '周医生：指标在掉，最多三个月。', to: '周医生（合上病历）：指标在掉。最多三个月。', why: '加一个动作，把诊断变成画面' }
  ],
  candidateAppend: `场 10 · 夜 · 外 · 宴会厅 · 门口
人物：林晚 顾霆琛

△ 雨落下来。林晚站在台阶上，没打伞。
顾霆琛（追出来）：林晚——
林晚（回头）：顾总，你认错人了。`,

  assets: [
    { id: 'a1', name: '林晚', kind: '角色', label: '正面', status: 'ok', hue: 0, count: 2, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '14:32', cost: '38 秒',
      desc: '28 岁，齐耳短发，眉骨偏高，眼神冷。米白高领针织 + 深灰长风衣，耳后一颗小痣。',
      prompt: '角色设定图，28 岁亚洲女性，齐耳短发，米白高领针织，深灰长风衣，正面全身，纯灰背景，柔和顶光，写实电影质感，高清', refs: 2, version: 3 },
    { id: 'a2', name: '林晚', kind: '角色', label: '侧面', status: 'ok', hue: 8, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '14:35', cost: '36 秒',
      desc: '同上，正侧面全身，保持发型轮廓与风衣剪裁一致。',
      prompt: '角色设定图，28 岁亚洲女性，齐耳短发，米白高领针织，深灰长风衣，正侧面全身，纯灰背景，柔和顶光，写实电影质感', refs: 1, version: 1 },
    { id: 'a3', name: '林晚', kind: '角色', label: '背面', status: 'wait', hue: 14, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '', cost: '',
      desc: '同上，背面全身，重点交代风衣袖口与肩线。',
      prompt: '角色设定图，28 岁亚洲女性，米白高领针织，深灰长风衣，背面全身，纯灰背景，柔和顶光，写实电影质感', refs: 0, version: 0 },
    { id: 'a4', name: '顾霆琛', kind: '角色', label: '正面', status: 'ok', hue: 196, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '14:20', cost: '37 秒',
      desc: '32 岁，深灰西装三件套，袖扣银质，短发向后梳，眼下有一道很浅的疤。',
      prompt: '角色设定图，32 岁亚洲男性，深灰三件套西装，银质袖扣，背头，面无表情，正面全身，纯灰背景，写实电影质感', refs: 1, version: 2 },
    { id: 'a5', name: '顾霆琛', kind: '角色', label: '侧面', status: 'wait', hue: 200, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '', cost: '',
      desc: '同上，正侧面，露出眼下那道疤。',
      prompt: '角色设定图，32 岁亚洲男性，深灰三件套西装，正侧面，眼下浅疤，纯灰背景，写实电影质感', refs: 0, version: 0 },
    { id: 'a6', name: '沈亦舟', kind: '角色', label: '正面', status: 'stale', hue: 34, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '13:58', cost: '39 秒',
      desc: '35 岁，藏青双排扣，笑着但不算温和，习惯把手插在口袋里。',
      prompt: '角色设定图，35 岁亚洲男性，藏青双排扣西装，手插口袋，微笑，正面全身，纯灰背景，写实电影质感', refs: 1, version: 1 },
    { id: 'a7', name: '苏蔓', kind: '角色', label: '正面', status: 'ok', hue: 320, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1365', at: '13:44', cost: '35 秒',
      desc: '26 岁，栗色长卷发，酒红丝绒裙，红唇，眼神向上。',
      prompt: '角色设定图，26 岁亚洲女性，栗色长卷发，酒红丝绒裙，红唇，正面全身，纯灰背景，写实电影质感', refs: 0, version: 1 },
    { id: 'a8', name: '民政局 · 大厅', kind: '场景', label: '全景', status: 'ok', hue: 84, count: 1, model: 'Flux 1.1 Pro', size: '1536 × 864', at: '13:30', cost: '41 秒',
      desc: '白炽灯管，水磨石地面，两排铁椅，窗口上方挂着红色指示牌。',
      prompt: '场景概念图，民政局办事大厅，白炽灯管，水磨石地面，铁椅，红色指示牌，全景，写实电影质感', refs: 1, version: 1 },
    { id: 'a9', name: '沈氏集团 · 顶层', kind: '场景', label: '全景', status: 'ok', hue: 152, count: 1, model: 'Flux 1.1 Pro', size: '1536 × 864', at: '13:22', cost: '43 秒',
      desc: '整面落地窗，长条会议桌，冷白顶光，城市天际线在窗外。',
      prompt: '场景概念图，集团顶层会议室，整面落地窗，长条会议桌，冷白顶光，城市天际线，全景，写实电影质感', refs: 1, version: 1 },
    { id: 'a10', name: '顾家 · 客厅', kind: '场景', label: '全景', status: 'bad', hue: 26, count: 1, model: 'Flux 1.1 Pro', size: '1536 × 864', at: '13:10', cost: '',
      desc: '现代中式客厅，米色布艺沙发，落地窗朝南，傍晚暖光，茶几上一份摊开的财经报纸。',
      prompt: '场景概念图，现代中式客厅，米色布艺沙发，落地窗，傍晚暖光，茶几上财经报纸，全景，写实电影质感', refs: 0, version: 0, error: '提示词第 3 行有 1 个不可用词「血腥」，已自动截断' },
    { id: 'a11', name: '离婚协议', kind: '道具', label: '正面', status: 'ok', hue: 46, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1024', at: '12:58', cost: '22 秒',
      desc: 'A4 两页，白纸黑字，右下角一枚红色印章，纸边有折痕。',
      prompt: '道具特写，离婚协议两页 A4 纸，白纸黑字，红色印章，纸边折痕，俯拍，柔和顶光', refs: 0, version: 1 },
    { id: 'a12', name: '婚戒', kind: '道具', label: '正面', status: 'wait', hue: 50, count: 1, model: 'Flux 1.1 Pro', size: '1024 × 1024', at: '', cost: '',
      desc: '素圈铂金，内壁刻着两个字母，磨得很亮。',
      prompt: '道具特写，素圈铂金婚戒，内壁刻字，磨砂背景，侧逆光，微距', refs: 0, version: 0 }
  ],
  angles: ['正面', '侧面', '背面', '四分之三侧', '面部特写', '表情 / 服装'],

  shots: [
    { no: 'S01', scene: '01', act: '林晚把离婚协议推到桌对面，指尖压着签字栏', size: '全景', move: '固定', dur: 5, line: '顾总，我从来没这么清楚过。', status: 'ok', hue: 84, video: true },
    { no: 'S02', scene: '01', act: '红章落下，特写，声音很轻', size: '大特写', move: '推进', dur: 6, line: '', status: 'ok', hue: 46, video: true },
    { no: 'S03', scene: '02', act: '黑色轿车停在台阶下，车窗降下', size: '中景', move: '跟拍', dur: 5, line: '沈总，我们不顺路。', status: 'ok', hue: 196, video: true },
    { no: 'S04', scene: '03', act: '林晚站在长桌尽头，风衣还没脱', size: '全景', move: '推进', dur: 6, line: '从今天起，沈氏总裁是林晚。', status: 'ok', hue: 152, video: true },
    { no: 'S05', scene: '03', act: '满座哗然，董事们交换眼神', size: '中景', move: '横移', dur: 5, line: '', status: 'ok', hue: 168, video: true },
    { no: 'S06', scene: '04', act: '门被推开，顾霆琛站在光里', size: '中景', move: '固定', dur: 4, line: '林晚，你在玩什么？', status: 'wait', hue: 200, video: false },
    { no: 'S07', scene: '05', act: '苏蔓把手机推过去，屏幕是财经头条', size: '近景', move: '固定', dur: 6, line: '她回来，是为了你吗？', status: 'ok', hue: 320, video: true },
    { no: 'S08', scene: '05', act: '顾霆琛盯着屏幕，很久没说话', size: '近景', move: '推进', dur: 5, line: '她是为了让我看见。', status: 'wait', hue: 208, video: false },
    { no: 'S09', scene: '06', act: '风很大，林晚摘下戒指，抬手停住', size: '特写', move: '推近', dur: 6, line: '这枚戒指，我要亲手还给他。', status: 'ok', hue: 50, video: true },
    { no: 'S10', scene: '07', act: '消毒水走廊，她把报告单捏出褶', size: '全景', move: '横移', dur: 5, line: '指标在掉，最多三个月。', status: 'stale', hue: 180, video: true },
    { no: 'S11', scene: '08', act: '镜子里多了个人，苏蔓补口红的手停住', size: '中景', move: '固定', dur: 6, line: '今晚你最好别开口。', status: 'wait', hue: 300, video: false },
    { no: 'S12', scene: '09', act: '水晶灯下，苏蔓举杯，全场安静', size: '全景', move: '环绕', dur: 5, line: '林晚的母亲，是沈家的保姆。', status: 'ok', hue: 34, video: true }
  ],

  clips: [
    { id: 'c1', track: 'pic', no: 'S01', name: 'S01 民政局 · 大厅', start: 0, dur: 5, hue: 84 },
    { id: 'c2', track: 'pic', no: 'S02', name: 'S02 红章落下', start: 5, dur: 6, hue: 46 },
    { id: 'c3', track: 'pic', no: 'S03', name: 'S03 民政局 · 门口', start: 11, dur: 5, hue: 196 },
    { id: 'c4', track: 'pic', no: 'S04', name: 'S04 沈氏 · 顶层', start: 16, dur: 6, hue: 152 },
    { id: 'c5', track: 'pic', no: 'S05', name: 'S05 满座哗然', start: 22, dur: 5, hue: 168, unsynced: true },
    { id: 'c6', track: 'pic', no: 'S06', name: 'S06 会议室门开', start: 27, dur: 4, hue: 200 },
    { id: 'c7', track: 'pic', no: 'S07', name: 'S07 顾家 · 客厅', start: 31, dur: 6, hue: 320 },
    { id: 'c8', track: 'pic', no: 'S08', name: 'S08 手机扣在桌上', start: 37, dur: 5, hue: 208 },
    { id: 'c9', track: 'pic', no: 'S09', name: 'S09 天台 · 摘戒指', start: 42, dur: 6, hue: 50 },
    { id: 'c10', track: 'pic', no: 'S10', name: 'S10 医院 · 走廊', start: 48, dur: 5, hue: 180 },
    { id: 'c11', track: 'pic', no: 'S12', name: 'S12 宴会厅 · 举杯', start: 53, dur: 5, hue: 34, unsynced: true },
    { id: 's1', track: 'sub', name: '顾总，我从来没这么清楚过', start: 2.4, dur: 2.6 },
    { id: 's2', track: 'sub', name: '沈总，我们不顺路', start: 12.6, dur: 2.4 },
    { id: 's3', track: 'sub', name: '从今天起，沈氏总裁是林晚', start: 18.5, dur: 3.2 },
    { id: 's4', track: 'sub', name: '她回来，是为了你吗', start: 32.2, dur: 2.8 },
    { id: 'v1', track: 'vo', name: '林晚 · 内心独白', start: 44, dur: 4 },
    { id: 'v2', track: 'vo', name: '苏蔓 · 宴会厅', start: 53.5, dur: 4.2 },
    { id: 'm1', track: 'mus', name: '主题 · 钢琴（弱进）', start: 0, dur: 58 },
    { id: 'f1', track: 'sfx', name: '红章落下', start: 5.2, dur: 1.2 },
    { id: 'f2', track: 'sfx', name: '雨声 · 渐入', start: 55, dur: 8 }
  ],
  bins: [
    { no: 'S06', name: 'S06 会议室门开', dur: 4, hue: 200, kind: 'shot' },
    { no: 'S08', name: 'S08 手机扣在桌上', dur: 5, hue: 208, kind: 'shot' },
    { no: 'S11', name: 'S11 苏蔓补口红', dur: 6, hue: 300, kind: 'shot' },
    { name: '钢琴 · 悬疑主题', dur: 58, kind: 'mus' },
    { name: '雨声', dur: 12, kind: 'sfx' }
  ]
};
