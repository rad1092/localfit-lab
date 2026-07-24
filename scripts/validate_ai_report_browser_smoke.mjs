import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import fsSync from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const RULE_DIR = path.join(ROOT, 'datacorpus', '_rule_validation');
const DOC_DIR = path.join(ROOT, 'research', 'rule_validation');
const OUT_SUMMARY = path.join(RULE_DIR, '97_ai_report_browser_smoke_summary.json');
const OUT_CASES = path.join(RULE_DIR, '97_ai_report_browser_smoke_cases.csv');
const OUT_DOC = path.join(DOC_DIR, '97_ai_report_browser_smoke_20260707.md');
const OUT_SCREENSHOT = path.join(RULE_DIR, '97_ai_report_browser_smoke_result.png');
const VITE_LOG = path.join(ROOT, '.codex-browser-smoke-vite.log');
const API_LOG = path.join(ROOT, '.codex-browser-smoke-ai-report.log');
const CHROME_LOG = path.join(ROOT, '.codex-browser-smoke-chrome.log');

const VERSION = 'ai_report_browser_smoke.v0.1-20260707';
const VITE_PORT = 15173;
const API_PORT = 18787;
const CDP_PORT = 19222;
const PAGE_URL = `http://127.0.0.1:${VITE_PORT}/`;
const API_BASE = `http://127.0.0.1:${API_PORT}`;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function csvEscape(value) {
  const text = String(value ?? '');
  if (/[",\n\r]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

async function writeCsv(filePath, rows) {
  const columns = Object.keys(rows[0] || {});
  const body = [
    columns.join(','),
    ...rows.map((row) => columns.map((col) => csvEscape(row[col])).join(',')),
  ].join('\n');
  await fs.writeFile(filePath, `\uFEFF${body}\n`, 'utf8');
}

async function waitForHttp(url, label, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = '';
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return response;
      lastError = `${response.status} ${response.statusText}`;
    } catch (error) {
      lastError = error.message;
    }
    await sleep(500);
  }
  throw new Error(`${label} 응답 대기 실패: ${lastError}`);
}

function spawnLogged(command, args, options, logPath) {
  const log = createWriteStream(logPath, { flags: 'w' });
  const child = spawn(command, args, {
    cwd: ROOT,
    env: { ...process.env, ...options.env },
    shell: false,
    windowsHide: true,
  });
  child.stdout.pipe(log);
  child.stderr.pipe(log);
  child.on('exit', (code) => log.write(`\n[process exited code=${code}]\n`));
  return child;
}

async function killProcess(child) {
  if (!child || child.killed) return;
  try {
    child.kill();
  } catch {
    // cleanup best effort
  }
}

async function startServers() {
  const vite = spawnLogged(
    'C:\\WINDOWS\\System32\\cmd.exe',
    ['/c', 'npm.cmd', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', String(VITE_PORT), '--strictPort'],
    { env: { VITE_AI_REPORT_API_BASE: API_BASE } },
    VITE_LOG,
  );
  const api = spawnLogged(
    path.join(ROOT, '.venv-ai-report', 'Scripts', 'python.exe'),
    ['scripts\\ai_report_server.py', '--host', '127.0.0.1', '--port', String(API_PORT)],
    { env: { AI_REPORT_DRY_RUN: '1' } },
    API_LOG,
  );
  await waitForHttp(PAGE_URL, 'Vite');
  await waitForHttp(`${API_BASE}/api/ai-report/health`, 'AI report server');
  return { vite, api };
}

function chromePath() {
  const candidates = [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  ];
  return candidates.find((candidate) => {
    try {
      return Boolean(fsSync.existsSync(candidate));
    } catch {
      return false;
    }
  });
}

async function startChrome() {
  const executable = chromePath();
  if (!executable) throw new Error('Chrome 또는 Edge 실행 파일을 찾지 못했습니다.');
  const profileDir = path.join('C:\\tmp', `codex-ai-report-smoke-${Date.now()}`);
  await fs.mkdir(profileDir, { recursive: true });
  const chrome = spawnLogged(
    executable,
    [
      '--headless=new',
      '--disable-gpu',
      '--disable-background-networking',
      '--disable-crash-reporter',
      '--disable-crashpad',
      '--no-first-run',
      '--no-default-browser-check',
      `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${profileDir}`,
      'about:blank',
    ],
    { env: {} },
    CHROME_LOG,
  );
  await waitForHttp(`http://127.0.0.1:${CDP_PORT}/json/version`, 'Chrome CDP');
  return { chrome, profileDir };
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else resolve(message.result || {});
      } else {
        this.events.push(message);
      }
    });
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', reject, { once: true });
    });
  }

  send(method, params = {}) {
    const id = ++this.id;
    const payload = JSON.stringify({ id, method, params });
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.ws.send(payload);
    return promise;
  }

  async waitForEvent(method, timeoutMs = 30_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const index = this.events.findIndex((event) => event.method === method);
      if (index >= 0) return this.events.splice(index, 1)[0];
      await sleep(100);
    }
    throw new Error(`${method} 이벤트 대기 실패`);
  }

  close() {
    this.ws.close();
  }
}

async function createPage() {
  const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(PAGE_URL)}`, { method: 'PUT' });
  if (!response.ok) throw new Error(`CDP tab 생성 실패: ${response.status}`);
  const target = await response.json();
  const cdp = new CdpClient(target.webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  return cdp;
}

async function evaluate(cdp, expression, timeoutMs = 30_000) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
    timeout: timeoutMs,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || JSON.stringify(result.exceptionDetails));
  }
  return result.result?.value;
}

async function runBrowserSmoke(cdp) {
  await evaluate(cdp, `
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 30000;
      const tick = () => {
        if (document.readyState === 'complete' && document.querySelector('[data-action="open-ai-report"]')) resolve(true);
        else if (Date.now() > deadline) reject(new Error('초기 화면 준비 실패'));
        else setTimeout(tick, 100);
      };
      tick();
    })
  `);

  await evaluate(cdp, `document.querySelector('[data-action="open-ai-report"]').click(); true;`);
  await evaluate(cdp, `
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 30000;
      const tick = () => {
        const modal = document.getElementById('aiReportModal');
        const service = document.getElementById('aiReportIndustryService');
        if (modal && modal.hidden === false && service && service.value === 'CS100001') resolve(true);
        else if (Date.now() > deadline) reject(new Error('AI 리포트 모달 또는 업종 기본 선택 준비 실패'));
        else setTimeout(tick, 100);
      };
      tick();
    })
  `);

  await evaluate(cdp, `
    const input = document.getElementById('aiReportLocation');
    input.value = '이태원';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('[data-action="search-ai-report-location"]').click();
    true;
  `);

  await evaluate(cdp, `
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 30000;
      const tick = () => {
        const candidate = document.querySelector('[data-action="select-ai-report-location"][data-code="3001491"]');
        if (candidate) resolve(true);
        else if (Date.now() > deadline) reject(new Error('이태원 관광특구 후보 검색 실패'));
        else setTimeout(tick, 100);
      };
      tick();
    })
  `);

  await evaluate(cdp, `
    document.querySelector('[data-action="select-ai-report-location"][data-code="3001491"]').click();
    true;
  `);

  const selected = await evaluate(cdp, `({
    tradeAreaCode: document.getElementById('aiReportTradeAreaCode').value,
    industryCode: document.getElementById('aiReportIndustryCode').value,
    locationValue: document.getElementById('aiReportLocation').value,
  })`);
  if (selected.tradeAreaCode !== '3001491') throw new Error(`상권 hidden code 불일치: ${selected.tradeAreaCode}`);
  if (selected.industryCode !== 'CS100001') throw new Error(`업종 hidden code 불일치: ${selected.industryCode}`);

  await evaluate(cdp, `document.getElementById('aiReportSubmit').click(); true;`);
  const report = await evaluate(cdp, `
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 90000;
      const tick = () => {
        const resultModal = document.getElementById('aiReportResultModal');
        const markdown = document.getElementById('aiReportMarkdown')?.innerText || '';
        const meta = document.getElementById('aiReportMeta')?.innerText || '';
        const error = document.getElementById('aiReportError')?.innerText || '';
        if (resultModal && resultModal.hidden === false && markdown.includes('상권 입지 상세 리포트')) {
          resolve({ meta, markdown, error });
        } else if (error) {
          reject(new Error(error));
        } else if (Date.now() > deadline) {
          reject(new Error('리포트 결과 대기 실패'));
        } else {
          setTimeout(tick, 200);
        }
      };
      tick();
    })
  `, 100_000);

  return { selected, report };
}

async function main() {
  await fs.mkdir(RULE_DIR, { recursive: true });
  await fs.mkdir(DOC_DIR, { recursive: true });

  let vite;
  let api;
  let chrome;
  let cdp;
  const cases = [];
  let summary;

  try {
    ({ vite, api } = await startServers());
    ({ chrome } = await startChrome());
    cdp = await createPage();
    await cdp.waitForEvent('Page.loadEventFired', 60_000).catch(() => null);
    const smoke = await runBrowserSmoke(cdp);
    const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
    await fs.writeFile(OUT_SCREENSHOT, Buffer.from(screenshot.data, 'base64'));

    cases.push({
      case_id: '97-C01',
      case_name: 'browser modal lookup submit smoke',
      observed: JSON.stringify(smoke),
      expected: 'trade_area_code=3001491, industry_code=CS100001, markdown rendered',
      result: 'PASS',
      reason_ko: '실제 Chrome headless에서 위치 후보와 업종 선택 후 dry-run 리포트가 렌더링되어야 한다.',
    });
    summary = {
      validation_version: VERSION,
      generated_at: new Date().toISOString().slice(0, 19),
      decision: 'AI_REPORT_BROWSER_SMOKE_PASS',
      pass_count: 1,
      fail_count: 0,
      page_url: PAGE_URL,
      api_base: API_BASE,
      screenshot: path.relative(ROOT, OUT_SCREENSHOT),
      outputs: {
        cases: path.relative(ROOT, OUT_CASES),
        summary: path.relative(ROOT, OUT_SUMMARY),
        doc: path.relative(ROOT, OUT_DOC),
      },
      reason_ko: 'AI 리포트 UI는 실제 브라우저에서 lookup 후보 선택, hidden code 확정, dry-run Markdown 렌더링까지 통과했다.',
    };
  } catch (error) {
    cases.push({
      case_id: '97-C01',
      case_name: 'browser modal lookup submit smoke',
      observed: error.stack || error.message,
      expected: 'browser interaction pass',
      result: 'FAIL',
      reason_ko: '실제 브라우저 클릭 검증이 실패하면 UI 입력 계약을 운영 통과로 보지 않는다.',
    });
    summary = {
      validation_version: VERSION,
      generated_at: new Date().toISOString().slice(0, 19),
      decision: 'AI_REPORT_BROWSER_SMOKE_FAIL',
      pass_count: 0,
      fail_count: 1,
      page_url: PAGE_URL,
      api_base: API_BASE,
      outputs: {
        cases: path.relative(ROOT, OUT_CASES),
        summary: path.relative(ROOT, OUT_SUMMARY),
        doc: path.relative(ROOT, OUT_DOC),
      },
      error: error.message,
      reason_ko: 'AI 리포트 UI 브라우저 smoke에서 실패 항목이 있어 수정이 필요하다.',
    };
  } finally {
    if (cdp) cdp.close();
    await killProcess(chrome);
    await killProcess(vite);
    await killProcess(api);
  }

  await writeCsv(OUT_CASES, cases);
  await fs.writeFile(OUT_SUMMARY, JSON.stringify(summary, null, 2), 'utf8');
  const doc = `# 97. AI 리포트 브라우저 smoke 검증

## 목적

96번은 서버 lookup과 프론트 코드 계약을 정적으로 확인했다.  
이번 검증은 실제 Chrome headless에서 모달을 열고, 위치 후보를 검색/선택하고, 업종 코드를 확정한 뒤 dry-run 리포트가 렌더링되는지 본다.

## 검증 결과

- validation version: \`${VERSION}\`
- decision: \`${summary.decision}\`
- PASS: \`${summary.pass_count}\`
- FAIL: \`${summary.fail_count}\`
- page url: \`${PAGE_URL}\`
- api base: \`${API_BASE}\`

## 사례

| case_id | case_name | observed | expected | result | reason_ko |
| --- | --- | --- | --- | --- | --- |
${cases.map((row) => `| ${row.case_id} | ${row.case_name} | ${String(row.observed).replace(/\n/g, ' ')} | ${row.expected} | ${row.result} | ${row.reason_ko} |`).join('\n')}

## 판단

${summary.reason_ko}

## 산출물

- \`${path.relative(ROOT, OUT_CASES)}\`
- \`${path.relative(ROOT, OUT_SUMMARY)}\`
- \`${path.relative(ROOT, OUT_DOC)}\`
${summary.screenshot ? `- \`${summary.screenshot}\`` : ''}
`;
  await fs.writeFile(OUT_DOC, `\uFEFF${doc}`, 'utf8');

  console.log(JSON.stringify(summary, null, 2));
  if (summary.fail_count > 0) process.exitCode = 1;
}

main();
