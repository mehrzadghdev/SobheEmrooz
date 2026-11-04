(() => {
    'use strict';
    if (!window.fetch || !window.AbortController) return;
    const main = document.getElementById('main-content');
    const progress = document.getElementById('navigation-progress');
    const status = document.getElementById('navigation-status');
    if (!main || !progress || !status) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const nativePath = /\/(?:login|logout|api|static)(?:\/|$)|\/advertisements\/\d+\/file\/?$/;
    let pending;
    let finishTimer;
    let scrollTimer;
    let animation;
    let renderedURL = location.href;

    function internal(url) {
        return url.origin === location.origin && /^https?:$/.test(url.protocol)
            && !nativePath.test(url.pathname) && !url.searchParams.get('export');
    }

    function busy(active, message) {
        clearTimeout(finishTimer);
        main.setAttribute('aria-busy', String(active));
        progress.hidden = false;
        progress.classList.toggle('is-complete', !active);
        status.textContent = message || (active ? 'در حال بارگذاری…' : 'صفحه بارگذاری شد.');
        if (!active) finishTimer = setTimeout(() => { progress.hidden = true; }, 300);
    }

    function saveScroll() {
        // On popstate the address already belongs to the destination entry.
        if (location.href === renderedURL) {
            history.replaceState({...history.state, sobheScroll: [scrollX, scrollY]}, '', location.href);
        }
    }

    function scrollToDestination(url, position) {
        if (position) window.scrollTo(...position);
        else if (url.hash) {
            let id;
            try { id = decodeURIComponent(url.hash.slice(1)); } catch (_) { id = url.hash.slice(1); }
            const target = document.getElementById(id);
            if (target) target.scrollIntoView();
            else window.scrollTo(0, 0);
        } else window.scrollTo(0, 0);
    }

    function showError(message) {
        document.querySelectorAll('[data-navigation-error]').forEach(element => element.remove());
        const alert = document.createElement('div');
        alert.dataset.navigationError = '';
        alert.className = 'alert danger';
        alert.setAttribute('role', 'alert');
        alert.textContent = message;
        main.prepend(alert);
    }

    function syncControls(form, incoming, resetVisible) {
        // Keep the actual controls alive so typing, focus, calendars and open
        // filter disclosures survive a result update.
        form.querySelectorAll(':scope > input[type="hidden"]').forEach(input => input.remove());
        form.prepend(...[...incoming.querySelectorAll(':scope > input[type="hidden"]')].map(input => input.cloneNode(true)));
        for (const source of incoming.elements) {
            if (!source.name || source.type === 'hidden' || source.tagName === 'BUTTON') continue;
            if (!resetVisible && source.name !== 'per_page') continue;
            const controls = [...form.elements].filter(control => control.name === source.name);
            const target = source.type === 'checkbox' || source.type === 'radio'
                ? controls.find(control => control.value === source.value) : controls[0];
            if (!target) continue;
            if (source.type === 'checkbox' || source.type === 'radio') target.checked = source.checked;
            else if (target.value !== source.value) {
                target.value = source.value;
                target.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }
        const copy = (selector, children = false) => {
            const sources = incoming.querySelectorAll(selector);
            form.querySelectorAll(selector).forEach((target, index) => {
                const source = sources[index];
                if (!source) return;
                if (children) target.replaceChildren(...[...source.childNodes].map(node => node.cloneNode(true)));
                else if (source.hasAttribute('href')) target.setAttribute('href', source.getAttribute('href'));
            });
        };
        copy('.filter-disclosure > summary', true);
        copy('.report-date-help', true);
        copy('.filter-actions a');
        form.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function updateRegions(content, url, {pop, trigger}) {
        const current = main.querySelector('[data-live-page]');
        const incoming = content.querySelector('[data-live-page]');
        const previous = new URL(renderedURL);
        if (!current || !incoming || current.dataset.livePage !== incoming.dataset.livePage
                || url.pathname !== previous.pathname) return false;
        const regions = [...current.querySelectorAll('[data-page-region]')]
            .filter(node => !node.parentElement.closest('[data-page-region]'));
        const replacements = new Map([...incoming.querySelectorAll('[data-page-region]')]
            .filter(node => !node.parentElement.closest('[data-page-region]'))
            .map(node => [node.dataset.pageRegion, document.importNode(node, true)]));
        if (!regions.length || regions.length !== replacements.size
                || regions.some(node => !replacements.has(node.dataset.pageRegion))) return false;
        const keys = new Set([...previous.searchParams.keys(), ...url.searchParams.keys()]);
        const changed = [...keys].filter(key => JSON.stringify(previous.searchParams.getAll(key)) !== JSON.stringify(url.searchParams.getAll(key)));
        const chartsOnly = changed.length && changed.every(key => key.startsWith('chart_'));
        const resetVisible = pop || trigger?.closest('.filter-actions a, .period-controls');
        const focused = document.activeElement;
        const selection = focused instanceof HTMLInputElement && focused.type === 'search'
            ? [focused.selectionStart, focused.selectionEnd] : null;
        const sort = trigger?.closest('a.table-sort');
        const sortKey = sort && new URL(sort.href).searchParams.get('sort');
        for (const region of regions) {
            const replacement = replacements.get(region.dataset.pageRegion);
            const forms = [...region.querySelectorAll('form[data-live-controls]')];
            for (const form of forms) {
                const source = replacement.querySelector(`form[data-live-controls="${form.dataset.liveControls}"]`);
                if (source) {
                    syncControls(form, source, resetVisible);
                }
            }
            if (region.hasAttribute('data-chart-panel')) {
                const oldData = region.querySelector('[data-report-chart-data]')?.textContent;
                const newData = replacement.querySelector('[data-report-chart-data]')?.textContent;
                if (oldData === newData) continue;
                document.dispatchEvent(new CustomEvent('sobhe:before-chart-update', {detail: {root: region}}));
                region.replaceWith(replacement);
            } else if (!chartsOnly) {
                const controls = new Map(forms.filter(form => form.parentElement === region)
                    .map(form => [form.dataset.liveControls, form]));
                if (!controls.size) region.replaceChildren(...replacement.childNodes);
                else {
                    // Keep forms attached to the live document. Detaching them
                    // interrupts focus and can strand running CSS transitions.
                    const retained = new Set(controls.values());
                    [...region.childNodes].forEach(node => { if (!retained.has(node)) node.remove(); });
                    let buffer = [];
                    for (const node of [...replacement.childNodes]) {
                        const control = node.nodeType === Node.ELEMENT_NODE && controls.get(node.dataset.liveControls);
                        if (control) {
                            control.before(...buffer);
                            buffer = [];
                            retained.delete(control);
                        } else buffer.push(node);
                    }
                    region.append(...buffer);
                    retained.forEach(node => node.remove());
                }
            } else {
                // Keep links in sync with the selected chart range without
                // replacing a table that has not changed.
                const selectors = '.table-sort, .pagination a, .report-exports a';
                const links = region.querySelectorAll(selectors);
                replacement.querySelectorAll(selectors).forEach((source, index) => {
                    if (links[index]) links[index].href = source.href;
                });
            }
        }
        main.querySelectorAll('[data-navigation-error]').forEach(node => node.remove());
        if (focused?.isConnected) {
            focused.focus({preventScroll: true});
            if (selection) focused.setSelectionRange(...selection);
        } else if (sortKey) {
            [...current.querySelectorAll('a.table-sort')]
                .find(link => new URL(link.href).searchParams.get('sort') === sortKey)?.focus({preventScroll: true});
        } else if (trigger?.closest('.chart-period-controls') && trigger.name === `chart_${trigger.closest('[data-chart-panel]')?.dataset.chartPanel}_range`) {
            current.querySelector(`[data-chart-panel="${trigger.closest('[data-chart-panel]').dataset.chartPanel}"] [data-chart-range="${trigger.value}"]`)?.focus({preventScroll: true});
        }
        return true;
    }

    async function navigate(url, {method = 'GET', body, pop = false, position, preserveScroll = false, trigger} = {}) {
        // A submitted mutation must finish before another page request can replace it.
        if (pending?.method === 'POST') return;
        pending?.controller.abort();
        const request = {controller: new AbortController(), method, finishLoading: window.SobheLoading?.start(trigger)};
        pending = request;
        saveScroll();
        const previousScroll = [scrollX, scrollY];
        busy(true);
        document.dispatchEvent(new CustomEvent('sobhe:navigation-start'));
        const timeout = setTimeout(() => request.controller.abort(), 60000);
        let failureMessage;
        try {
            const response = await fetch(url, {
                method, body, signal: request.controller.signal, credentials: 'same-origin',
                cache: 'no-store', headers: {Accept: 'text/html'},
            });
            const finalURL = new URL(response.url);
            if (finalURL.origin !== location.origin) throw new Error('External redirect');
            if (!response.headers.get('Content-Type')?.includes('text/html')
                    || response.headers.get('Content-Disposition')?.includes('attachment')) {
                throw new Error('Unexpected navigation response');
            }
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            if (pending !== request || request.historyChanged) return;
            const content = page.getElementById('main-content');
            if (!content) throw new Error('Missing page content');
            if (content.dataset.session !== main.dataset.session || !internal(finalURL)) {
                // Account changes need fresh header controls and sidebar permissions.
                location.assign(finalURL.href);
                return;
            }
            // Validation and stale-preview responses carry a new, reviewable form.
            const reviewable = method === 'POST' && [400, 409, 422].includes(response.status)
                && content.querySelector('form');
            if (!response.ok && !reviewable) {
                const error = new Error('Navigation failed');
                error.userMessage = content.querySelector('[role="alert"], .empty-state h1')?.textContent.trim();
                throw error;
            }
            finalURL.hash = url.hash;
            const updated = method === 'GET' && updateRegions(content, finalURL, {pop, trigger});
            animation?.cancel();
            if (!updated) {
                document.dispatchEvent(new CustomEvent('sobhe:before-render'));
                main.replaceChildren(...content.childNodes);
            }
            main.dataset.space = content.dataset.space;
            document.body.dataset.space = content.dataset.space;
            document.title = page.title;
            const incomingLinks = page.querySelector('.sidebar-links');
            const links = document.querySelector('.sidebar-links');
            if (!updated && links && incomingLinks) links.replaceChildren(...incomingLinks.childNodes);
            if (pop || finalURL.href === location.href) history.replaceState(history.state, '', finalURL);
            else history.pushState({}, '', finalURL);
            renderedURL = location.href;
            // Initialize date fields before restoring scroll; enhancement changes their height.
            document.dispatchEvent(new CustomEvent('sobhe:page-render'));
            if (!updated) main.focus({preventScroll: true});
            scrollToDestination(finalURL, position || (preserveScroll || updated ? previousScroll : null));
            saveScroll();
            if (!updated && !reducedMotion.matches && main.animate) {
                animation = main.animate(
                    [{opacity: 0, transform: 'translateY(6px)'}, {opacity: 1, transform: 'translateY(0)'}],
                    {duration: 300, easing: 'ease-out'},
                );
            }
        } catch (error) {
            if (pending !== request || request.historyChanged) return;
            if (pop) {
                location.replace(url.href);
                return;
            }
            // A failed POST may already have committed; never retry it automatically.
            failureMessage = error.userMessage || (method === 'POST'
                ? 'ارتباط قطع شد یا درخواست ناموفق بود. پیش از ارسال دوباره، صفحه را تازه کنید و نتیجه را بررسی کنید.'
                : 'بارگذاری صفحه انجام نشد. اتصال اینترنت را بررسی کرده و دوباره تلاش کنید.');
            showError(failureMessage);
        } finally {
            clearTimeout(timeout);
            request.finishLoading?.();
            if (pending === request) {
                pending = null;
                busy(false, failureMessage);
                document.dispatchEvent(new CustomEvent('sobhe:navigation-end'));
            }
        }
    }

    function downloadable(url) {
        return url.origin === location.origin && (url.searchParams.get('export') === 'xlsx'
            || /\/advertisements\/\d+\/file\/?$/.test(url.pathname));
    }

    async function downloadFile(url, trigger) {
        const finishLoading = window.SobheLoading?.start(trigger);
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 60000);
        try {
            const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin', cache: 'no-store'});
            if (!response.ok || /text\/html|application\/json/i.test(response.headers.get('Content-Type') || '')) {
                throw new Error('دریافت فایل انجام نشد؛ دسترسی و اتصال اینترنت را بررسی و دوباره تلاش کنید.');
            }
            const disposition = response.headers.get('Content-Disposition') || '';
            const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
            const plain = disposition.match(/filename="?([^";]+)/i);
            let name = plain?.[1] || (url.searchParams.get('export') ? 'advertisement-report.xlsx' : 'download');
            if (encoded) {
                try { name = decodeURIComponent(encoded[1].trim()); } catch (_) { /* Use the plain filename. */ }
            }
            const blob = await response.blob();
            const objectURL = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = objectURL;
            link.download = name;
            document.body.append(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(objectURL), 1000);
        } catch (error) {
            showError(error.name === 'AbortError' ? 'دریافت فایل طولانی شد؛ دوباره تلاش کنید.'
                : (error.name === 'Error' ? error.message : 'دریافت فایل ناموفق بود؛ دوباره تلاش کنید.'));
        } finally {
            clearTimeout(timeout);
            finishLoading?.();
        }
    }

    document.addEventListener('click', event => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest('a[href]');
        if (!link || link.hasAttribute('download') || link.hasAttribute('target') || link.closest('[data-native-navigation]')) return;
        const url = new URL(link.href, location.href);
        if (downloadable(url)) {
            event.preventDefault();
            downloadFile(url, link);
            return;
        }
        if (!internal(url)) return;
        if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
        event.preventDefault();
        navigate(url, {trigger: link});
    });

    document.addEventListener('submit', event => {
        const form = event.target;
        if (event.defaultPrevented || !main.contains(form) || form.closest('[data-native-navigation]')) return;
        const button = event.submitter;
        const target = button?.getAttribute('formtarget') || form.target;
        const method = (button?.getAttribute('formmethod') || form.method).toUpperCase();
        const url = new URL(button?.getAttribute('formaction') || form.action, location.href);
        if (target || !['GET', 'POST'].includes(method) || !internal(url)) return;
        const data = new FormData(form);
        if (button?.name) data.append(button.name, button.value);
        if (method === 'GET') url.search = new URLSearchParams(data).toString();
        if (!internal(url)) return;
        event.preventDefault();
        navigate(url, {method, body: method === 'POST' ? data : undefined, preserveScroll: method === 'POST',
            trigger: button || form.querySelector('button[type="submit"], button:not([type])')});
    });

    history.scrollRestoration = 'manual';
    saveScroll();
    window.addEventListener('scroll', () => {
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(saveScroll, 100);
    }, {passive: true});
    function restoreHistory() {
        if (pending?.method === 'POST') {
            pending.historyChanged = true;
            document.addEventListener('sobhe:navigation-end', restoreHistory, {once: true});
            return;
        }
        const url = new URL(location.href);
        const previous = new URL(renderedURL);
        if (!pending && url.pathname === previous.pathname && url.search === previous.search) {
            renderedURL = location.href;
            scrollToDestination(url, history.state?.sobheScroll);
            return;
        }
        navigate(url, {pop: true, position: history.state?.sobheScroll});
    }
    window.addEventListener('popstate', restoreHistory);
    window.addEventListener('hashchange', () => {
        const previous = new URL(renderedURL);
        if (!pending && location.pathname === previous.pathname && location.search === previous.search) {
            renderedURL = location.href;
            saveScroll();
        }
    });
    window.addEventListener('pageshow', () => {
        if (!pending) { main.setAttribute('aria-busy', 'false'); progress.hidden = true; }
    });
})();
