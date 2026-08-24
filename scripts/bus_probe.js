// bus_probe.js — Node 端总线探针：连 Python broker，打印收到的 action.*。
// 证明 Python broker ↔ Node 客户端 跨运行时 JSON-lines TCP 传输可用（Electron 角色即用此协议）。
// 用法: node bus_probe.js [port=8765] [millis=8000]
const net = require('net');
const PORT = parseInt(process.argv[2] || '8765', 10);
const MS = parseInt(process.argv[3] || '8000', 10);

let count = 0, buf = '';
function connect() {
  const sock = net.connect(PORT, '127.0.0.1', () => console.log(`[node-probe] connected :${PORT}`));
  sock.on('data', (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      try {
        const m = JSON.parse(line);
        if (m.channel === 'action') {
          count++;
          console.log(`  action#${count} ${m.type}: ${m.data.text || m.data.motion || m.data.expression || ''}`);
        }
      } catch (e) { /* ignore partial/invalid */ }
    }
  });
  sock.on('error', () => setTimeout(connect, 300));   // broker 未就绪则重试
  sock.on('close', () => setTimeout(connect, 300));
}
connect();
setTimeout(() => { console.log(`[node-probe] done, ${count} actions received`); process.exit(count > 0 ? 0 : 2); }, MS);
