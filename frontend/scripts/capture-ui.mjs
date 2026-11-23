import fs from 'node:fs/promises';
import path from 'node:path';

const targets = await fetch('http://localhost:9222/json').then((response) => response.json());
const page = targets.find((target) => target.type === 'page' && target.url.startsWith('http://localhost:5173/'));
if (!page) throw new Error('Local MDE-NG browser target not found');

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
let messageId = 0;
const pending = new Map();
socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++messageId;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const stages = ['Intake', 'Medical Triage', 'Scheduling', 'Clinical Evaluation', 'Internal QA', 'VA Delivery & Review'];
const outDir = path.resolve('screenshots');
await fs.mkdir(outDir, { recursive: true });
const report = [];
for (const stage of stages) {
  await send('Page.navigate', { url: `http://localhost:5173/?stage=${encodeURIComponent(stage)}` });
  await pause(900);
  const metrics = await send('Runtime.evaluate', { expression: `({ title: document.title, heading: document.querySelector('h1')?.textContent, width: innerWidth, scrollWidth: document.documentElement.scrollWidth, height: innerHeight, panels: document.querySelectorAll('.work-panel').length })`, returnByValue: true });
  const screenshot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const slug = stage.toLowerCase().replaceAll(' ', '-').replaceAll('&', 'and');
  await fs.writeFile(path.join(outDir, `${slug}.png`), Buffer.from(screenshot.data, 'base64'));
  report.push({ stage, ...metrics.result.value });
}
for (const width of [768, 390]) {
  await send('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1024, deviceScaleFactor: 1, mobile: width === 390 });
  for (const stage of ['Scheduling', 'Clinical Evaluation']) {
    await send('Page.navigate', { url: `http://localhost:5173/?stage=${encodeURIComponent(stage)}` });
    await pause(700);
    const metrics = await send('Runtime.evaluate', { expression: `({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, sidebarDrawer: getComputedStyle(document.querySelector('.sidebar')).position, panels: document.querySelectorAll('.work-panel').length })`, returnByValue: true });
    report.push({ stage: `${stage} responsive`, viewport: width, ...metrics.result.value });
  }
}
await send('Emulation.clearDeviceMetricsOverride');
await send('Page.navigate', { url: 'http://localhost:5173/?stage=Intake' });
await pause(700);
await send('Runtime.evaluate', { expression: `document.querySelector('.workspace-heading .btn').click()` });
await pause(150);
let stateShot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
await fs.writeFile(path.join(outDir, 'intake-next-flow-toast.png'), Buffer.from(stateShot.data, 'base64'));
await send('Page.navigate', { url: `http://localhost:5173/?stage=${encodeURIComponent('Clinical Evaluation')}` });
await pause(700);
await send('Runtime.evaluate', { expression: `document.querySelector('.segmented.left button').click()` });
await pause(150);
stateShot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
await fs.writeFile(path.join(outDir, 'clinical-status-alignment.png'), Buffer.from(stateShot.data, 'base64'));
socket.close();
console.log(JSON.stringify(report, null, 2));
