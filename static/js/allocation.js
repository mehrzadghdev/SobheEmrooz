(() => {
    'use strict';
    const enhanced = new WeakSet();
    let cleanup;
    function initialize() {
        const form = document.getElementById('advertisement-form');
        if (!form || enhanced.has(form)) return;
        enhanced.add(form);
        const outlet = form.elements.namedItem('outlet_id');
        const volume = form.elements.namedItem('volume');
        // The picker keeps this name on a hidden source and gives its ID to the visible input.
        const allocationDate = form.querySelector('[name="allocation_date"]');
        const token = form.elements.namedItem('preview_token');
        const acknowledgement = document.getElementById('override-confirmed');
        const panel = document.getElementById('allocation-preview');
        const status = document.getElementById('preview-status');
        const save = document.getElementById('save-ad');
        const categories = JSON.parse(form.dataset.categories);
        let manual = Boolean(outlet.value);
        let timer, controller, current, finishPreview;
        function write(id, value) { document.getElementById(id).textContent = value ?? '—'; }
        function show(data) {
            current = data;
            token.value = data.token;
            document.getElementById('preview-results').hidden = !volume.value.trim();
            write('preview-period', `${data.period_year} · فروردین تا اسفند`);
            write('preview-status', data.recovery ? 'پیشنهاد سراسری برای بازگرداندن سهم محلی به ۵۰ درصد' : 'اولویت با رسانه‌های محلی دارای کمترین حجم دریافتی در سال است.');
            write('recommended-name', data.recommended?.name || 'رسانه فعال در گروه موردنیاز وجود ندارد');
            write('recommended-allocated', data.recommended ? `${data.recommended.allocated} کادر` : '—');
            write('recommended-category-percent', data.recommended ? `${data.recommended.category_percent}٪` : '—');
            const recommendedGroup = data.recommended && data.groups[data.recommended.category];
            const selectedGroup = data.selected && data.groups[data.selected.category];
            write('recommended-group-target', recommendedGroup ? `${categories[data.recommended.category]} · ${recommendedGroup.target_percent}٪` : '—');
            write('selected-name', data.selected?.name);
            write('selected-allocated', data.selected ? `${data.selected.allocated} / ${data.selected.projected_allocated}` : '—');
            write('selected-percent', data.selected ? `${data.selected.actual_percent}٪` : '—');
            write('selected-group-target', selectedGroup ? `${categories[data.selected.category]} · ${selectedGroup.target_percent}٪` : '—');
            write('selected-group-current', selectedGroup ? `${selectedGroup.allocated} / ${selectedGroup.expected}` : '—');
            write('selected-group-projected', selectedGroup ? `${selectedGroup.projected_allocated} / ${selectedGroup.projected_expected}` : '—');
            write('projected-local', `${data.projected_local_percent}٪`);
            const warnings = document.getElementById('preview-warnings');
            warnings.replaceChildren();
            data.warnings.forEach(message => {
                const paragraph = document.createElement('p');
                paragraph.className = 'alert warning';
                paragraph.textContent = message;
                warnings.append(paragraph);
            });
            document.getElementById('override-label').hidden = !data.warnings.length;
            acknowledgement.checked = false;
            const chosen = (manual || volume.value.trim()) ? (data.selected?.outlet_id || '') : '';
            const options = data.options.map(option => new Option(`${option.name} · ${categories[option.category]}`, option.id));
            outlet.replaceChildren(new Option('رسانه دریافت‌کننده را انتخاب کنید…', ''), ...options);
            outlet.value = String(chosen);
            document.getElementById('use-recommendation').disabled = !data.recommended;
        }
        async function refresh(trigger) {
            token.value = '';
            acknowledgement.checked = false;
            controller?.abort();
            controller = null;
            finishPreview?.();
            const finishSave = window.SobheLoading?.start(save);
            const finishTrigger = window.SobheLoading?.start(trigger);
            const finish = () => { finishSave?.(); finishTrigger?.(); };
            finishPreview = finish;
            controller = new AbortController();
            const activeController = controller;
            panel.setAttribute('aria-busy', 'true');
            save.disabled = true;
            status.textContent = 'در حال محاسبه سهم و پیشنهاد رسانه…';
            const url = new URL(form.dataset.previewUrl, window.location.origin);
            url.searchParams.set('allocation_date', allocationDate.value || form.dataset.defaultAllocationDate);
            url.searchParams.set('volume', volume.value);
            if (manual && outlet.value) url.searchParams.set('outlet_id', outlet.value);
            if (form.dataset.excludeId) url.searchParams.set('exclude_id', form.dataset.excludeId);
            try {
                let response = await fetch(url, {signal:activeController.signal, headers:{Accept:'application/json'}});
                let data = await response.json();
                // A new period can have a different roster; offer that period's recommendation.
                if (!response.ok && url.searchParams.has('outlet_id') && data.error?.includes('رسانه انتخاب')) {
                    manual = false;
                    url.searchParams.delete('outlet_id');
                    response = await fetch(url, {signal:activeController.signal, headers:{Accept:'application/json'}});
                    data = await response.json();
                }
                if (!response.ok) throw new Error(data.error || 'دریافت پیشنهاد ناموفق بود.');
                if (activeController !== controller || !form.isConnected) return;
                show(data);
            } catch (error) {
                if (activeController === controller && form.isConnected && error.name !== 'AbortError') {
                    status.textContent = error.message || 'اتصال برقرار نشد؛ دوباره تلاش کنید.';
                    document.getElementById('preview-results').hidden = true;
                }
            } finally {
                finish();
                if (finishPreview === finish) finishPreview = null;
                if (activeController === controller && form.isConnected) {
                    panel.setAttribute('aria-busy', 'false');
                    save.disabled = false;
                }
            }
        }
        function schedule() {
            controller?.abort();
            finishPreview?.();
            finishPreview = window.SobheLoading?.start(save);
            controller = null;
            token.value = '';
            acknowledgement.checked = false;
            save.disabled = true;
            panel.setAttribute('aria-busy', 'true');
            clearTimeout(timer);
            timer = setTimeout(refresh, 300);
        }
        // Text inputs already emit input while typing; change on blur would reset a reviewed preview.
        volume.addEventListener('input', schedule);
        allocationDate.addEventListener('input', schedule);
        allocationDate.addEventListener('change', schedule);
        outlet.addEventListener('change', () => { manual = Boolean(outlet.value); schedule(); });
        document.getElementById('use-recommendation').addEventListener('click', () => {
            manual = false;
            outlet.value = String(current?.recommended?.outlet_id || '');
            refresh(document.getElementById('use-recommendation'));
        });
        const initial = document.getElementById('initial-preview');
        if (initial) show(JSON.parse(initial.textContent));
        else if (volume.value) refresh();
        cleanup = () => {
            clearTimeout(timer);
            controller?.abort();
            finishPreview?.();
            finishPreview = null;
            controller = null;
        };
    }
    initialize();
    document.addEventListener('sobhe:page-render', initialize);
    document.addEventListener('sobhe:before-render', () => { cleanup?.(); cleanup = null; });
})();
