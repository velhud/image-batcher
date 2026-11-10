const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('node:http');
const { readFile, stat } = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

let browser, server, baseUrl;
before(async () => {
  const root = path.resolve(__dirname, '../dist');
  await stat(path.join(root, 'index.html'));
  server = createServer(async (req, res) => {
    try {
      const name = new URL(req.url, 'http://localhost').pathname;
      const file = path.resolve(root, '.' + (name === '/' ? '/index.html' : name));
      if (!file.startsWith(root + path.sep)) { res.writeHead(403); res.end(); return; }
      const body = await readFile(file);
      const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': mime }); res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
});
after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

function fixture() {
  const sizes = { 'Square 1024': { width: 1024, height: 1024 }, Portrait: { width: 1024, height: 1536 }, Landscape: { width: 1536, height: 1024 } };
  const caps = { max_images: 4, supports_style: false, supports_negative_prompt: false, supports_seed: false, supports_quality: false, supports_safety: false,
    supports_attachments: false, max_attachments: 0, supports_custom_size: true, output_formats: ['png'], supports_background: false, quality_options: [], size_presets: sizes };
  return {
    session: { id: 'test-session', global_settings: { provider_id: 'mock', model_id: 'mock-standard', size_preset: 'Square 1024', custom_size: null, num_images: 1,
      style_preset: 'None', negative_prompt: '', seed: null, random_seed: true, quality: 5, safety: 2, naming_pattern: '{index}_{slug}.png', concurrency_limit: 2,
      generate_behavior: 'keep', regen_use_same_seed: true, confirm_generate_threshold: 300, rate_limit_rpm: 60,
      thinking_budget: 0, thinking_level: 'high', prompt_wrapper: '', output_format: 'png', background: 'auto' },
      templates: [], rows: [{ id: 'row-1', prompt: 'An amber glass bottle', prompt_id: 'bottle-01', category_id: 'catalogue', source_metadata: {}, status: 'Idle',
        error_message: '', selected: false, settings: {}, attachments: [], images: [], tags: [] }] },
    providers: [{ id: 'mock', name: 'Mock', models: [{ id: 'mock-standard', name: 'Mock Standard', provider_id: 'mock', capabilities: caps }] },
      { id: 'openai', name: 'OpenAI', models: [{ id: 'gpt-image-2.5-flare', name: 'GPT Image 2.5 Flare', provider_id: 'openai', capabilities: { ...caps, max_images: 10,
        supports_quality: true, quality_options: ['low', 'medium', 'high'], supports_attachments: true, max_attachments: 16, supports_background: true, output_formats: ['png', 'jpeg', 'webp'] } }] }],
    stats: { total: 1, completed: 0, errors: 0, average_duration: 0 },
  };
}

async function openWorkspace(options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const state = fixture();
  options.prepare?.(state);
  const calls = [];
  await page.route('http://127.0.0.1:8765/**', async route => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'content-type' } });
    const request = route.request().postDataJSON();
    calls.push(request);
    const { action, data = {} } = request;
    let result;
    let status = 200;
    if (options.rejectAction === action) {
      return route.fulfill({ status: 409, headers: { 'Access-Control-Allow-Origin': '*' }, json: { ok: false, error: 'The active row must finish before this change.' } });
    }
    switch (action) {
      case 'state': result = state; break;
      case 'provider_status': result = { azure_openai: { configured: false, missing: ['AZURE_OPENAI_ENDPOINT'], endpoint: '', deployment: '', api_version: 'preview' }, azure_logo_wrapper: '' }; break;
      case 'global_settings': Object.assign(state.session.global_settings, data); result = state.session.global_settings; break;
      case 'update_row': {
        const row = state.session.rows.find(item => item.id === data.row_id);
        if (data.settings) Object.assign(row.settings, data.settings);
        Object.assign(row, Object.fromEntries(Object.entries(data).filter(([key]) => key !== 'settings' && key !== 'row_id')));
        result = row; break;
      }
      case 'cancel_row': state.session.rows.find(row => row.id === data.row_id).status = 'Cancelled'; result = { cancelled: true }; break;
      case 'preflight': result = { valid: !options.invalid, rows: state.session.rows.map(row => ({ row_id: row.id, prompt_id: row.prompt_id, provider: 'mock', model: 'mock-standard', requested_images: 1,
        errors: options.invalid ? ['Reference file is missing.'] : [] })), total_requests: 0, total_images: 1 }; break;
      case 'generate_rows': result = { queued: data.row_ids }; break;
      case 'select_folder': result = { path: '/tmp/imageai-export' }; break;
      case 'export_bundle': result = { path: '/tmp/imageai-export/bundle', exported: 1, missing: [], manifest_path: '/tmp/imageai-export/bundle/manifest.json', contact_sheet: '/tmp/imageai-export/bundle/contact-sheet.png' }; break;
      case 'parse_batch': result = { prompts: ['Bottle. ' + data.prompt_append], rows: [{ prompt: 'Bottle. ' + data.prompt_append, prompt_id: '', category_id: '', source_metadata: {} }], errors: [] }; break;
      case 'import_batch': result = { rows: [], errors: [] }; break;
      default: result = {};
    }
    return route.fulfill({ status, headers: { 'Access-Control-Allow-Origin': '*' }, json: { ok: true, data: result } });
  });
  await page.addInitScript(() => {
    window.imageaiDesktop = {
      backendOrigin: async () => 'http://127.0.0.1:8765',
      selectFolder: async () => '/tmp/imageai-export',
    };
  });
  await page.goto(baseUrl);
  await page.getByRole('heading', { name: 'Studio', exact: true }).waitFor();
  return { page, state, calls, close: () => context.close() };
}

test('provider changes expose supported controls and preserve requested custom dimensions', async () => {
  const workspace = await openWorkspace({ prepare: state => {
    state.session.global_settings.thinking_budget = 512;
    state.session.global_settings.thinking_level = 'low';
  } });
  const { page, calls } = workspace;
  try {
    assert.equal(await page.getByLabel('Quality', { exact: true }).count(), 0);
    await page.getByLabel('Provider', { exact: true }).selectOption('openai');
    await page.getByLabel('Quality', { exact: true }).waitFor();
    assert.ok(calls.some(call => call.action === 'global_settings' && call.data.thinking_budget === 0 && call.data.thinking_level === 'high'));
    assert.equal(await page.getByLabel('Images per prompt', { exact: true }).getAttribute('max'), '10');
    await page.getByLabel('Output format', { exact: true }).selectOption('jpeg');
    await page.waitForFunction(() => document.querySelector('#global-background')?.value === 'opaque');
    assert.equal(await page.locator('#global-background option[value="transparent"]').isDisabled(), true);
    await page.getByLabel('Image dimensions', { exact: true }).selectOption('Custom');
    await page.getByLabel('Width', { exact: true }).fill('1536');
    const savedDimensions = page.waitForResponse(response => {
      const payload = response.request().postDataJSON();
      return payload?.action === 'global_settings' && payload.data?.custom_size?.width === 1536;
    });
    await page.getByLabel('Width', { exact: true }).press('Tab');
    await savedDimensions;
    assert.ok(calls.some(call => call.action === 'global_settings' && call.data.custom_size?.width === 1536));
    assert.equal(await page.getByText('Reasoning (Thinking)', { exact: true }).count(), 0);
    await page.getByTitle('Row Settings', { exact: true }).click();
    const savedRowModel = page.waitForResponse(response => {
      const payload = response.request().postDataJSON();
      return payload?.action === 'update_row' && payload.data?.settings?.provider_id === 'openai';
    });
    await page.getByRole('group', { name: 'Row overrides', exact: true }).getByLabel('Provider', { exact: true }).selectOption('openai');
    await savedRowModel;
    assert.ok(calls.some(call => call.action === 'update_row' && call.data.settings?.thinking_budget === 0 && call.data.settings?.thinking_level === 'high'));
  } finally { await workspace.close(); }
});

test('row stop cancels only that row', async () => {
  const workspace = await openWorkspace({ prepare: state => { state.session.rows[0].status = 'Generating'; } });
  try {
    await workspace.page.getByRole('button', { name: 'Stop Row', exact: true }).click();
    await workspace.page.getByText('Cancelled', { exact: true }).waitFor();
    assert.ok(workspace.calls.some(call => call.action === 'cancel_row' && call.data.row_id === 'row-1'));
    assert.equal(workspace.calls.some(call => call.action === 'stop_all'), false);
  } finally { await workspace.close(); }
});

test('invalid preflight shows the row error and makes no generation request', async () => {
  const workspace = await openWorkspace({ invalid: true });
  try {
    await workspace.page.getByRole('button', { name: 'Generate All', exact: true }).click();
    await workspace.page.getByText('bottle-01: Reference file is missing.', { exact: true }).waitFor();
    assert.equal(workspace.calls.some(call => call.action === 'generate_rows'), false);
  } finally { await workspace.close(); }
});

test('portable export exposes output location and manifest contents', async () => {
  const workspace = await openWorkspace();
  try {
    await workspace.page.getByRole('button', { name: 'Portable export', exact: true }).click();
    await workspace.page.getByText('Portable export: 1 images · 0 missing files', { exact: true }).waitFor();
    assert.match(await workspace.page.getByRole('region', { name: 'Batch results', exact: true }).textContent(), /session, images, references, manifest, and contact sheet/);
    assert.ok(workspace.calls.some(call => call.action === 'export_bundle' && call.data.row_ids[0] === 'row-1'));
  } finally { await workspace.close(); }
});

test('batch append is previewed and imported; editing invalidates the preview', async () => {
  const workspace = await openWorkspace();
  const { page, calls } = workspace;
  try {
    await page.getByRole('button', { name: 'Batch Import', exact: true }).click();
    await page.getByLabel('Batch input data').fill('Bottle.');
    await page.getByLabel('Append to every parsed prompt').fill('White background.');
    await page.getByRole('button', { name: 'Update Preview', exact: true }).click();
    await page.getByRole('button', { name: 'Import 1 Rows', exact: true }).waitFor();
    await page.getByLabel('Append to every parsed prompt').fill('Blue background.');
    assert.equal(await page.getByRole('button', { name: 'Import 1 Rows', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'Update Preview', exact: true }).click();
    await page.getByRole('button', { name: 'Import 1 Rows', exact: true }).click();
    await page.getByRole('heading', { name: 'Batch Input', exact: true }).waitFor({ state: 'hidden' });
    assert.ok(calls.some(call => call.action === 'parse_batch' && call.data.prompt_append === 'White background.'));
    assert.ok(calls.some(call => call.action === 'import_batch' && call.data.prompt_append === 'Blue background.'));
  } finally { await workspace.close(); }
});

test('HTTP conflict preserves the backend explanation', async () => {
  const workspace = await openWorkspace({ rejectAction: 'global_settings' });
  try {
    await workspace.page.getByLabel('Provider', { exact: true }).selectOption('openai');
    await workspace.page.getByRole('alert').filter({ hasText: 'The active row must finish before this change.' }).waitFor();
    assert.equal(await workspace.page.getByText('HTTP Error: 409', { exact: true }).count(), 0);
  } finally { await workspace.close(); }
});

test('interrupted work is visible and a declined retry makes no request', async () => {
  const workspace = await openWorkspace({ prepare: state => { state.session.rows[0].status = 'Interrupted'; state.session.rows[0].error_message = 'Previous provider outcome is unknown.'; } });
  try {
    await workspace.page.getByText('Interrupted', { exact: true }).waitFor();
    const declinedRetry = workspace.page.waitForEvent('dialog').then(dialog => dialog.dismiss());
    await workspace.page.getByRole('button', { name: 'Review & Retry', exact: true }).click();
    await declinedRetry;
    assert.equal(workspace.calls.some(call => call.action === 'generate_rows'), false);
  } finally { await workspace.close(); }
});
