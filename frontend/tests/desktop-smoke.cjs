const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { _electron } = require('playwright');

const packaged = process.env.IMAGEAI_ELECTRON_EXECUTABLE;

test('packaged desktop carries a real offline batch through its backend, gallery and session dialogs', { timeout: 60000 }, async () => {
  assert.ok(packaged && path.isAbsolute(packaged), 'Set IMAGEAI_ELECTRON_EXECUTABLE to the packaged executable.');
  const scratch = await fs.mkdtemp(path.join(process.env.IMAGEAI_TEST_DATA_DIR || os.tmpdir(), 'imageai-desktop-test-'));
  const profile = path.join(process.env.IMAGEAI_TEST_PROFILES || scratch, path.basename(scratch));
  const exportFolder = path.join(scratch, 'export');
  const singleFolder = path.join(scratch, 'single');
  const saved = path.join(scratch, 'saved-session.json');
  const relocated = path.join(scratch, 'relocated');
  await fs.mkdir(exportFolder);
  await fs.mkdir(singleFolder);
  let app;
  try {
    app = await _electron.launch({
      executablePath: packaged,
      cwd: scratch,
      args: [`--user-data-dir=${profile}`],
      env: { ...process.env, IMAGEAI_DATA_DIR: path.join(scratch, 'workspace') },
    });
    // Keep the real isolated IPC handlers. Supply deterministic choices at the OS-dialog boundary.
    await app.evaluate(({ dialog, clipboard }, { exportFolder, saved }) => {
      globalThis.testChoices = { directory: exportFolder, file: saved, clipboard: null, options: [] };
      dialog.showOpenDialog = async (_, options) => {
        globalThis.testChoices.options.push(options);
        if (options.properties.includes('openDirectory')) return { canceled: false, filePaths: [globalThis.testChoices.directory] };
        if (options.properties.includes('multiSelections')) return { canceled: false, filePaths: globalThis.testChoices.references };
        return { canceled: false, filePaths: [globalThis.testChoices.file] };
      };
      dialog.showSaveDialog = async () => ({ canceled: false, filePath: saved });
      clipboard.writeImage = image => { globalThis.testChoices.clipboard = image.getSize(); };
    }, { exportFolder, saved });
    const page = await app.firstWindow();
    page.setDefaultTimeout(8000);
    await page.getByRole('heading', { name: 'Studio', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => typeof window.require), 'undefined');

    await page.getByRole('button', { name: 'Add rows', exact: true }).click();
    await page.getByLabel('Blank rows to add').fill('1');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByRole('button', { name: 'Preflight', exact: true }).click();
    await page.getByText(/Prompt is empty/).waitFor();

    await page.getByRole('button', { name: 'Batch Import', exact: true }).click();
    await page.getByRole('button', { name: 'CSV', exact: true }).click();
    await page.getByLabel('Batch input data').fill('prompt_id,category_id,prompt,campaign\nqa-01,brand,"A circle, blue",test');
    await page.getByLabel('Append to every parsed prompt').fill('White background.');
    await page.getByRole('combobox').filter({ has: page.getByRole('option', { name: 'Replace all rows', exact: true }) }).selectOption('replace');
    await page.getByRole('button', { name: 'Update Preview', exact: true }).click();
    page.once('dialog', d => d.accept());
    await page.getByRole('button', { name: 'Import 1 Rows', exact: true }).click();
    await page.getByText('brand / qa-01', { exact: true }).waitFor();

    const wrapper = page.getByLabel('Shared prompt instructions', { exact: true });
    await wrapper.fill('Center the shape.');
    await wrapper.press('Tab');
    const count = page.getByLabel('Images per prompt', { exact: true });
    await count.fill('2');
    await count.press('Tab');
    await page.getByRole('button', { name: 'Generate Missing', exact: true }).click();
    await page.getByText('2 images', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Generate Missing', exact: true }).click();
    await page.getByText('No eligible rows needed generation.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Verify Batch', exact: true }).click();
    await page.getByText(/Batch verification.*2 saved images/).waitFor();

    await page.getByRole('button', { name: 'Portable export', exact: true }).click();
    await page.getByText('Portable export: 2 images · 0 missing files', { exact: true }).waitFor().catch(async error => {
      const alert = (await page.getByRole('alert').allTextContents()).join(' ');
      throw new Error(`Portable export did not finish. ${alert}`, { cause: error });
    });
    const [bundle] = await fs.readdir(exportFolder);
    await fs.cp(path.join(exportFolder, bundle), relocated, { recursive: true });
    const manifest = JSON.parse(await fs.readFile(path.join(relocated, 'manifest.json'), 'utf8'));
    assert.equal(manifest.image_count, 2);
    const originalSession = JSON.parse(await fs.readFile(path.join(relocated, 'session.json'), 'utf8'));
    const [row] = originalSession.rows;
    assert.equal(row.prompt_id, 'qa-01');
    assert.equal(row.source_metadata.campaign, 'test');
    assert.match(row.images[0].metadata.full_prompt, /White background\.\s+Center the shape\./);
    const originalImagePaths = row.images.map(image => path.join(relocated, image.file_path));

    await app.evaluate((_, folder) => { globalThis.testChoices.directory = folder; }, singleFolder);
    await page.getByRole('button', { name: 'Gallery Grid', exact: true }).click();
    await page.getByRole('button', { name: /^View A circle, blue/ }).first().click();
    const fullImage = page.getByAltText('Full size', { exact: true });
    await fullImage.waitFor();
    await page.waitForFunction(() => document.querySelector('img[alt="Full size"]')?.naturalWidth === 1024);
    await page.getByRole('button', { name: 'Copy image', exact: true }).click();
    await page.getByText('Image copied.', { exact: true }).waitFor();
    assert.deepEqual(await app.evaluate(() => globalThis.testChoices.clipboard), { width: 1024, height: 1024 });
    await page.getByRole('button', { name: 'Export this image', exact: true }).click();
    await page.getByText('Exported 1 images.', { exact: true }).waitFor();
    const metadata = JSON.parse(await fs.readFile(path.join(singleFolder, 'export_metadata.json'), 'utf8'));
    assert.equal(metadata.length, 1);
    assert.equal(metadata[0].prompt_id, 'qa-01');
    await page.keyboard.press('Escape');

    const saving = page.waitForResponse(response => response.request().postDataJSON()?.action === 'save_session');
    await page.getByTitle('Save Session As', { exact: true }).click();
    assert.ok((await saving).ok());
    assert.equal(JSON.parse(await fs.readFile(saved, 'utf8')).rows.length, 1);

    page.once('dialog', d => d.accept());
    await page.getByTitle('New Session', { exact: true }).click();
    await page.getByText('Connected • 0 Rows', { exact: true }).waitFor();
    await app.evaluate((_, file) => { globalThis.testChoices.file = file; }, path.join(relocated, 'session.json'));
    await page.getByTitle('Open Session', { exact: true }).click();
    await page.getByText('Connected • 1 Rows', { exact: true }).waitFor();

    await page.getByRole('button', { name: 'List View', exact: true }).click();
    await page.getByLabel('Provider', { exact: true }).selectOption('openai');
    await app.evaluate((_, references) => { globalThis.testChoices.references = references; }, originalImagePaths);
    await page.getByTitle(/^Attach reference image/).click();
    await page.getByRole('button', { name: 'Clear references', exact: true }).waitFor();
    assert.equal(await page.getByTitle('Remove attachment', { exact: true }).count(), 2);
    await page.getByRole('button', { name: 'Clear references', exact: true }).click();
    await page.getByRole('button', { name: 'Clear references', exact: true }).waitFor({ state: 'hidden' });
    await page.getByLabel('Provider', { exact: true }).selectOption('mock');

    await page.getByRole('button', { name: 'Templates', exact: true }).click();
    await page.getByLabel('Template String', { exact: false }).fill('A {shape}');
    await page.getByLabel('Variables', { exact: false }).fill('shape: square, triangle');
    await page.getByRole('button', { name: 'Preview Expansion' }).click();
    await page.getByText('1. A square', { exact: true }).waitFor();
    await page.getByLabel('Variables', { exact: false }).fill('shape: hexagon');
    await page.getByRole('button', { name: 'Save & Add Rows', exact: true }).click();
    await page.getByPlaceholder('Enter prompt...').nth(1).waitFor();
    assert.equal(await page.getByPlaceholder('Enter prompt...').nth(1).inputValue(), 'A hexagon');
  } finally {
    if (app) await app.close();
    await fs.rm(profile, { recursive: true, force: true });
    await fs.rm(scratch, { recursive: true, force: true });
  }
});
