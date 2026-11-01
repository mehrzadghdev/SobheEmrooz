(() => {
    'use strict';
    const form = document.getElementById('advertisement-form');
    if (!form) return;
    const outlet = form.elements.namedItem('outlet_id');
    const volume = form.elements.namedItem('volume');
    const allocationDate = form.elements.namedItem('allocation_date');
    const token = form.elements.namedItem('preview_token');
    const acknowledgement = document.getElementById('override-confirmed');
    const panel = document.getElementById('allocation-preview');
    const status = document.getElementById('preview-status');
    const save = document.getElementById('save-ad');
    const categories = JSON.parse(form.dataset.categories);
    let manual = Boolean(outlet.value);
    let timer, controller, current;
    function write(id, value) { document.getElementById(id).textContent = value ?? '—'; }
    function show(data) {
        current = data;
        token.value = data.token;
        document.getElementById('preview-results').hidden = false;
        write('preview-period', `${data.period_year} · ${data.period_half === 1 ? 'فروردین تا شهریور' : 'مهر تا اسفند'}`);
        write('preview-status', data.recovery ? 'پیشنهاد سراسری برای بازگرداندن سهم محلی به ۵۰ درصد' : 'اولویت با رسانه‌های محلی و رسانه‌های کمتر از سهم هدف است.');
        write('recommended-name', data.recommended?.name || 'رسانه واجد سهم فعال در گروه موردنیاز وجود ندارد');
        write('recommended-allocated', data.recommended ? `${data.recommended.allocated} کادر` : '—');
        write('recommended-category-percent', data.recommended ? `${data.recommended.category_percent}٪` : '—');
        write('recommended-target', data.recommended ? `${data.recommended.target_share}٪` : '—');
        write('selected-name', data.selected?.name);
        write('selected-allocated', data.selected?.allocated);
        write('selected-percent', data.selected ? `${data.selected.actual_percent}٪ / ${data.selected.target_percent}٪` : '—');
        write('selected-quota', data.selected ? `${data.selected.expected} / ${data.selected.headroom}` : '—');
        write('selected-projected', data.selected ? `${data.selected.projected_allocated} / ${data.selected.projected_expected}` : '—');
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
        const chosen = data.selected?.outlet_id || '';
        const options = data.options.map(option => new Option(`${option.name} · ${categories[option.category]}`, option.id));
        outlet.replaceChildren(new Option('رسانه دریافت‌کننده را انتخاب کنید…', ''), ...options);
        outlet.value = String(chosen);
        document.getElementById('use-recommendation').disabled = !data.recommended;
    }
    async function refresh() {
        token.value = '';
        acknowledgement.checked = false;
        controller?.abort();
        controller = null;
        if (!volume.value.trim() || !allocationDate.value) {
            status.textContent = 'برای دریافت پیشنهاد، تاریخ تخصیص و حجم آگهی را وارد کنید.';
            document.getElementById('preview-results').hidden = true;
            save.disabled = false;
            panel.setAttribute('aria-busy', 'false');
            return;
        }
        controller = new AbortController();
        const activeController = controller;
        panel.setAttribute('aria-busy', 'true');
        save.disabled = true;
        status.textContent = 'در حال محاسبه سهم و پیشنهاد رسانه…';
        const url = new URL(form.dataset.previewUrl, window.location.origin);
        url.searchParams.set('allocation_date', allocationDate.value);
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
            if (activeController !== controller) return;
            show(data);
        } catch (error) {
            if (error.name !== 'AbortError') {
                status.textContent = error.message || 'اتصال برقرار نشد؛ دوباره تلاش کنید.';
                document.getElementById('preview-results').hidden = true;
            }
        } finally {
            if (activeController === controller) {
                panel.setAttribute('aria-busy', 'false');
                save.disabled = false;
            }
        }
    }
    function schedule() {
        controller?.abort();
        controller = null;
        token.value = '';
        acknowledgement.checked = false;
        save.disabled = true;
        panel.setAttribute('aria-busy', 'true');
        clearTimeout(timer);
        timer = setTimeout(refresh, 300);
    }
    [volume,allocationDate].forEach(input => { input.addEventListener('input', schedule); input.addEventListener('change', schedule); });
    outlet.addEventListener('change', () => { manual = Boolean(outlet.value); schedule(); });
    document.getElementById('use-recommendation').addEventListener('click', () => {
        manual = false;
        outlet.value = String(current?.recommended?.outlet_id || '');
        refresh();
    });
    const initial = document.getElementById('initial-preview');
    if (initial) show(JSON.parse(initial.textContent));
    else if (volume.value && allocationDate.value) refresh();
})();
