const {test, before, after, beforeEach, afterEach} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, expect} = require('playwright/test');

const baseURL = process.env.SOBHE_BROWSER_URL;
if (!baseURL) throw new Error('Run with python scripts/run_browser_tests.py to use disposable application data.');
let browser, context, page, errors;
before(async () => { browser = await chromium.launch({headless: true, channel: process.env.SOBHE_BROWSER_CHANNEL || undefined}); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
    context = await browser.newContext({baseURL, reducedMotion: 'no-preference'});
    page = await context.newPage();
    page.setDefaultTimeout(10000);
    errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', async dialog => {
        errors.push(`Unexpected browser ${dialog.type()} dialog`);
        await dialog.dismiss();
    });
});
afterEach(async () => {
    await context?.close();
    assert.deepEqual(errors, [], 'The page raised a JavaScript error');
});

async function login() {
    await page.goto('/login');
    await page.locator('#username').fill('administrator');
    await page.locator('#password').fill('test-password-123');
    await Promise.all([page.waitForURL('/admin/advertisements'), page.locator('.auth form button').click()]);
}

test('table controls paginate, search all rows, sort numbers, and reinitialize after navigation', async () => {
    await page.goto('/');
    await page.evaluate(() => {
        const section = document.createElement('section');
        section.id = 'table-test';
        const rows = Array.from({length: 32}, (_, index) => `<tr><td>رسانه ${index + 1}</td><td>${index + 1}</td></tr>`).join('');
        section.innerHTML = `<div class="table-wrap"><table><thead><tr><th>رسانه</th><th>حجم</th></tr></thead><tbody>${rows}</tbody></table></div>`;
        document.querySelector('main').append(section);
        document.dispatchEvent(new Event('sobhe:page-render'));
        document.dispatchEvent(new Event('sobhe:page-render'));
    });
    const table = page.locator('#table-test');
    await expect(table.locator('.table-controls')).toHaveCount(1);
    await expect(table.locator('tbody tr:visible')).toHaveCount(15);
    await table.getByRole('button', {name: 'بعدی', exact: true}).click();
    await expect(table.locator('tbody tr:visible').first()).toContainText('رسانه 16');
    await table.getByRole('button', {name: 'حجم'}).click();
    await expect(table.locator('tbody tr:visible').first()).toContainText('رسانه 1');
    await table.getByRole('button', {name: 'حجم'}).click();
    await expect(table.locator('tbody tr:visible').first()).toContainText('رسانه 32');
    await expect(table.locator('th').nth(1)).toHaveAttribute('aria-sort', 'descending');
    await table.getByRole('searchbox').fill('رسانه ۲۷');
    await expect(table.locator('tbody tr:visible')).toHaveCount(1);
    await expect(table.locator('tbody tr:visible')).toContainText('رسانه 27');
    await table.getByRole('searchbox').fill('missing');
    await expect(table.locator('tbody tr:visible')).toHaveText('نتیجه‌ای پیدا نشد.');
    await table.getByRole('searchbox').fill('');
    await table.locator('select').selectOption('30');
    await expect(table.locator('tbody tr:visible')).toHaveCount(30);
    await capture('table-controls');
});
async function markShell() {
    await page.evaluate(() => {
        window.testShell = {
            header: document.querySelector('.site-header'),
            sidebar: document.getElementById('side-navigation'),
            main: document.getElementById('main-content'),
        };
        document.addEventListener('sobhe:navigation-end', () => {
            window.testAnimationCount = window.testShell.main.getAnimations().length;
            window.testAnimationDurations = window.testShell.main.getAnimations().map(animation => animation.effect.getTiming().duration);
        });
    });
}
async function expectShell() {
    assert.equal(await page.evaluate(() => window.testShell.header === document.querySelector('.site-header')
        && window.testShell.sidebar === document.getElementById('side-navigation')
        && window.testShell.main === document.getElementById('main-content')), true);
}
async function navigateByLink(selector, destination) {
    await page.locator(selector).first().click();
    await page.waitForURL(destination);
    await expect(page.locator('#main-content')).toHaveAttribute('aria-busy', 'false');
}
async function readyWorker() {
    await page.evaluate(async () => {
        await navigator.serviceWorker.ready;
        if (!navigator.serviceWorker.controller) {
            await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, {once: true}));
        }
    });
}
async function capture(name) {
    if (!process.env.SOBHE_BROWSER_ARTIFACTS) return;
    await expect(page.locator('#navigation-progress')).toBeHidden();
    fs.mkdirSync('outputs/browser', {recursive: true});
    const scroll = await page.evaluate(() => [scrollX, scrollY]);
    await page.evaluate(async () => {
        await Promise.all(document.getAnimations()
            .filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime))
            .map(animation => animation.finished.catch(() => {})));
        window.scrollTo(0, 0);
        await new Promise(requestAnimationFrame);
    });
    await page.screenshot({path: `outputs/browser/${name}.png`, fullPage: true});
    await page.evaluate(position => window.scrollTo(...position), scroll);
}

async function confirmAction(trigger, accept = true, captureName) {
    await trigger.click();
    await expect(page.locator('#confirmation-dialog')).toBeVisible();
    await expect(page.locator('#confirmation-cancel')).toBeFocused();
    if (captureName) await capture(captureName);
    await page.locator(accept ? '#confirmation-accept' : '#confirmation-cancel').click();
    await expect(page.locator('#confirmation-dialog')).toBeHidden();
}

test('navigation keeps the shell, shows progress, and reinitializes filters and print controls', async () => {
    await page.goto('/');
    await markShell();
    let release;
    await page.route('**/reports', async route => {
        await new Promise(resolve => { release = resolve; });
        await route.continue();
    });
    await page.locator('.sidebar-links a[href="/reports"]').click();
    await expect(page.locator('#navigation-progress')).toBeVisible();
    await expect(page.locator('#main-content')).toHaveAttribute('aria-busy', 'true');
    await expect(page.locator('.sidebar-links a[href="/reports"]')).not.toHaveClass(/is-loading/);
    await expect(page.locator('.sidebar-links a[href="/reports"]')).not.toHaveAttribute('aria-busy','true');
    await expect.poll(() => typeof release).toBe('function');
    release();
    await page.waitForURL('/reports');
    await expect(page.locator('h1')).toHaveText('گزارش آگهی‌ها');
    await expectShell();
    assert.equal(await page.evaluate(() => window.testAnimationCount), 1);
    assert.ok((await page.evaluate(() => window.testAnimationDurations)).every(duration => duration >= 300));
    await capture('desktop-reports');
    await expect(page.locator('.sidebar-links a[href="/reports"]')).toHaveAttribute('aria-current', 'page');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'main-content');
    await expect(page.locator('.report-search')).not.toHaveAttribute('open');
    await page.locator('.report-search > summary').click();
    await page.locator('.filter-disclosure:not(.column-selector) > summary').click();
    await page.locator('.column-selector > summary').click();
    await expect(page.locator('#report-start')).toHaveClass('jalali-input');
    await page.locator('[data-select-all="columns"]').click();
    assert.equal(await page.locator('[name="columns"]:checked').count(), await page.locator('[name="columns"]').count());
    await page.locator('#ma-min').fill('7001');
    await page.locator('.report-filters form .actions button').click();
    await page.waitForURL(url => url.searchParams.get('ma_min') === '7001');
    await expect(page.locator('#ma-min')).toHaveValue('7001');
    await expectShell();
    await page.evaluate(() => { window.print = () => { window.testPrinted = true; }; });
    await page.locator('[data-print]').click();
    assert.equal(await page.evaluate(() => window.testPrinted), true);
});

test('forms loaded through navigation retain allocation previews, review stale tokens, and upload once', async () => {
    await login();
    await markShell();
    await navigateByLink('.sidebar-links a[href="/admin/advertisements"]', '/admin/advertisements');
    await navigateByLink('a[href="/admin/advertisements/new"]', '/admin/advertisements/new');
    await expectShell();
    await page.locator('[data-allocation-details] > summary').click();
    await page.locator('#ma_reference').fill('8101');
    await page.locator('#organization_id').selectOption({label: 'راه و شهرسازی'});
    await page.locator('#allocation_date').fill('۱۴۰۵/۰۷/۰۹');
    const initialPreview = page.waitForResponse(response => response.url().includes('/api/allocation-preview'));
    await page.locator('#volume').fill('10');
    const previewResponse = await initialPreview;
    assert.equal(previewResponse.status(), 200, await previewResponse.text());
    await expect(page.locator('#preview-token')).not.toHaveValue('');
    await expect(page.locator('#save-ad')).toBeEnabled();
    const initialData = await previewResponse.json();
    await expect(page.locator('#selected-group-target')).toContainText(`${initialData.groups[initialData.selected.category].target_percent}٪`);
    await expect(page.locator('#selected-group-projected')).not.toHaveText('—');
    await expect(page.locator('#recommended-target')).toHaveCount(0);
    await page.evaluate(() => { document.getElementById('preview-token').value = 'stale-preview'; });
    if (await page.locator('#override-label').isVisible()) await page.locator('#override-confirmed').check();
    const stale = page.waitForResponse(response => response.url().endsWith('/advertisements/new') && response.request().method() === 'POST');
    await page.locator('#save-ad').click();
    const staleResponse = await stale;
    const staleHtml = await staleResponse.text();
    assert.equal(staleResponse.status(), 409, staleHtml.match(/<div class="alert[^>]*>[^<]+/g)?.join('\n'));
    await expect(page.locator('.alert.warning').first()).toContainText('پیش‌نمایش تازه شد');
    await expect(page.locator('#ma_reference')).toHaveValue('8101');
    await expect(page.locator('#preview-token')).not.toHaveValue('stale-preview');
    await expect(page.locator('#allocation_date')).toHaveClass('jalali-input');
    await expectShell();

    // A changed date must still trigger a new preview after replacing the form.
    await page.locator('#allocation_date').fill('۱۴۰۵/۰۷/۱۰');
    await expect(page.locator('#preview-token')).not.toHaveValue('');
    await expect(page.locator('#save-ad')).toBeEnabled();
    if (await page.locator('#override-label').isVisible()) await page.locator('#override-confirmed').check();
    await page.locator('#file').setInputFiles({name: 'browser.png', mimeType: 'image/png',
        buffer: fs.readFileSync(require.resolve('../../static/icons/icon-192.png'))});
    let posts = 0;
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/advertisements/new')) posts++; });
    await page.locator('#save-ad').click();
    await page.waitForURL(/\/advertisements\/\d+$/);
    await expect(page.locator('h1')).toContainText('8101');
    assert.equal(posts, 1);
    await expectShell();
    const download = page.waitForEvent('download');
    await page.locator('a[href$="/file"]').first().click();
    assert.equal((await download).suggestedFilename(), 'browser.png');
    await expectShell();
});

test('history restores scroll and delegated dialogs and group targets survive navigation', async () => {
    await login();
    await markShell();
    await navigateByLink('.sidebar-links a[href="/admin/advertisements"]', '/admin/advertisements');
    const trigger = page.locator('[data-delete-ad]').first();
    await trigger.click();
    await expect(page.locator('#ad-delete-dialog')).toBeVisible();
    await page.locator('#ad-delete-dialog [data-dialog-close]').last().click();
    await expect(page.locator('#ad-delete-dialog')).not.toBeVisible();
    await navigateByLink('.sidebar-links a[href="/reports"]', '/reports');
    await page.evaluate(() => window.scrollTo(0, 600));
    await page.waitForFunction(() => history.state?.sobheScroll?.[1] >= 590);
    const scroll = await page.evaluate(() => window.scrollY);
    // Keyboard activation avoids scrolling the sidebar into view before saving the position.
    await page.locator('.sidebar-links a[href="/admin/periods"]').focus();
    await page.keyboard.press('Enter');
    await page.waitForURL('/admin/periods');
    await page.goBack();
    await page.waitForURL('/reports');
    await expect(page.locator('#main-content')).toHaveAttribute('aria-busy', 'false');
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scroll);
    await page.goForward();
    await page.waitForURL('/admin/periods');
    await navigateByLink('a[href="/admin/periods/1"]', '/admin/periods/1');
    await expect(page.locator('[name^="share_"]')).toHaveCount(0);
    await expect(page.locator('[data-group-target="local"]')).toHaveText('هدف 50٪');
    await expect(page.locator('[data-group-target="national"]')).toHaveText('هدف 50٪');
    await expectShell();
});

test('failed requests retain edits, submitted mutations are not retried, and exports use native downloads', async () => {
    await login();
    await navigateByLink('.sidebar-links a[href="/reports"]', '/reports');
    await markShell();
    const download = page.waitForEvent('download');
    await page.locator('a[href*="export=xlsx"]').click();
    assert.equal((await download).suggestedFilename(), 'advertisement-report.xlsx');
    await expectShell();
    await navigateByLink('.sidebar-links a[href="/admin/catalog/organizations"]', '/admin/catalog/organizations');
    await page.locator('#new-name').fill('پیش‌نویس محفوظ');
    await page.route('**/reports', route => route.abort('failed'));
    await page.locator('.sidebar-links a[href="/reports"]').click();
    await expect(page.locator('[data-navigation-error]')).toBeVisible();
    await expect(page.locator('#new-name')).toHaveValue('پیش‌نویس محفوظ');
    assert.equal(new URL(page.url()).pathname, '/admin/catalog/organizations');
    let posts = 0;
    await page.route('**/admin/catalog/organizations', route => {
        if (route.request().method() === 'POST') { posts++; return route.abort('failed'); }
        return route.continue();
    });
    await page.locator('#new-name').press('Enter');
    await expect(page.locator('[data-navigation-error]')).toContainText('پیش از ارسال دوباره');
    assert.equal(posts, 1);
    await expect(page.locator('#new-name')).toHaveValue('پیش‌نویس محفوظ');
});

test('reduced motion and the mobile drawer keep working during client navigation', async () => {
    await page.setViewportSize({width: 390, height: 844});
    await page.emulateMedia({reducedMotion: 'reduce'});
    await page.goto('/');
    await markShell();
    await page.locator('#navigation-toggle').click();
    await expect(page.locator('html')).toHaveClass(/sidebar-open/);
    await page.locator('#sidebar-toggle').click();
    await expect(page.locator('html')).not.toHaveClass(/sidebar-open/);
    await expect(page.locator('#sidebar-backdrop')).toBeHidden();
    await expect(page.locator('body')).not.toHaveClass(/drawer-open/);
    await expect(page.locator('#navigation-toggle')).toBeFocused();
    await page.locator('#navigation-toggle').click();
    await expect(page.locator('#sidebar-toggle')).toHaveAttribute('aria-expanded', 'true');
    await navigateByLink('.sidebar-links a[href="/reports"]', '/reports');
    await expect(page.locator('html')).not.toHaveClass(/sidebar-open/);
    await expect(page.locator('#navigation-toggle')).toHaveAttribute('aria-expanded', 'false');
    assert.equal(await page.evaluate(() => document.getElementById('main-content').getAnimations().length), 0);
    assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById('navigation-progress'), '::after').animationName), 'none');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const bounds = await page.locator('.filtered-stats .stat').evaluateAll(nodes => nodes.map(node => {
        const value = node.getBoundingClientRect();
        const card = node.closest('.stat-card').getBoundingClientRect();
        return {left: value.left, right: value.right, cardLeft: card.left, cardRight: card.right};
    }));
    for (const value of bounds) assert.ok(value.left >= value.cardLeft && value.right <= value.cardRight, JSON.stringify(value));
    await expectShell();
    const yearBounds = await page.locator('#period-year').boundingBox();
    const buttonBounds = await page.locator('.period-controls button').boundingBox();
    assert.ok(Math.abs(yearBounds.y - buttonBounds.y) < 2, 'Year control and submit button should share one row');
    await capture('mobile-reports');
});

test('the worker caches public assets, keeps records out, and recovers from an offline launch', async () => {
    await login();
    await readyWorker();
    await navigateByLink('.sidebar-links a[href="/reports"]', '/reports');
    const cached = await page.evaluate(async () => {
        const names = (await caches.keys()).filter(name => name.startsWith('sobhe-pwa-'));
        return Promise.all(names.map(async name => ({name,
            urls: (await (await caches.open(name)).keys()).map(request => new URL(request.url).pathname)})));
    });
    assert.equal(cached.length, 1);
    assert.ok(cached[0].urls.includes('/offline'));
    assert.ok(cached[0].urls.includes('/static/js/allocation.js'));
    assert.ok(cached[0].urls.every(url => url === '/offline' || url === '/manifest.webmanifest' || url.startsWith('/static/')));
    await context.setOffline(true);
    await expect(page.locator('#pwa-connection')).toBeVisible();
    await page.locator('.sidebar-links a[href="/"]').click();
    await expect(page.locator('[data-navigation-error]')).toBeVisible();
    await expect(page.locator('h1')).toHaveText('گزارش آگهی‌ها');
    await page.reload();
    await expect(page.locator('h1')).toHaveText('اتصال اینترنت برقرار نیست');
    await expect(page.locator('[name="csrf_token"]')).toHaveCount(0);
    await capture('offline');
    assert.equal(await page.evaluate(() => new FontFace('offline-font', 'url(/static/fonts/woff2/IRANYekanWebRegular.woff2)').load().then(() => true)), true);
    await context.setOffline(false);
    await page.locator('[data-pwa-retry]').click();
    await expect(page.locator('h1')).toHaveText('گزارش آگهی‌ها');
});

test('installation consumes the browser prompt once and hides after installation', async () => {
    await page.goto('/');
    await page.evaluate(() => {
        const event = new Event('beforeinstallprompt', {cancelable: true});
        event.prompt = async () => { window.testPromptCount = (window.testPromptCount || 0) + 1; };
        event.userChoice = Promise.resolve({outcome: 'dismissed'});
        window.dispatchEvent(event);
    });
    await expect(page.locator('#pwa-install')).toBeVisible();
    await page.locator('#pwa-install').click();
    assert.equal(await page.evaluate(() => window.testPromptCount), 1);
    await expect(page.locator('#pwa-install')).toBeHidden();
    await page.evaluate(() => window.dispatchEvent(new Event('appinstalled')));
    await expect(page.locator('#pwa-install')).toBeHidden();
});

test('an account change in another tab refreshes the complete page shell', async () => {
    await login();
    await markShell();
    const csrf = await page.locator('.account [name="csrf_token"]').inputValue();
    await context.request.post('/logout', {form: {csrf_token: csrf}});
    await page.locator('.sidebar-links a[href="/reports"]').click();
    await expect(page.locator('.account a[href="/login"]')).toBeVisible();
    await expect(page.locator('.sidebar-links a[href="/admin/users"]')).toHaveCount(0);
    assert.equal(await page.evaluate(() => window.testShell), undefined);
});

test('admin and public pages stay separate through navigation, history, and theme changes', async () => {
    await page.setViewportSize({width: 1440, height: 1000});
    await login();
    await markShell();
    await expect(page.locator('body')).toHaveAttribute('data-space', 'admin');
    await expect(page.locator('#sidebar-toggle')).toHaveAttribute('aria-expanded', 'true');
    const logo = await page.locator('.sidebar-brand').boundingBox();
    const sidebarButton = await page.locator('#sidebar-toggle').boundingBox();
    assert.ok(sidebarButton.x + sidebarButton.width <= logo.x, 'The arrow belongs at the end of the RTL sidebar heading');
    await page.locator('#sidebar-toggle').click();
    await expect(page.locator('html')).toHaveClass(/sidebar-collapsed/);
    await expect(page.locator('#navigation-toggle')).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#sidebar-toggle')).toHaveAttribute('aria-label', 'باز کردن فهرست');
    await capture('collapsed-sidebar');
    await page.locator('#sidebar-toggle').click();
    await expect(page.locator('html')).not.toHaveClass(/sidebar-collapsed/);
    await expect(page.locator('#navigation-toggle')).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('.workspace-context')).toHaveCount(0);
    const links = await page.locator('.sidebar-links a').allTextContents();
    assert.deepEqual(links.slice(0, 3).map(text => text.trim()), ['آگهی‌ها', 'رسانه‌ها', 'سازمان‌ها']);
    const icons = await page.locator('.sidebar-links svg').evaluateAll(nodes => nodes.map(node => node.innerHTML));
    assert.equal(icons.length, new Set(icons).size);
    await expect(page.locator('.sidebar-links [aria-current="page"]')).toHaveCount(1);
    await expect(page.locator('#main-content [data-delete-ad]').first()).toBeVisible();
    await capture('admin-advertisements');

    await navigateByLink('.sidebar-links a[href="/reports"]', '/reports');
    await expect(page.locator('body')).toHaveAttribute('data-space', 'public');
    await expect(page.locator('#main-content')).toHaveAttribute('data-space', 'public');
    await expect(page.locator('.workspace-context')).toHaveCount(0);
    await expect(page.locator('h1')).toHaveText('گزارش آگهی‌ها');
    await expect(page.locator('#main-content a[href^="/admin/"], #main-content [data-delete-ad]')).toHaveCount(0);
    await expect(page.locator('#date-field')).toHaveValue('approval_date');
    await expect(page.locator('.report-search')).not.toHaveAttribute('open');
    await page.locator('.report-search > summary').focus();
    await page.keyboard.press('Enter');
    await page.locator('.filter-disclosure:not(.column-selector) > summary').click();
    await expect(page.locator('#ma-min')).toBeVisible();
    await page.locator('#ma-min').fill('7001');
    await page.locator('.column-selector > summary').click();
    const countBefore = await page.locator('[name="columns"]:checked').count();
    await page.locator('[name="columns"]').first().setChecked(!(await page.locator('[name="columns"]').first().isChecked()));
    const countAfter = await page.locator('[name="columns"]:checked').count();
    assert.equal(Math.abs(countBefore - countAfter), 1);
    await expect(page.locator('[data-column-count]')).toHaveText(`${countAfter.toLocaleString('fa-IR')} ستون`);
    await capture('report-accordions');
    await page.locator('.filter-actions button[type="submit"]').click();
    await page.waitForURL(url => url.searchParams.get('ma_min') === '7001');
    await expect(page.locator('.report-search')).not.toHaveAttribute('open');
    await page.locator('.report-search > summary').click();
    await expect(page.locator('#ma-min')).toBeVisible();
    await expect(page.locator('.accordion-meta').first()).toContainText('فیلتر فعال');
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await capture('dark-reports');

    await page.goBack();
    await page.waitForURL('/reports');
    await page.goBack();
    await page.waitForURL('/admin/advertisements');
    await expect(page.locator('.workspace-context')).toHaveCount(0);
    await expect(page.locator('body')).toHaveAttribute('data-space', 'admin');
    await expectShell();
    await navigateByLink('.sidebar-links a[href="/admin/catalog/outlets"]', '/admin/catalog/outlets');
    await navigateByLink('.row-actions a[title="مشاهده"]', /\/admin\/media\/\d+$/);
    await expect(page.locator('#main-content a[href$="/edit"]')).toBeVisible();
    await expect(page.locator('.admin-navigation [aria-current="page"]')).toHaveText('رسانه‌ها');
    await navigateByLink('.sidebar-links a[href="/reports?view=media"]', '/reports?view=media');
    await expect(page.locator('.media-share-table thead th')).toHaveCount(5);
    await capture('media-shares');
    await navigateByLink('.media-share-table tbody a', /\/media\/\d+\?/);
    await expect(page.locator('#main-content a[href^="/admin/"], #main-content [data-delete-ad]')).toHaveCount(0);
    await expect(page.locator('.public-navigation [aria-current="page"]')).toHaveText('سهم رسانه‌ها');
});

test('iPhone installation offers Share menu instructions', async () => {
    const appleContext = await browser.newContext({baseURL, viewport: {width: 390, height: 844},
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'});
    try {
        const apple = await appleContext.newPage();
        await apple.goto('/');
        await expect(apple.locator('#pwa-install')).toBeVisible();
        await apple.locator('#pwa-install').click();
        await expect(apple.locator('#pwa-install-help')).toBeVisible();
        await expect(apple.locator('#pwa-install-help')).toContainText('Add to Home Screen');
        await apple.locator('[data-pwa-close]').click();
        await expect(apple.locator('#pwa-install-help')).toBeHidden();
    } finally { await appleContext.close(); }
});

test('direct routes and GET and POST forms still work without JavaScript', async () => {
    const nativeContext = await browser.newContext({baseURL, javaScriptEnabled: false});
    try {
        const native = await nativeContext.newPage();
        await native.goto('/login');
        await native.locator('#username').fill('administrator');
        await native.locator('#password').fill('test-password-123');
        await native.locator('.auth form button').click();
        await native.waitForURL('/admin/advertisements');
        await native.goto('/admin/catalog/subjects');
        await native.locator('#new-name').fill('آزمایش بدون جاوااسکریپت');
        await native.locator('#new-name').press('Enter');
        await expect(native.locator('.catalog-name').last()).toBeVisible();
        await native.goto('/reports');
        await expect(native.locator('[name="half"]')).toHaveCount(0);
        await native.locator('.period-controls button').click();
        await native.waitForURL(url => url.pathname === '/reports' && url.searchParams.has('year'));
    } finally { await nativeContext.close(); }
});

test('media types use one local group and periods use automatic memberships', async () => {
    await login();
    await markShell();
    await navigateByLink('.sidebar-links a[href="/admin/catalog/outlets"]', '/admin/catalog/outlets');
    const names = ['رسانه محلی اول مرورگر', 'رسانه محلی دوم مرورگر', 'رسانه سراسری مرورگر'];
    for (const [index, group] of ['local', 'local', 'national'].entries()) {
        await navigateByLink('a[href="/admin/media/new"]', '/admin/media/new');
        await expect(page.locator('#media-type')).toHaveValue('');
        await page.locator('#media-name').fill(names[index]);
        await page.locator('#media-type').selectOption(group === 'national' ? 'national' : 'local');
        await expect(page.locator('#media-local-group')).toHaveCount(0);
        if (index === 0) await capture('media-type-local');
        await page.locator('[data-media-form] button[type="submit"], [data-media-form] button:not([type])').click();
        await page.waitForURL('/admin/catalog/outlets');
        const row = page.locator('tbody tr').filter({hasText:names[index]});
        await expect(row).toHaveCount(1);
        const preview = await context.request.get('/api/allocation-preview?allocation_date=2026-10-01&volume=1');
        assert.equal(preview.status(), 200);
        assert.ok((await preview.json()).options.some(option => option.name === names[index] && option.category === group));
    }
    await expectShell();
    await navigateByLink('.sidebar-links a[href="/admin/periods"]', '/admin/periods');
    await expect(page.locator('#new-half')).toHaveCount(0);
    await page.locator('#new-year').fill('1407');
    await page.getByRole('button', {name:'ایجاد دوره',exact:true}).click();
    await page.waitForURL(/\/admin\/periods\/\d+$/);
    await expect(page.locator('[data-group-target]')).toHaveText(['هدف 50٪','هدف 50٪']);
    await expect(page.locator('h1')).toContainText('فروردین تا اسفند');
    await expect(page.locator('[name^="share_"], [name^="category_"], [name^="include_"]')).toHaveCount(0);
    await page.locator('[name="active"]').check();
    await page.getByRole('button', {name:'ذخیره وضعیت دوره',exact:true}).click();
    await expect(page.locator('.alert.success')).toContainText('وضعیت دوره ذخیره شد');
    await capture('automatic-period-groups');
    await page.setViewportSize({width:390,height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await capture('automatic-period-mobile');
});

test('media type validation also works without JavaScript', async () => {
    const nativeContext = await browser.newContext({baseURL,javaScriptEnabled:false});
    try {
        const native = await nativeContext.newPage();
        await native.goto('/login');
        await native.locator('#username').fill('administrator');
        await native.locator('#password').fill('test-password-123');
        await native.locator('.auth form button').click();
        await native.waitForURL('/admin/advertisements');
        await native.goto('/admin/media/new');
        await native.locator('#media-name').fill('رسانه بدون جاوااسکریپت');
        await native.locator('#media-type').selectOption('local');
        await native.locator('[data-media-form] button').click();
        await native.waitForURL('/admin/catalog/outlets');
        await expect(native.locator('tbody tr').filter({hasText:'رسانه بدون جاوااسکریپت'})).toContainText('رسانه‌های محلی');
    } finally { await nativeContext.close(); }
});

test('entity delete actions confirm removal and archive referenced organizations', async () => {
    await login();
    await navigateByLink('.sidebar-links a[href="/admin/catalog/organizations"]', '/admin/catalog/organizations');
    await page.locator('#new-name').fill('سازمان آزمایش حذف');
    await page.locator('#new-name').press('Enter');
    const organizationDelete = page.getByRole('button', {name: 'حذف سازمان آزمایش حذف', exact: true});
    await expect(organizationDelete).toBeVisible();
    await confirmAction(organizationDelete, false, 'delete-confirmation-light');
    await expect(organizationDelete).toBeVisible();
    await expect(organizationDelete).toBeFocused();
    await confirmAction(organizationDelete);
    await expect(organizationDelete).toHaveCount(0);
    await expect(page.locator('.alert.success')).toContainText('حذف شد');
    await capture('organization-actions');

    await navigateByLink('.sidebar-links a[href="/admin/catalog/subjects"]', '/admin/catalog/subjects');
    await page.locator('#new-name').fill('موضوع آزمایش حذف');
    await page.locator('#new-name').press('Enter');
    const subjectDelete = page.getByRole('button', {name: 'حذف موضوع آزمایش حذف', exact: true});
    await expect(subjectDelete).toBeVisible();
    await confirmAction(subjectDelete);
    await expect(subjectDelete).toHaveCount(0);

    await navigateByLink('.sidebar-links a[href="/admin/periods"]', '/admin/periods');
    await page.locator('#new-year').fill('1406');
    await page.getByRole('button', {name: 'ایجاد دوره', exact: true}).click();
    await page.waitForURL(/\/admin\/periods\/\d+$/);
    await navigateByLink('a[href="/admin/periods"]', '/admin/periods');
    const periodRow = page.locator('tbody tr').filter({hasText: '1406'});
    await expect(periodRow).toHaveCount(1);
    await confirmAction(periodRow.locator('button[title="حذف دوره"]'));
    await expect(periodRow).toHaveCount(0);
    await expect(page.locator('button[title="دوره دارای سابقه آگهی قابل حذف نیست"]')).toBeDisabled();

    await navigateByLink('.sidebar-links a[href="/admin/users"]', '/admin/users');
    await page.locator('#username-new').fill('browser-delete-user');
    await page.locator('#name-new').fill('حساب آزمایش حذف');
    await page.locator('#password-new').fill('temporary-password-123');
    await page.getByRole('button', {name: 'ایجاد حساب شخصی', exact: true}).click();
    const user = page.locator('details[data-user-id]').filter({hasText: 'حساب آزمایش حذف'});
    await expect(user).toHaveCount(1);
    await user.locator('summary').click();
    const userDelete = user.getByRole('button', {name: 'حذف حساب حساب آزمایش حذف', exact: true});
    await expect(userDelete).toBeVisible();
    await capture('account-delete-action');
    await confirmAction(userDelete);
    await expect(user).toHaveCount(0);

    await navigateByLink('.sidebar-links a[href="/admin/catalog/outlets"]', '/admin/catalog/outlets');
    const mediaArchive = page.getByRole('button', {name: 'بایگانی شهرآرا', exact: true});
    await page.locator('#theme-toggle').click();
    await confirmAction(mediaArchive, true, 'archive-confirmation-dark');
    await expect(mediaArchive).toBeDisabled();

    await navigateByLink('.sidebar-links a[href="/admin/catalog/organizations"]', '/admin/catalog/organizations');
    const archive = page.getByRole('button', {name: 'بایگانی راه و شهرسازی', exact: true});
    await expect(archive).toBeEnabled();
    await confirmAction(archive);
    await expect(archive).toBeDisabled();
    await expect(page.locator('.alert.success')).toContainText('بایگانی شد');
    await page.goto('/advertisements/1');
    await expect(page.locator('h1')).toHaveText('آگهی م/الف 7001');
});

test('custom confirmations preserve validation and button values and cancel across history changes', async () => {
    await login();
    await navigateByLink('.sidebar-links a[href="/admin/catalog/subjects"]', '/admin/catalog/subjects');
    await markShell();
    await page.evaluate(() => {
        const form = document.getElementById('new-name').form;
        form.dataset.confirm = 'این موضوع به فهرست اضافه شود؟';
        form.dataset.confirmTitle = 'افزودن موضوع';
        form.dataset.confirmLabel = 'تأیید و افزودن';
        const button = form.querySelector('button');
        button.name = 'intent';
        button.value = 'create-subject';
    });
    const submit = page.locator('#main-content .check-action button');
    let posts = 0;
    page.on('request', request => {
        if (request.method() === 'POST' && new URL(request.url()).pathname === '/admin/catalog/subjects') posts++;
    });
    // An invalid form does not open a confirmation or submit.
    await submit.click();
    await expect(page.locator('#confirmation-dialog')).toBeHidden();
    assert.equal(posts, 0);
    await page.locator('#new-name').fill('موضوع تأیید عمومی');
    await submit.click();
    const dialog = page.getByRole('dialog', {name: 'افزودن موضوع', exact: true});
    await expect(dialog).toBeVisible();
    await expect(page.locator('#confirmation-description')).toHaveText('این موضوع به فهرست اضافه شود؟');
    await expect(page.locator('#confirmation-cancel')).toBeFocused();
    await expect(page.locator('#confirmation-accept')).not.toHaveClass(/danger/);
    const duration = await dialog.evaluate(node => parseFloat(getComputedStyle(node).animationDuration));
    assert.ok(duration >= 0.3);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(submit).toBeFocused();
    await expect(page.locator('#new-name')).toHaveValue('موضوع تأیید عمومی');
    assert.equal(posts, 0);

    await submit.click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(page.locator('#confirmation-accept')).toBeFocused();
    const submission = page.waitForRequest(request => request.method() === 'POST'
        && new URL(request.url()).pathname === '/admin/catalog/subjects');
    await page.keyboard.press('Enter');
    const request = await submission;
    assert.match(request.postData(), /name="intent"\r\n\r\ncreate-subject/);
    const deletion = page.getByRole('button', {name: 'حذف موضوع تأیید عمومی', exact: true});
    await expect(deletion).toBeVisible();
    assert.equal(posts, 1);
    await expectShell();

    await deletion.click();
    await expect(page.locator('#confirmation-title')).toHaveText('حذف موضوع تأیید عمومی');
    await page.goBack();
    await page.waitForURL('/admin/advertisements');
    await expect(page.locator('#confirmation-dialog')).toBeHidden();
    assert.equal(posts, 1);
    await page.goForward();
    await page.waitForURL('/admin/catalog/subjects');
    await expect(deletion).toBeVisible();
    await page.setViewportSize({width: 390, height: 844});
    await page.emulateMedia({reducedMotion: 'reduce'});
    await deletion.click();
    await expect(page.locator('#confirmation-dialog')).toBeVisible();
    assert.equal(await page.locator('#confirmation-dialog').evaluate(node => node.getAnimations().length), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await capture('mobile-confirmation');
    await page.locator('#confirmation-dialog [aria-label="بستن"]').click();
    await expect(page.locator('#confirmation-dialog')).toBeHidden();
    await expect(deletion).toBeFocused();
    assert.equal(posts, 1);
});

test('public chart slices drill into media shares and survive navigation and empty years', async () => {
    await page.goto('/reports?view=media&year=1405');
    await markShell();
    await expect(page.locator('[data-line-chart] svg')).toBeVisible();
    const month = page.locator('[data-chart-month="0"]');
    await month.hover();
    await expect(page.locator('body > .chart-popover')).toBeVisible();
    await expect(page.locator('body > .chart-popover')).toContainText('فروردین');
    await page.locator('[data-chart-mode="bar"]').click();
    await expect(page.locator('[data-line-chart]')).toHaveAttribute('data-mode','bar');
    await expect(page.locator('[data-line-chart] .chart-bar')).toHaveCount(24);
    await month.focus();
    await expect(page.locator('body > .chart-popover')).toContainText('مجموع ماه');
    await page.keyboard.press('Escape');
    await expect(page.locator('body > .chart-popover')).toBeHidden();
    await page.locator('[data-chart-mode="line"]').click();
    const local = page.locator('[data-chart-slice="local"]');
    await local.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-chart-dialog]')).toBeVisible();
    await expect(page.locator('#chart-detail-title')).toHaveText('سهم رسانه‌های محلی');
    await expect(page.locator('[data-detail-legend] a').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-chart-dialog]')).toBeHidden();
    await expect(local).toBeFocused();
    await page.locator('[data-chart-category="national"]').click();
    await expect(page.locator('#chart-detail-title')).toHaveText('سهم رسانه‌های سراسری');
    await page.locator('[data-chart-close]').click();
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-chart-category="local"]').click();
    await expect(page.locator('[data-detail-pie] svg')).toBeVisible();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await capture('public-chart-drilldown-mobile');
    await navigateByLink('[data-detail-legend] a', /\/media\/\d+\?/);
    await expect(page.locator('[data-chart-dialog][open]')).toHaveCount(0);
    await page.goBack();
    await page.waitForURL('/reports?view=media&year=1405');
    await expect(page.locator('[data-summary-pie] svg')).toBeVisible();
    await expectShell();
    await page.locator('#period-year').fill('1408');
    await page.locator('.period-controls button').click();
    await page.waitForURL(url => url.searchParams.get('year') === '1408');
    await expect(page.locator('[data-summary-pie]')).toHaveCount(0);
    await page.locator('[data-chart-category="local"]').click();
    await expect(page.locator('[data-detail-pie]')).toContainText('حجمی ثبت نشده');
});

test('admin ads are a plain list and the ad form requires identification, organization, media and volume', async () => {
    await login();
    await expect(page.locator('#report-filter-form, .period-controls, .filtered-stats')).toHaveCount(0);
    await navigateByLink('a[href="/admin/advertisements/new"]','/admin/advertisements/new');
    await expect(page.locator('#advertisement-form details[open]')).toHaveCount(0);
    assert.deepEqual(await page.locator('#advertisement-form [required]').evaluateAll(nodes => nodes.map(node=>node.name).sort()),
        ['ma_reference','organization_id','outlet_id','volume']);
    await expect(page.locator('#allocation-preview')).toBeVisible();
    await expect(page.locator('#allocation-preview').locator('xpath=ancestor::details')).toHaveCount(0);
    await capture('minimal-ad-form');
    await page.locator('#ma_reference').fill('9201');
    await page.locator('#organization_id').selectOption({index:1});
    await page.locator('#outlet_id').selectOption({index:1});
    await page.locator('#volume').fill('2.5');
    await expect(page.locator('#preview-token')).not.toHaveValue('');
    if (await page.locator('#override-label').isVisible()) await page.locator('#override-confirmed').check();
    await expect(page.locator('#save-ad')).toBeEnabled();
    await page.locator('#save-ad').click();
    await page.waitForURL(/\/admin\/advertisements\/\d+$/);
    await expect(page.locator('h1')).toContainText('9201');
    await expect(page.locator('.stat').first()).toContainText('2.5');
    await navigateByLink('.sidebar-links a[href="/admin/advertisements"]','/admin/advertisements');
    await expect(page.locator('tbody tr').filter({hasText:'9201'})).toHaveCount(1);
    await capture('plain-admin-ads');
});

test('ad entry requires volume and saves reviewed allocation without JavaScript', async () => {
    const nativeContext=await browser.newContext({baseURL,javaScriptEnabled:false});
    try {
        const native=await nativeContext.newPage();
        await native.goto('/login');
        await native.locator('#username').fill('administrator');
        await native.locator('#password').fill('test-password-123');
        await native.locator('.auth form button').click();
        await native.waitForURL('/admin/advertisements');
        await native.goto('/admin/advertisements/new');
        await expect(native.locator('#advertisement-form details[open]')).toHaveCount(0);
        await native.locator('#ma_reference').fill('9202');
        await native.locator('#organization_id').selectOption({index:1});
        await native.locator('#outlet_id').selectOption({index:1});
        await native.locator('#save-ad').click();
        await expect(native).toHaveURL(/\/advertisements\/new$/);
        await native.locator('#volume').fill('2.5');
        const review=native.waitForResponse(response=>response.request().method()==='POST' && response.url().endsWith('/advertisements/new'));
        await native.locator('#save-ad').click();
        assert.equal((await review).status(),409);
        await expect(native.locator('#allocation-preview')).toBeVisible();
        if (await native.locator('#override-label').isVisible()) await native.locator('#override-confirmed').check();
        await native.locator('#save-ad').click();
        await native.waitForURL(/\/admin\/advertisements\/\d+$/);
        await expect(native.locator('h1')).toContainText('9202');
    } finally {await nativeContext.close();}
});

test('loading buttons preserve native login and POST values, prevent repeats, and recover after failure', async () => {
    await page.goto('/login');
    await page.locator('#username').fill('administrator');
    await page.locator('#password').fill('test-password-123');
    await page.locator('.auth form button').evaluate(button=>{button.name='intent';button.value='login';});
    let releaseLogin, loginBody;
    await page.route('**/login',async route=>{
        if(route.request().method()!=='POST') return route.continue();
        loginBody=route.request().postData();
        await new Promise(resolve=>{releaseLogin=resolve;});
        await route.continue();
    });
    await page.locator('.auth form button').click({noWaitAfter:true});
    await page.waitForFunction(()=>document.querySelector('.auth form button')?.getAttribute('aria-busy')==='true');
    await expect.poll(()=>typeof releaseLogin).toBe('function');
    assert.match(loginBody,/intent=login/);
    releaseLogin();
    await page.waitForURL('/admin/advertisements');
    await expect(page.locator('.is-loading')).toHaveCount(0);
    await navigateByLink('.sidebar-links a[href="/admin/catalog/subjects"]','/admin/catalog/subjects');
    await page.locator('#new-name').fill('آزمایش حالت بارگذاری');
    const submit=page.locator('.check-action button');
    await submit.evaluate(button=>{button.name='intent';button.value='loading-test';});
    let releasePost, posts=0, postBody;
    await page.route('**/admin/catalog/subjects',async route=>{
        if(route.request().method()!=='POST') return route.continue();
        posts++;
        postBody=route.request().postData();
        if(posts===1) {
            await new Promise(resolve=>{releasePost=resolve;});
            return route.abort('failed');
        }
        return route.continue();
    });
    await submit.click();
    await expect(submit).toHaveAttribute('aria-busy','true');
    if(process.env.SOBHE_BROWSER_ARTIFACTS) {
        fs.mkdirSync('outputs/browser',{recursive:true});
        await page.screenshot({path:'outputs/browser/loading-save.png'});
    }
    await expect.poll(()=>typeof releasePost).toBe('function');
    assert.match(postBody,/name="intent"\r\n\r\nloading-test/);
    await submit.evaluate(button=>button.form.requestSubmit(button));
    assert.equal(posts,1);
    releasePost();
    await expect(page.locator('[data-navigation-error]')).toBeVisible();
    await expect(submit).not.toHaveClass(/is-loading/);
    await expect(submit).not.toHaveAttribute('aria-disabled','true');
    await expect(page.locator('#new-name')).toHaveValue('آزمایش حالت بارگذاری');
    await submit.click();
    await expect(page.locator('.catalog-name input').last()).toBeVisible();
    await expect(page.locator('.alert.success')).toBeVisible();
    assert.equal(posts,2);
    await expect(page.locator('.is-loading')).toHaveCount(0);
});

test('export buttons load until the download completes and recover from a failed download', async () => {
    await page.goto('/reports');
    const exportButton=page.locator('a[href*="export=xlsx"]');
    let release, fail=false;
    await page.route('**/*export=xlsx*',async route=>{
        await new Promise(resolve=>{release=resolve;});
        if(fail) return route.abort('failed');
        return route.continue();
    });
    const download=page.waitForEvent('download');
    await exportButton.click();
    await expect(exportButton).toHaveAttribute('aria-busy','true');
    await expect.poll(()=>typeof release).toBe('function');
    release();
    assert.equal((await download).suggestedFilename(),'advertisement-report.xlsx');
    await expect(exportButton).not.toHaveClass(/is-loading/);
    fail=true;
    release=null;
    await exportButton.click();
    await expect(exportButton).toHaveClass(/is-loading/);
    await expect.poll(()=>typeof release).toBe('function');
    release();
    await expect(page.locator('[data-navigation-error]')).toBeVisible();
    await expect(exportButton).not.toHaveAttribute('aria-disabled','true');
    await expect(page).toHaveURL('/reports');
});

test('allocation preview controls show loading and recover when calculation fails', async () => {
    await login();
    await navigateByLink('a[href="/admin/advertisements/new"]','/admin/advertisements/new');
    let release, fail=false;
    await page.route('**/api/allocation-preview?*',async route=>{
        await new Promise(resolve=>{release=resolve;});
        if(fail) return route.abort('failed');
        return route.continue();
    });
    await page.locator('#volume').fill('4');
    await expect(page.locator('#save-ad')).toHaveClass(/is-loading/);
    await expect.poll(()=>typeof release).toBe('function');
    release();
    await expect(page.locator('#preview-token')).not.toHaveValue('');
    await expect(page.locator('#save-ad')).not.toHaveClass(/is-loading/);
    release=null;
    fail=true;
    await page.locator('#use-recommendation').click();
    await expect(page.locator('#use-recommendation')).toHaveAttribute('aria-busy','true');
    await expect(page.locator('#save-ad')).toHaveClass(/is-loading/);
    await expect.poll(()=>typeof release).toBe('function');
    release();
    await expect(page.locator('#allocation-preview')).toHaveAttribute('aria-busy','false');
    await expect(page.locator('#save-ad')).toBeEnabled();
    await expect(page.locator('#use-recommendation')).not.toHaveClass(/is-loading/);
    await expect(page.locator('.is-loading')).toHaveCount(0);
});
