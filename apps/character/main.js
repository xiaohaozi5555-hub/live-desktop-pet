/* main.js — Electron 主进程：透明置顶桌宠窗 + edge-tts 语音桥 + 截图验证模式。
 *   普通:  npm start            可见透明置顶窗 + 调试面板
 *   演示:  electron . --demo    自动播放一段 action 序列
 *   截图:  electron . --capture 逐状态截图到 .cache/ 后退出（视觉验证用）
 *   录制:  npm start -- --no-gpu  正常运行但关闭硬件加速——排查"外部录屏/直播伴侣抓这个
 *          透明窗口画面卡在第一帧不更新"时用这个先试，不影响功能，只是关掉GPU合成
 *
 * 启动诊断：所有窗口生命周期事件落盘到 startup.log，方便主播反馈"打不开/看不见"时排查；
 * 窗口越界（比如上次记的坐标在已拔掉的副屏上）时自动纠正回主屏可见区域。
 */
const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const net = require('net');
const BUS_PORT = parseInt(process.env.BUS_PORT || '8765', 10);

const ROOT = __dirname;
const REPO = path.resolve(ROOT, '..', '..');
const PYTHON = path.join(REPO, '.venv', 'Scripts', 'python.exe');
const CACHE = path.join(ROOT, '.cache');
fs.mkdirSync(CACHE, { recursive: true });

// 一键启动要拉起的后台服务。顺序有意义：broker 必须最先起，其余都要连它。
// perception-game 同时提供"被吓反应"和"卡关攻略截屏"，控制台的看画面按钮依赖它在跑。
const SERVICES = [
  { key: 'broker', name: '总线', script: ['services', 'bus', 'broker.py'] },
  { key: 'brain', name: '决策', script: ['services', 'brain', 'run.py'] },
  { key: 'dialogue', name: '对话', script: ['services', 'dialogue', 'run.py'] },
  { key: 'game', name: '看画面', script: ['services', 'perception-game', 'run.py'] },
];
// 实时弹幕（DouyinBarrageGrab 路线）总开关。默认关：依赖的抓包程序未必装了，
// 开着只会让服务空转重连、把 startup.log 刷满。装好后设 PET_GRAB=1，弹幕数据接入和
// 「环境守护」的证书/代理还原就随桌宠一起自动起停，主播不用记任何操作。
//
// 两个服务**职责分开，不能合并**：
//   danmaku  —— 数据链路：连抓包程序 → 归一化 → 发总线。桌宠有没有反应全靠它。
//   grabverify —— 验证员：只旁观判卷（另开一条连接拿原始包 + 在总线上只订阅不发布）。
//     它是阶段性的，验证完那几个未知项就可以关掉，关掉不影响桌宠工作。
//     曾经把转发写进验证员里，那样验证员既答题又判卷，链路断了它自己发现不了。
const GRAB_ON = process.env.PET_GRAB === '1' || process.env.PET_GRAB_RECORD === '1';
const GRAB_VERIFY = process.env.PET_GRAB_VERIFY === '1' || process.env.PET_GRAB_RECORD === '1';
if (GRAB_ON) {
  SERVICES.push({ key: 'danmaku', name: '弹幕', script: ['services', 'perception-danmaku', 'run.py'] });
}
if (GRAB_VERIFY) {
  SERVICES.push({ key: 'grabverify', name: '验证员', script: ['services', 'perception-danmaku', 'record_grab.py'] });
}

const CAPTURE = process.argv.includes('--capture');
const DEMO = process.argv.includes('--demo');
const NO_GPU = process.argv.includes('--no-gpu');
// 一次性"搜一下"模式：`electron . --search=<关键词>`，把结果按 JSON 打到 stdout 然后退出。
// 卡关攻略要联网找攻略，而**只有真实的非无头浏览器搜得到东西**（2026-07-30 逐个实测）：
//   裸 HTTP：百度只给桩页 / 搜狗直接返验证码 / Bing 国内版返回的是别的查询的缓存内容
//   无头 Chrome（--headless=new --dump-dom）：撞上百度滑块验证码
//   真实浏览器打开百度：结果完全对题，还自带 B 站视频攻略
// Electron 内置的就是一个完整的真实 Chromium，`show:false` 只是不显示、不是无头，行为跟正常
// 浏览器一致。用它就不必再装 Playwright（要往 C 盘下 150MB，而本机 C 盘常年紧张）。
const SEARCH_ARG = process.argv.find((a) => a.startsWith('--search='));
const SEARCH_QUERY = SEARCH_ARG ? SEARCH_ARG.slice('--search='.length) : null;
if (CAPTURE || NO_GPU) app.disableHardwareAcceleration(); // 稳定 capturePage / 排查外部软件抓不到透明窗口画面

// 让桌宠在"被别的窗口盖住"时**仍然继续绘制**。
// 症状：直播伴侣用窗口/游戏/进程采集时桌宠画面定格，只有全屏采集正常。
//
// 曾有**两个候选原因**，① 已被实测排除：
//   ① 采集侧 —— 抖音官方文档说采集透明分层窗口要用「游戏进程」素材源 + 勾选「允许窗口
//      透明」（见 `抖音玩法提审规则要点.md`）。依据看着很硬，2026-07-25 录 demo 时也曾
//      按这条解释过同一现象。**但 2026-07-29 用户明确说这个选项他早就试过，没有用。**
//      别再让用户去勾一遍，也别再把它写进文档当"下一步"。
//   ② 渲染侧（本处所改，仍属推断，尚未证实）：Chromium 在 Windows 上会算窗口遮挡，判定
//      桌宠被全屏游戏盖住就停止绘制；全屏采集时桌宠露在屏幕上不算遮挡，所以只有那种正常。
// ① 出局后 ② 成了唯一在手的假设，但它没被验证过，别当成定论。② 也无效的话，兜底方案是
// "放弃透明背景 + 纯色背景走色度键抠图"——这条现在是明牌的退路，不是最后才考虑的选项。
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion'); // 关掉遮挡计算（Windows 上的主因）
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');          // 被遮挡也不降级
app.commandLine.appendSwitch('disable-renderer-backgrounding');                  // 渲染进程不进后台节流

// ── 不透明模式（采集定格的兜底方案）─────────────────────────────────────────────
//
// 上面 ② 那套开关只能解决"我们自己停止绘制"，解决不了"对方根本抓不到这种窗口"。
// 透明窗在 Windows 上是**分层窗口**(WS_EX_LAYERED)，而逐窗口采集（BitBlt/PrintWindow/
// 游戏钩子）对分层 + GPU 合成的窗口本来就常年不可靠；全屏采集走的是另一条路（桌面复制，
// 抓的是合成后的整个桌面），这正好解释了"只有全屏采集正常"这个现象。
//
// 所以这条路不去赌对方的采集实现，而是把"透明"这个变量整个去掉：窗口不透明、铺一层纯绿底，
// 再由直播伴侣的**绿幕抠图**把绿色抠掉。这样既能被采集到，又能保住"悬浮在游戏画面上"的效果。
//
// 2026-07-30 用户在软件里实地确认了一个关键前提（此前中控台判断错过一次，别再搞反）：
//   ✅ 「窗口捕获」/「捕捉进程」/「捕捉游戏」这三种源**有**绿幕抠图选项
//   ❌ 「截屏捕获」**没有**绿幕抠图
// 而「截屏捕获」恰恰是唯一能把透明桌宠采成动态的方式——它走的是合成后的桌面那条路。
// 于是形成一个死结：能采到动态画面的源没法抠背景，能抠背景的源采不到透明窗。
// **不透明 + 纯绿 + 窗口捕获 + 绿幕抠图** 正是同时满足两边的那个组合。
//
// 默认纯绿：实测 108 张立绘里离纯绿最远（色距 196，品红 187、纯蓝 171），抠图时最不容易
// 连角色一起抠掉。**角色配色以后大改的话重新跑一次取色。** 换色用 PET_CHROMA。
// ⚠️ 已知质量风险：立绘边缘是半透明抗锯齿像素，压在绿底上会混进绿色，抠完可能留一圈绿边。
// 真出现了先调抠图的相似度/羽化，其次考虑给角色加描边把边缘盖住（那是美术决定）。
//
// 📌 顺带：在这个组合下，上面那三个防遮挡开关**反而变得有用了**——「窗口捕获」时桌宠会被
// 全屏游戏盖住，而那正是 Chromium 会停止绘制的情形。之前它们不对症，现在对症了。
// 解析顺序：命令行显式指定 > 环境变量。**必须有 `--transparent` 这个反向开关**——控制台上的
// 切换是靠"带新参数重启"实现的，而重启会继承 PET_OPAQUE 环境变量（开播模式.cmd 设的），
// 没有反向开关就再也切不回透明。
const OPAQUE = process.argv.includes('--opaque')
  || (!process.argv.includes('--transparent') && process.env.PET_OPAQUE === '1');
const CHROMA = process.env.PET_CHROMA || '#00FF00';

// ── 移出视野（桌宠只给观众看，主播自己看不见）──────────────────────────────────
//
// 「窗口捕获」读的是**这个窗口自己的画面**，不是"屏幕上那块区域"。所以窗口根本不需要出现在
// 你眼前——挪到所有显示器之外，采集照样拿得到内容。这一下同时解决三件事：
//   ① 你自己不用一直盯着一个绿方块（出戏）
//   ② 「看画面」截全屏做卡关攻略时，画面里不再混进绿方块和魔丸
//   ③ 被吓检测的亮度统计不再被那块恒定绿色拉偏
//
// ⚠️ **「最小化」是唯一不能用的做法**：最小化的窗口不再绘制，采集只会拿到空白——这跟"被别的
// 窗口盖住"完全不同（盖住时窗口仍在绘制，那也是上面三个防遮挡开关在保的场景）。
//
// 位置能在运行时改（不像 transparent），所以控制台那个开关是**即时生效、不用重启**的。
const OFFSCREEN_START = process.argv.includes('--offscreen') || process.env.PET_OFFSCREEN === '1';

const STARTUP_LOG = path.join(ROOT, 'startup.log');
function logStartup(message) {
  const line = `[${new Date().toISOString()}] ${message}\n`;
  try { fs.appendFileSync(STARTUP_LOG, line, 'utf8'); } catch (e) { /* 日志失败不能拦启动 */ }
  console.log(line.trimEnd());
}

// 解析 venv 里的 edge-tts（优先 exe，退回 python -m edge_tts）
function ttsCmd() {
  const exe = path.resolve(ROOT, '..', '..', '.venv', 'Scripts', 'edge-tts.exe');
  if (fs.existsSync(exe)) return { cmd: exe, pre: [] };
  return { cmd: path.resolve(ROOT, '..', '..', '.venv', 'Scripts', 'python.exe'), pre: ['-m', 'edge_tts'] };
}

// 跑 edge-tts 合成一段语音。**必须异步**：这里曾经用 spawnSync，而 edge-tts 每次要等一次
// 网络往返，同步等待会把 Electron 主进程整个卡住——连带 connectBus 收到的 action 也没法及时
// 转发给渲染进程。实测表现是桌宠动作间隔忽长忽短（总线明明是均匀 4 秒），严重时整个反应被吞掉。
function runTts(cmd, args) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args);
    const err = [];
    child.stderr.on('data', (chunk) => err.push(chunk));
    child.on('error', (e) => resolve({ status: -1, stderr: Buffer.from(String(e)) }));
    child.on('close', (status) => resolve({ status, stderr: Buffer.concat(err) }));
  });
}

// 若窗口坐标落在当前屏幕可见区域之外（比如记住的坐标来自已拔掉的副屏），拉回主屏居中。
function ensureWindowOnScreen(target, width, height) {
  const work = screen.getPrimaryDisplay().workArea;
  const b = target.getBounds();
  const outside = b.x + b.width < work.x || b.y + b.height < work.y ||
    b.x > work.x + work.width || b.y > work.y + work.height;
  if (outside) {
    target.setBounds({
      x: Math.round(work.x + work.width - width - 20),
      y: Math.round(work.y + work.height - height - 20),
      width, height,
    });
    logStartup('pet: 窗口越界，已纠正回主屏右下角');
  }
}

// 移出视野 / 移回来。位置是能在运行时改的，所以这个开关即时生效，不用重启。
let offscreen = false;
let onScreenPos = null;          // 挪出去之前的位置，挪回来时复原

// ⚠️ **必须给窗口留 1 个像素在桌面内。**
// 2026-07-30 真实开播实测：整个挪出桌面之后桌宠就不动了，挪回来才恢复——Chromium 判定
// 窗口完全不可见就停止出帧。上面那三个防遮挡开关管的是"被别的窗口盖住"，管不了"整个不在
// 桌面上"这种情况。留 1px 在屏幕角落，它就仍然算可见、继续绘制，而窗口捕获拿到的是**整个
// 窗口**的画面，不受这 1px 限制。
// 代价：主播屏幕左上角会有一个 1×1 的绿点。这是目前已知唯一能同时满足"看不见"和"还在动"的做法。
const OFFSCREEN_KEEP_PX = 1;

function offscreenPoint() {
  // 多屏时**不能写死负数**——副屏完全可能就在主屏左边或上边，写死 -2600 反而挪到副屏上去了。
  // 取所有显示器里最靠左上的那个角，把窗口推出去、只留 OFFSCREEN_KEEP_PX 个像素在里面。
  const all = screen.getAllDisplays();
  const minX = Math.min(...all.map((d) => d.bounds.x));
  const minY = Math.min(...all.map((d) => d.bounds.y));
  const [w, h] = win.getSize();
  return [minX - w + OFFSCREEN_KEEP_PX, minY - h + OFFSCREEN_KEEP_PX];
}

function setOffscreen(on) {
  if (!win || win.isDestroyed()) return;
  if (on) {
    if (!offscreen) onScreenPos = win.getPosition();
    const [x, y] = offscreenPoint();
    win.setPosition(x, y);
  } else if (onScreenPos) {
    win.setPosition(onScreenPos[0], onScreenPos[1]);
  } else {
    ensureWindowOnScreen(win, 260, 340);       // 一开机就是移出状态，没有旧位置可复原
  }
  offscreen = on;
  logStartup(`pet: ${on ? '已移出视野（观众经窗口捕获仍能看到）' : '已移回屏幕'}`);
}

function attachDiagnostics(name, target) {
  logStartup(`${name}: created ${JSON.stringify(target.getBounds())}`);
  target.on('show', () => logStartup(`${name}: show ${JSON.stringify(target.getBounds())}`));
  target.on('hide', () => logStartup(`${name}: hide`));
  target.on('closed', () => logStartup(`${name}: closed`));
  target.webContents.on('did-finish-load', () => logStartup(`${name}: did-finish-load`));
  target.webContents.on('did-fail-load', (_e, code, desc, url) => logStartup(`${name}: did-fail-load ${code} ${desc} ${url}`));
  target.webContents.on('render-process-gone', (_e, details) => logStartup(`${name}: render-process-gone ${details.reason}`));
  target.webContents.on('console-message', (_e, level, message) => logStartup(`${name}: renderer console ${level} ${message}`));
}

// ---- 后台服务托管 ----------------------------------------------------------
// 由 Electron 自己拉起 Python 服务，好处是"双击一次全起来"、退出时一起收掉，不留孤儿进程。
const procs = new Map();          // key -> ChildProcess
let voiceProc = null;

function portInUse(port) {        // broker 可能已被别的窗口起过，避免重复起导致端口冲突
  return new Promise((resolve) => {
    const sock = net.connect(port, '127.0.0.1');
    const done = (v) => { sock.destroy(); resolve(v); };
    sock.once('connect', () => done(true));
    sock.once('error', () => done(false));
    setTimeout(() => done(false), 600);
  });
}

function startService(svc) {
  if (procs.has(svc.key)) return;
  const script = path.join(REPO, ...svc.script);
  if (!fs.existsSync(PYTHON) || !fs.existsSync(script)) {
    logStartup(`service ${svc.key}: 跳过（找不到 ${fs.existsSync(PYTHON) ? script : PYTHON}）`);
    return;
  }
  const child = spawn(PYTHON, [script], { cwd: REPO, windowsHide: true });
  procs.set(svc.key, child);
  logStartup(`service ${svc.key}: started pid=${child.pid}`);
  child.stdout.on('data', (d) => logStartup(`[${svc.key}] ${String(d).trim()}`));
  child.stderr.on('data', (d) => logStartup(`[${svc.key}!] ${String(d).trim()}`));
  child.on('exit', (code) => {
    procs.delete(svc.key);
    logStartup(`service ${svc.key}: exit ${code}`);
    pushBusState();
  });
}

async function startServices() {
  for (const svc of SERVICES) {
    if (svc.key === 'broker' && await portInUse(BUS_PORT)) {
      logStartup('service broker: 端口已被占用，复用现有 broker');
      continue;
    }
    startService(svc);
    await new Promise((r) => setTimeout(r, svc.key === 'broker' ? 900 : 350));
  }
}

// 按进程树杀。**不能直接 child.kill()**：venv 里的 python.exe 是个转发壳，会再拉起
// C:\Program Files\Python311\python.exe 当子进程，只杀壳的话真进程会变孤儿继续跑
// （跟 npm start 杀不掉 electron 是同一个坑）。实测父子关系确实存在，所以走 taskkill /T。
function killTree(child, label) {
  if (!child) return;
  try {
    if (process.platform === 'win32' && child.pid) {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
    } else {
      child.kill();
    }
    logStartup(`service ${label}: killed(tree)`);
  } catch (e) { /* 退出路径不阻塞 */ }
}

function stopAll() {
  if (backupWin && !backupWin.isDestroyed()) {
    try { backupWin.destroy(); } catch (e) { /* 退出路径不阻塞 */ }
  }
  backupWin = null;
  for (const [key, child] of procs) killTree(child, key);
  procs.clear();
  if (voiceProc) { killTree(voiceProc, 'voice'); voiceProc = null; }
  if (ttsDaemon) { killTree(ttsDaemon, 'tts'); ttsDaemon = null; ttsPending.clear(); }
  envGuard('cleanup', { detached: true });
}

// 环境守护：启动时拍快照(arm)，退出时还原(cleanup)——删掉抓包程序装进系统的根证书、
// 复原被它改掉的系统代理。那个程序自己**没有卸载实现**（读过源码确认），不做这一步，
// 一张十年有效期的机器级根证书就会永久留在系统里。
// 退出那次必须 detached：Electron 进程马上就没了，清理还得接着跑完；同时绝不能同步等，
// 同步调用会卡死主进程（教训见上面 runTts 的注释）。
function envGuard(action, opts = {}) {
  const script = path.join(REPO, 'services', 'env-guard', 'guard.py');
  if (!GRAB_ON || !fs.existsSync(PYTHON) || !fs.existsSync(script)) return;
  try {
    const child = spawn(PYTHON, [script, action], {
      cwd: REPO, windowsHide: true,
      detached: !!opts.detached, stdio: opts.detached ? 'ignore' : 'pipe',
    });
    if (opts.detached) { child.unref(); return; }
    child.stdout.on('data', (d) => logStartup(`[env-guard] ${String(d).trim()}`));
    child.stderr.on('data', (d) => logStartup(`[env-guard!] ${String(d).trim()}`));
  } catch (e) {
    logStartup(`env-guard ${action} 失败: ${e}`);
  }
}

// ---- 控制台窗口 ------------------------------------------------------------
let consoleWin;
function createConsole() {
  consoleWin = new BrowserWindow({
    width: 900, height: 620, minWidth: 720, minHeight: 480,
    title: '魔丸 · 控制台', backgroundColor: '#14141a',
    autoHideMenuBar: true, show: true,
    webPreferences: {
      preload: path.join(ROOT, 'console-preload.js'),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  attachDiagnostics('console', consoleWin);
  consoleWin.loadFile('console.html').catch((err) => logStartup(`console: loadFile 失败 ${String(err)}`));
  consoleWin.webContents.once('did-finish-load', () => { pushBusState(); pushVoiceState(); });
  // 桌宠窗是无边框的（frame:false），没有关闭按钮——控制台是唯一能关的窗口，
  // 所以关掉它就等于退出整个程序，否则用户关了控制台会发现桌宠关不掉、后台服务还在跑。
  consoleWin.on('closed', () => {
    consoleWin = null;
    if (!CAPTURE && !DEMO) app.quit();
  });
}

function toConsole(channel, payload) {
  if (consoleWin && !consoleWin.isDestroyed()) consoleWin.webContents.send(channel, payload);
}
function pushBusState() {
  toConsole('bus-state', { connected: !!(busSock && !busSock.destroyed), dialogue: procs.has('dialogue') });
}
function pushVoiceState() {
  toConsole('voice-state', { running: !!voiceProc, wakeWord: process.env.PET_WAKE_WORD || '魔丸' });
}

let win;
function createWindow() {
  win = new BrowserWindow({
    width: 260, height: 340,
    // 不透明模式下窗口自己铺底色。`html,body` 在 styles.css 里是 background:transparent，
    // 所以这里的 backgroundColor 会直接透出来，不用改 CSS。
    transparent: !OPAQUE, frame: false, resizable: false,
    ...(OPAQUE ? { backgroundColor: CHROMA } : {}),
    alwaysOnTop: true, skipTaskbar: true, show: true,
    webPreferences: {
      preload: path.join(ROOT, 'preload.js'), contextIsolation: true, nodeIntegration: false,
      // 窗口不可见/被遮挡时不要把定时器和动画降频——桌宠是被外部软件采集的，"看不见"
      // 不等于"不需要动"。缺这一条时表现为采集画面定格，见上方命令行开关的注释。
      backgroundThrottling: false,
    },
  });
  attachDiagnostics('pet', win);
  // 把当前模式写进日志：排查采集问题时第一件要确认的就是"这次跑的到底是哪种窗口"
  logStartup(`pet: 窗口模式=${OPAQUE ? `不透明(色度键底色 ${CHROMA})` : '透明'} 硬件加速=${(CAPTURE || NO_GPU) ? '关' : '开'}`);
  // ⚠️ 光给 BrowserWindow 设 backgroundColor **不够**。`styles.css` 第一行是
  // `html, body { background: transparent }`——页面自己声明了透明，非透明窗口下 Chromium
  // 就拿默认白色打底，结果窗口是白的而不是绿的（2026-07-30 实测踩到：采集出来一片纯白）。
  // 所以这里直接注入一条 !important 覆盖掉。用 on 而不是 once：重载后仍然生效。
  if (OPAQUE) {
    win.webContents.on('did-finish-load', () => {
      win.webContents.insertCSS(`html, body { background: ${CHROMA} !important; }`)
        .catch((err) => logStartup(`pet: 注入底色失败 ${String(err)}`));
    });
  }
  win.setAlwaysOnTop(true, 'screen-saver');
  if (CAPTURE) {
    win.setPosition(-2600, -2600); // 截图模式移出可视区，避免闪窗
  } else if (OFFSCREEN_START) {
    // 注意不要在这条分支里调 ensureWindowOnScreen——那个函数专门把越界窗口拉回主屏，
    // 会正好把我们刚挪出去的窗口拽回来。
    setOffscreen(true);
  } else {
    ensureWindowOnScreen(win, 260, 340);
    // show:true 通常够用；极少数情况下渲染慢会显得"启动了但看不见"，兜底强制 show 一次。
    setTimeout(() => {
      if (!win.isDestroyed() && !win.isVisible()) {
        win.show();
        logStartup('pet: fallback show（2s 后仍不可见，已强制显示）');
      }
    }, 2000);
  }
  win.loadFile('index.html').catch((err) => logStartup(`pet: loadFile 失败 ${String(err)}`));
  win.webContents.once('did-finish-load', () => {
    if (CAPTURE) runCapture();
    else if (DEMO) win.webContents.send('command', { kind: 'demo' });
    else connectBus();   // 普通启动：连本地总线，接收 brain 发来的 action.*
  });
}

// ── 自己出声时掐麦（防"她听见自己→当成主播说话→再回一句"的死循环）──────────────
//
// 2026-07-29 实测：TTS 从音箱出来被麦克风收回去，声纹没挡住（合成音相似度过了阈值），
// 于是自问自答连说 5 轮停不下来。详见 packages/contract/events.md 的 audio.self_speaking。
//
// 这里在**合成开始**就置位（而不是等播放开始），因为合成期间本来也不该有新输入进来，
// 早一点置位只会更安全；解除则必须等渲染进程报告"真的播完了"——只有它知道音频结束。
const GATE_MAX_MS = 30000;      // 兜底：万一渲染进程崩了没报 done，麦克风不能永久失聪
let selfSpeaking = false;
let gateTimer = null;

function setSelfSpeaking(on) {
  if (gateTimer) { clearTimeout(gateTimer); gateTimer = null; }
  if (on === selfSpeaking) {
    // 已经在说了又来一句（排队播放）：只把兜底计时往后推，不重复发事件
    if (on) gateTimer = setTimeout(() => setSelfSpeaking(false), GATE_MAX_MS);
    return;
  }
  selfSpeaking = on;
  publishBus({
    channel: 'perception', type: 'audio.self_speaking', ts: Date.now(),
    source: 'character', data: { on },
  });
  if (on) gateTimer = setTimeout(() => {
    logStartup('[tts] 兜底解除掐麦：30s 没等到播放结束回报');
    setSelfSpeaking(false);
  }, GATE_MAX_MS);
}

// 渲染进程报告：这一句（以及排在后面的）都播完了 / 或被"闭嘴"打断
ipcMain.on('speak-done', () => setSelfSpeaking(false));

// ── 常驻 edge-tts 进程 ────────────────────────────────────────────────────────
// 命令行方式每合成一句都要重付约 0.86s 的解释器冷启动（2026-07-29 实测），常驻只付一次。
// 剩下约 2s 是到微软的网络往返，砍不掉，且实测跟文本长短几乎无关。
// 起不来 / 出错 / 超时，一律退回下面原来的"现起一个 edge-tts"——宁可慢，也不能说不出话。
const TTS_TIMEOUT_MS = 20000;
let ttsDaemon = null;
let ttsReqId = 0;
const ttsPending = new Map();

function killTtsDaemon(reason) {
  if (!ttsDaemon) return;
  const d = ttsDaemon;
  ttsDaemon = null;
  for (const [, p] of ttsPending) p.reject(new Error(reason));
  ttsPending.clear();
  try { d.kill(); } catch (e) { /* 已经死了 */ }
}

function ttsDaemonProc() {
  if (ttsDaemon) return ttsDaemon;
  const script = path.join(REPO, 'services', 'tts', 'tts_daemon.py');
  if (!fs.existsSync(script) || !fs.existsSync(PYTHON)) return null;
  let child;
  try {
    child = spawn(PYTHON, [script], { cwd: REPO, windowsHide: true });
  } catch (err) {
    logStartup(`[tts] 常驻进程起不来，退回命令行方式：${err}`);
    return null;
  }
  let buf = '';
  child.stdout.on('data', (chunk) => {
    buf += chunk.toString('utf8');
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      let msg;
      try { msg = JSON.parse(line); } catch (e) { continue; }
      if (msg.ready === false) logStartup(`[tts] 常驻进程未就绪：${msg.error}`);
      const p = ttsPending.get(msg.id);
      if (p) { ttsPending.delete(msg.id); p.resolve(msg); }
    }
  });
  child.on('error', () => killTtsDaemon('常驻 TTS 进程出错'));
  child.on('close', () => killTtsDaemon('常驻 TTS 进程已退出'));
  ttsDaemon = child;
  return child;
}

// 用常驻进程合成；返回 true=成功。任何失败都返回 false，由调用方走兜底。
function synthViaDaemon(text, voice, out) {
  const proc = ttsDaemonProc();
  if (!proc) return Promise.resolve(false);
  const id = ++ttsReqId;
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      ttsPending.delete(id);
      logStartup('[tts] 常驻进程超时，这一句退回命令行方式');
      resolve(false);
    }, TTS_TIMEOUT_MS);
    ttsPending.set(id, {
      resolve: (msg) => { clearTimeout(timer); resolve(!!msg.ok); },
      reject: () => { clearTimeout(timer); resolve(false); },
    });
    try {
      proc.stdin.write(`${JSON.stringify({ id, text, voice, out })}\n`);
    } catch (err) {
      clearTimeout(timer); ttsPending.delete(id); resolve(false);
    }
  });
}

// TTS: 文本 -> mp3 -> base64 data URI（渲染进程 <audio> 直接播放）
ipcMain.handle('speak', async (_e, { text, voice }) => {
  try {
    setSelfSpeaking(true);
    const out = path.join(CACHE, `tts-${Date.now()}-${ttsReqId + 1}.mp3`);
    const voiceName = voice || 'zh-CN-XiaoyiNeural';
    // 计时：合成要走一次网络往返，实测 1.4~4.8s 且抖动很大，是"回复慢"的主要来源之一，
    // 所以如实记录下来（`常驻`/`命令行` 标出走的是哪条路，便于排查）。
    const t0 = Date.now();
    let ok = await synthViaDaemon(text || '', voiceName, out);
    let how = '常驻';
    let err = '';
    if (!ok) {
      how = '命令行';
      const t = ttsCmd();
      const r = await runTts(t.cmd, [...t.pre, '--voice', voiceName, '--text', text || '', '--write-media', out]);
      ok = r.status === 0;
      if (!ok) err = (r.stderr && r.stderr.toString('utf8')) || 'edge-tts failed';
    }
    logStartup(`[tts][耗时] ${Date.now() - t0}ms (${how})  ${String(text || '').slice(0, 20)}`);
    if (!ok || !fs.existsSync(out)) {
      return { ok: false, error: err || 'edge-tts failed' };
    }
    const b64 = fs.readFileSync(out).toString('base64');
    return { ok: true, audio: `data:audio/mp3;base64,${b64}` };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

// 用一个隐藏的真实浏览器窗口去百度搜一次，抽出结果。见文件头部 SEARCH_QUERY 那段的实测理由。
// **不做验证码相关的任何事**：撞上验证就如实返回 error，让上层说"没搜到"，绝不绕过。
async function runSearch(query) {
  const url = `https://www.baidu.com/s?wd=${encodeURIComponent(query)}`;
  let w = null;
  try {
    w = new BrowserWindow({
      show: false, width: 1280, height: 900,
      webPreferences: { offscreen: false, javascript: true, images: false },
    });
    // **强制直连，不走系统代理。** Chromium 默认会读 Windows 的系统代理设置，而本机常挂
    // Shadowsocks（出口在美国）。两个理由必须绕开它：
    //   ① 开播时主播会把 VPN 关掉，那时系统代理多半指着一个死端口，搜索会直接挂——
    //      "开发时好用、开播时失效"正是最坏的情况（2026-07-30 用户主动提出这个疑点才发现）。
    //   ② 百度这类国内站点从国内 IP 直连本来就通，绕一趟美国反而更慢、更容易被当异常流量。
    await w.webContents.session.setProxy({ mode: 'direct' });
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('加载超时')), 20000);
      w.webContents.once('did-finish-load', () => { clearTimeout(t); resolve(); });
      w.webContents.once('did-fail-load', (_e, code, desc) => {
        clearTimeout(t); reject(new Error(`加载失败 ${code} ${desc}`));
      });
      w.loadURL(url).catch(reject);
    });
    await new Promise((r) => setTimeout(r, 1200));   // 结果是脚本填的，给它一点时间落地

    const out = await w.webContents.executeJavaScript(`(() => {
      const txt = document.body.innerText || '';
      if (txt.includes('安全验证') || txt.includes('请完成下方验证')) return { blocked: true };
      const seen = new Set(), items = [];
      for (const el of document.querySelectorAll('#content_left .result, #content_left .c-container')) {
        const a = el.querySelector('h3 a') || el.querySelector('a[href]');
        if (!a || !a.href || !a.href.startsWith('http')) continue;
        // 「相关搜索」「大家还在搜」指向的是百度自己的搜索页，不是攻略，滤掉
        if (/^https?:\\/\\/[^/]*baidu\\.com\\/s\\?/.test(a.href)) continue;
        const title = (a.innerText || '').trim();
        if (!title || seen.has(title)) continue;
        seen.add(title);
        // 摘要不写死 class：百度的类名常改。直接拿整块可见文字，去掉开头重复的标题即可。
        let body = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (body.startsWith(title)) body = body.slice(title.length).trim();
        items.push({ title, url: a.href, snippet: body.slice(0, 300) });
        if (items.length >= 8) break;
      }
      return { blocked: false, items };
    })()`);

    if (out && out.blocked) return { query, results: [], error: '被搜索引擎要求验证，本次跳过' };
    return { query, results: (out && out.items) || [] };
  } catch (err) {
    return { query, results: [], error: String((err && err.message) || err) };
  } finally {
    if (w && !w.isDestroyed()) w.destroy();
  }
}

// ── 弹幕备用源：我们自己开一个隐藏窗口挂在直播间页面上 ──────────────────────────
//
// 为什么需要它（2026-08-02 真开播断流 58 分钟之后做的）：抓包程序是靠"改直播伴侣的启动
// 参数、给它注入 `--proxy-server=127.0.0.1:8827,direct://`"来接管流量的。注意结尾那个
// `direct://` —— 那是 Chromium 的回退规则，**代理一旦出问题，直播伴侣会自动改走直连**，
// 从此抓包程序什么也看不到，而它自己还好好活着。这正是那次断流的现象。
//
// 直播伴侣那条连接我们管不了（重启它＝打断直播），但**可以自己再开一条**：拿我们自己的
// 隐藏窗口去看同一个直播间，走同一个代理。于是弹幕有了两条独立来源，直播伴侣那条断了也
// 还有这条。抓包程序 `DoMessage` 按 msgId 去重（每类保留最近 300 条），两条源不会重复。
//
// ⚠️ **前提：抓包程序的 `processFilter` 里必须有 `electron`**（它默认只认"直播伴侣"这个
// 进程名，我们的窗口进程名是 electron，不加就会被它直接过滤掉）。已在
// `D:\BarrageGrab\WssBarrageServer.exe.config` 里加上，且保留了"直播伴侣"——最坏情况是
// 这条备用源不工作，主链路照旧。
//
// 代价（主播已知情并同意）：会给自己的直播间挂一个观众（在线人数 +1）；多一个隐藏窗口。
// 视频流默认被拦掉（只要弹幕不要画面），省 CPU——直播时本来就在跑游戏 + 软件渲染。
const BACKUP_PARTITION = 'persist:barrage-backup';
const GRAB_PROXY_PORT = parseInt(process.env.PET_GRAB_PROXY_PORT || '8827', 10);
const BACKUP_BLOCK_VIDEO = process.env.PET_BARRAGE_BACKUP_VIDEO !== '1';  // 默认拦视频
let backupWin = null;
let backupReloadAt = 0;

// 直播间号：优先环境变量；否则从最近一次录制的原始包里认出来（主播自己的房间号是固定的，
// 之前只要真开播过一次就一定录到过）。认不出就不启动备用源，并把原因写进日志。
function liveRoomId() {
  const fromEnv = (process.env.PET_LIVE_ROOM_ID || '').trim();
  if (fromEnv) return fromEnv;
  try {
    const dir = path.join(REPO, '.cache', 'grab');
    const files = fs.readdirSync(dir)
      .filter((f) => f.startsWith('raw-') && f.endsWith('.jsonl'))
      .sort()
      .reverse();
    for (const f of files) {
      const txt = fs.readFileSync(path.join(dir, f), 'utf8');
      // 原始包里 Data 是一段 JSON 字符串，所以文件里长这样：\"WebRoomId\":\"91935611423\"
      const m = txt.match(/WebRoomId\W{1,8}(\d{6,})/);
      if (m) return m[1];
    }
  } catch (e) { /* 认不出就走下面的日志分支 */ }
  return null;
}

async function startBarrageBackup() {
  if (!GRAB_ON || backupWin) return;
  const roomId = liveRoomId();
  if (!roomId) {
    logStartup('[backup] 认不出直播间号，弹幕备用源未启动（可设环境变量 PET_LIVE_ROOM_ID=<房间号>）');
    return;
  }
  try {
    backupWin = new BrowserWindow({
      show: false, width: 1280, height: 800,
      webPreferences: {
        partition: BACKUP_PARTITION,   // 独立会话：它的代理设置不能影响卡关搜索那个窗口（那个要强制直连）
        images: false,
        // 隐藏窗口默认会被降频，而降频会让这条 WebSocket 收得断断续续——备用源的全部意义
        // 就是"平时不吭声、关键时刻还活着"，这一条不能省。
        backgroundThrottling: false,
      },
    });
    const ses = backupWin.webContents.session;
    // 走抓包程序的代理，它才拦得到这条连接的弹幕流量
    await ses.setProxy({ proxyRules: `127.0.0.1:${GRAB_PROXY_PORT}` });
    backupWin.webContents.setAudioMuted(true);
    if (BACKUP_BLOCK_VIDEO) {
      // 只要弹幕不要画面：把视频流拦掉能省下大部分 CPU。真出问题（比如页面因此不加载弹幕）
      // 设 PET_BARRAGE_BACKUP_VIDEO=1 就能放行，不用改代码。
      ses.webRequest.onBeforeRequest({ urls: ['*://*/*'] }, (details, cb) => {
        const isMedia = details.resourceType === 'media'
          || /\.(flv|m3u8|ts)(\?|$)/i.test(details.url);
        cb({ cancel: isMedia });
      });
    }
    backupWin.webContents.on('render-process-gone', (_e, d) => {
      logStartup(`[backup] 备用源渲染进程没了(${d.reason})，5s 后重开`);
      try { backupWin.destroy(); } catch (err) { /* 已经没了 */ }
      backupWin = null;
      setTimeout(startBarrageBackup, 5000);
    });
    await backupWin.loadURL(`https://live.douyin.com/${roomId}`);
    logStartup(`[backup] 弹幕备用源已挂上直播间 ${roomId}`
      + `（走代理 :${GRAB_PROXY_PORT}，静音${BACKUP_BLOCK_VIDEO ? '、已拦视频' : ''}）`);
  } catch (err) {
    logStartup(`[backup] 弹幕备用源启动失败：${String(err)}`);
    try { if (backupWin) backupWin.destroy(); } catch (e) { /* ignore */ }
    backupWin = null;
  }
}

// 断流时重新加载备用源。**这是零风险的那条恢复路径**——重开的是我们自己的窗口，
// 完全不碰直播伴侣，不会影响推流、更不会掉播。
function reloadBarrageBackup(why) {
  const now = Date.now();
  if (!backupWin || backupWin.isDestroyed()) { startBarrageBackup(); return; }
  if (now - backupReloadAt < 60_000) return;      // 一分钟内不重复重载，免得告警连发时刷个不停
  backupReloadAt = now;
  logStartup(`[backup] ${why}，重新加载备用源`);
  try { backupWin.webContents.reload(); } catch (err) { logStartup(`[backup] 重载失败：${String(err)}`); }
}

async function snap(name) {
  const img = await win.webContents.capturePage();
  const p = path.join(CACHE, `m1-${name}.png`);
  fs.writeFileSync(p, img.toPNG());
  return p;
}

// 截图验证：逐个渲染典型状态并截图，覆盖 表情×动作×气泡
async function runCapture() {
  const states = [
    { expression: 'neutral', motion: 'idle', bubble: '' },
    { expression: 'happy', motion: 'wave', bubble: '欢迎 夜行猫 进入直播间~' },
    { expression: 'scared', motion: 'scared', bubble: '呀啊——吓死宝宝了！' },
    { expression: 'smug', motion: 'thank_big', bubble: '谢谢神秘大哥的嘉年华！！' },
    { expression: 'blush', motion: 'beg', bubble: '谢谢 夜行猫 的关注！么么哒~' },
  ];
  const shots = [];
  for (const s of states) {
    win.webContents.send('command', { kind: 'render', ...s });
    await new Promise((r) => setTimeout(r, 800));
    shots.push(await snap(`${s.expression}-${s.motion}`));
  }
  fs.writeFileSync(path.join(CACHE, 'm1-capture-log.json'), JSON.stringify({ ok: true, count: shots.length, shots }, null, 2));
  app.quit();
}

// 连本地总线（Python broker）：把 brain 发的 action.* 转给渲染进程，同时抄送控制台
let busSock = null;
function connectBus() {
  const sock = net.connect(BUS_PORT, '127.0.0.1', () => {
    logStartup(`[character] 已连总线 :${BUS_PORT}`);
    busSock = sock;
    pushBusState();
  });
  let buf = '';
  sock.on('data', (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      try {
        const msg = JSON.parse(line);
        if (msg.channel === 'action' && win && !win.isDestroyed()) win.webContents.send('action', msg);
        // 弹幕断流：先重开我们自己的备用源（零风险，不碰直播伴侣）。perception-danmaku
        // 那边的"重启抓包程序"是另一条更重的路，两者互不冲突。
        if (msg.channel === 'perception' && msg.type === 'danmaku.health'
            && msg.data && msg.data.ok === false) {
          reloadBarrageBackup('收到弹幕断流告警');
        }
        // 真开播那一刻必须重载一次备用源。桌宠是先起、主播后开播的，所以那个窗口一开始
        // 停在"未开播"的页面上，页面自己不会等到开播再去连弹幕——不重载它就永远是个摆设。
        if (msg.channel === 'command' && msg.type === 'stream_start') {
          backupReloadAt = 0;              // 开播是大事，绕过那个 1 分钟的重载节流
          reloadBarrageBackup('直播开始了');
        }
        toConsole('bus-message', msg);
      } catch (e) { /* ignore partial/invalid */ }
    }
  });
  const retry = () => {
    if (busSock === sock) { busSock = null; pushBusState(); }
    setTimeout(connectBus, 1500);
  };
  sock.on('error', retry);   // broker 未起则重试
  sock.on('close', retry);
}

// 主进程内部往总线发一条（总线没连上就静默丢弃——掐麦这类信号不值得把主流程搞崩）
function publishBus(msg) {
  if (!busSock || busSock.destroyed) return false;
  try {
    busSock.write(`${JSON.stringify(msg)}\n`);
    return true;
  } catch (err) {
    return false;
  }
}

// ── 绿幕模式开关（控制台用）─────────────────────────────────────────────────────
// ⚠️ `transparent` 是 BrowserWindow 的构造参数，硬件加速开关又必须在 app ready 之前调用，
// **两者都没法在运行时改**。所以"切换"只能是"带上新参数把桌宠重启一次"，按钮上已写明。
ipcMain.handle('stream-mode-get', () => ({ on: OPAQUE, chroma: CHROMA, grabOn: GRAB_ON }));

ipcMain.handle('stream-mode-set', (_e, on) => {
  // 开播中禁止切换：重启会走 before-quit 的 envGuard('cleanup') 再由新实例 arm，
  // 等于在直播途中把抓包程序的根证书删掉重装、系统代理拆了重设。风险远大于便利。
  if (GRAB_ON) {
    return { ok: false, error: '开播中不能切换：重启会连带重装抓包证书、重设系统代理。下播后再切。' };
  }
  const drop = new Set(['--opaque', '--transparent', '--no-gpu']);
  const args = process.argv.slice(1).filter((a) => !drop.has(a));
  args.push(on ? '--opaque' : '--transparent');
  // 绿幕模式必须同时关硬件加速：GPU 合成的画面交不给逐窗口采集，实测采出来是一片纯白
  // （2026-07-30，详见文件头部那段注释）。这两个开关是一套的，不要只加一个。
  if (on) args.push('--no-gpu');
  logStartup(`pet: 切换窗口模式 -> ${on ? '绿幕(不透明+软件渲染)' : '透明'}，重启 args=${args.join(' ')}`);
  app.relaunch({ args });
  app.quit();      // 用 quit 不用 exit：要走 before-quit -> stopAll，别留孤儿 python 进程
  return { ok: true };
});

// 移出视野：位置能运行时改，所以这个是即时生效的，不像绿幕模式要重启
ipcMain.handle('offscreen-get', () => ({ on: offscreen }));
ipcMain.handle('offscreen-set', (_e, on) => {
  setOffscreen(!!on);
  return { ok: true, on: offscreen };
});

// 控制台发出的指令统一走这里上总线（渲染进程不直接碰 socket）
ipcMain.handle('bus-publish', (_e, msg) => {
  if (!busSock || busSock.destroyed) return { ok: false, error: '总线未连接' };
  return publishBus(msg) ? { ok: true } : { ok: false, error: '写入失败' };
});

// 语音识别子进程：控制台按钮起停。stdout 逐行转给控制台显示"最近听到什么"。
ipcMain.handle('voice-start', () => {
  if (voiceProc) return { ok: true };
  const script = path.join(REPO, 'services', 'perception-voice', 'run.py');
  if (!fs.existsSync(PYTHON) || !fs.existsSync(script)) return { ok: false, error: '找不到语音模块' };
  voiceProc = spawn(PYTHON, [script], { cwd: REPO, windowsHide: true });
  logStartup(`voice: started pid=${voiceProc.pid}`);
  // 语音输出既发控制台显示，也落 startup.log——出问题时控制台已经关了，没有日志就无从排查
  // （实测踩过：用户反馈"喊了没反应"，日志里只有起停记录，看不出录错了麦克风）。
  const relay = (d) => String(d).split(/\r?\n/).forEach((l) => {
    if (!l.trim()) return;
    toConsole('voice-log', l);
    logStartup(`[voice] ${l.trim()}`);
  });
  voiceProc.stdout.on('data', relay);
  voiceProc.stderr.on('data', relay);
  voiceProc.on('exit', (code) => {
    logStartup(`voice: exit ${code}`);
    voiceProc = null;
    pushVoiceState();
  });
  pushVoiceState();
  return { ok: true };
});

// 麦克风测试 / 声纹重录：mic_tool.py 每行吐一个 JSON 事件，原样转给控制台由 UI 渲染。
// 做成"点按钮 + 看提示"是因为让用户对着看不见进度的后台进程盲说，实测两次都拿到无效数据。
let micProc = null;
ipcMain.handle('mic-run', (_e, mode) => {
  if (micProc) return { ok: false, error: '上一次还没结束' };
  const script = path.join(REPO, 'services', 'perception-voice', 'mic_tool.py');
  if (!fs.existsSync(PYTHON) || !fs.existsSync(script)) return { ok: false, error: '找不到麦克风工具' };
  if (voiceProc) { killTree(voiceProc, 'voice'); voiceProc = null; pushVoiceState(); }  // 独占麦克风
  micProc = spawn(PYTHON, [script, mode === 'enroll' ? 'enroll' : 'test'], { cwd: REPO, windowsHide: true });
  let tail = '';
  micProc.stdout.on('data', (d) => {
    tail += String(d);
    let i;
    while ((i = tail.indexOf('\n')) >= 0) {
      const line = tail.slice(0, i).trim(); tail = tail.slice(i + 1);
      if (!line) continue;
      try { toConsole('mic-event', JSON.parse(line)); } catch (err) { logStartup(`[mic] ${line}`); }
    }
  });
  micProc.stderr.on('data', (d) => logStartup(`[mic!] ${String(d).trim()}`));
  micProc.on('exit', (code) => {
    micProc = null;
    toConsole('mic-event', { ev: 'exit', code });
    logStartup(`mic_tool(${mode}): exit ${code}`);
  });
  return { ok: true };
});

ipcMain.handle('mic-stop', () => {
  if (micProc) { killTree(micProc, 'mic_tool'); micProc = null; }
  return { ok: true };
});

ipcMain.handle('voice-stop', () => {
  if (voiceProc) { killTree(voiceProc, 'voice'); voiceProc = null; }
  pushVoiceState();
  return { ok: true };
});

// --console / --no-services 供调试：只开窗口不拉服务，或反过来
const NO_SERVICES = process.argv.includes('--no-services');

app.whenReady().then(async () => {
  logStartup(`app ready cwd=${process.cwd()}`);
  // 搜索模式：不建桌宠窗、不起任何服务、不碰环境守护，搜完就退。
  if (SEARCH_QUERY !== null) {
    const out = await runSearch(SEARCH_QUERY);
    process.stdout.write(`${JSON.stringify(out)}\n`);
    app.exit(out.results && out.results.length ? 0 : 2);
    return;
  }
  createWindow();
  if (!CAPTURE && !DEMO) {
    createConsole();
    envGuard('arm');                       // 先拍快照，再拉服务——抓包程序装证书必须发生在快照之后
    if (!NO_SERVICES) await startServices();
    // 常驻 TTS 必须**提前**起。它自己也要付一次约 0.86s 的解释器+import 冷启动，
    // 懒到第一次说话才起的话，第一句照样白等——而第一句恰恰是最该快的那句。
    // 2026-07-30 实测过这个坑：第一句 4155ms(常驻) 跟旧的命令行方式 4131ms 毫无差别。
    ttsDaemonProc();
    // 弹幕备用源（只在开播模式下起）。放在服务之后：它要走抓包程序的代理，先让那边就绪。
    startBarrageBackup();
  }
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', stopAll);          // 关窗即收掉所有后台服务，不留孤儿 python 进程
