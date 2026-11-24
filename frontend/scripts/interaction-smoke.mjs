const targets = await fetch('http://localhost:9222/json').then((response) => response.json());
const page = targets.find((target) => target.type === 'page' && target.url.startsWith('http://localhost:5173/'));
if (!page) throw new Error('Local MDE-NG browser target not found');
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
let id = 0; const pending = new Map();
socket.addEventListener('message', (event) => { const message = JSON.parse(event.data); if (!message.id || !pending.has(message.id)) return; const task = pending.get(message.id); pending.delete(message.id); message.error ? task.reject(new Error(message.error.message)) : task.resolve(message.result); });
const send = (method, params = {}) => new Promise((resolve, reject) => { const next = ++id; pending.set(next, { resolve, reject }); socket.send(JSON.stringify({ id: next, method, params })); });
const wait = (ms = 250) => new Promise((resolve) => setTimeout(resolve, ms));
const go = async (stage) => { await send('Page.navigate', { url: `http://localhost:5173/?stage=${encodeURIComponent(stage)}` }); await wait(550); };
const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result.value;
const results = [];
const check = (name, passed, detail) => { results.push({ name, passed: Boolean(passed), detail }); if (!passed) process.exitCode = 1; };

await go('Intake');
await evaluate(`document.querySelectorAll('.case-list>button')[1].click()`); await wait();
check('Intake selects an ESR in place', (await evaluate(`document.querySelector('.case-primary b').textContent`)).includes('ESR-10483'), 'Selected case header updated');

await go('Medical Triage');
await evaluate(`document.querySelectorAll('.choice-grid label')[1].click()`); await wait();
check('Triage updates DBQ mapping', (await evaluate(`document.querySelector('.plan-canvas .panel-title p').textContent`)).includes('2 DBQ'), 'Second DBQ added to plan');

await go('Scheduling');
await evaluate(`document.querySelectorAll('.examiner-list>button')[2].click()`); await wait();
check('Scheduling blocks an ineligible examiner', (await evaluate(`document.querySelector('.toast')?.textContent || ''`)).includes('training expired'), 'Eligibility reason shown');

await go('Clinical Evaluation');
await evaluate(`document.querySelectorAll('.editor-footer .btn')[1].click()`); await wait();
check('Clinical validation reports missing fields', (await evaluate(`document.querySelectorAll('.validation-box small').length`)) === 3, 'Three required responses identified');

await go('Internal QA');
check('QA blocks approval with mandatory finding', await evaluate(`document.querySelector('.findings-rail>.btn-primary').disabled`), 'Approval disabled');
await evaluate(`document.querySelectorAll('.qa-actions button')[1].click()`); await wait();
check('QA enables approval after required verification', !(await evaluate(`document.querySelector('.findings-rail>.btn-primary').disabled`)), 'Blocking finding verified');

await go('VA Delivery & Review');
await evaluate(`document.querySelector('.package-rail>.btn-primary').click()`); await wait();
check('Delivery distinguishes sent from confirmed receipt', (await evaluate(`document.querySelector('.delivery-status-banner h2').textContent`)).includes('awaiting confirmed VA receipt'), 'Sent state awaits response');
await evaluate(`document.querySelector('.response-button').click()`); await wait();
check('Delivery records a separate receipt confirmation', (await evaluate(`document.querySelector('.delivery-status-banner h2').textContent`)).includes('VA receipt confirmed'), 'Receipt state confirmed separately');

socket.close();
console.log(JSON.stringify(results, null, 2));
