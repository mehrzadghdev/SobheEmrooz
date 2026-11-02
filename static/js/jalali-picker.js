(() => {
    'use strict';
    const dates = window.SobheDates;
    if (!dates || !window.HTMLDialogElement) return;
    const errorText = 'تاریخ شمسی معتبر وارد کنید؛ مانند ۲۳ آبان ۱۴۰۳ یا ۱۴۰۳/۰۸/۲۳.';
    let serial = 0;
    let active;
    let displayed;
    const dialog = document.createElement('dialog');
    dialog.className = 'jalali-calendar';
    dialog.setAttribute('aria-label', 'انتخاب تاریخ شمسی');
    dialog.innerHTML = `
        <div class="calendar-heading"><strong>انتخاب تاریخ شمسی</strong><button type="button" class="secondary" data-close aria-label="بستن تقویم">×</button></div>
        <div class="calendar-navigation">
            <button type="button" class="secondary" data-month-step="-1" aria-label="ماه قبل">→</button>
            <select aria-label="ماه">${dates.months.map((month, index) => `<option value="${index + 1}">${month}</option>`).join('')}</select>
            <input type="text" inputmode="numeric" aria-label="سال شمسی" dir="ltr" maxlength="4">
            <button type="button" class="secondary" data-month-step="1" aria-label="ماه بعد">←</button>
        </div>
        <p class="visually-hidden" data-calendar-status role="status" aria-live="polite"></p>
        <div class="calendar-weekdays" aria-hidden="true"><span>ش</span><span>ی</span><span>د</span><span>س</span><span>چ</span><span>پ</span><span>ج</span></div>
        <div class="calendar-days" role="group" aria-label="روزهای ماه"></div>
        <div class="calendar-actions"><button type="button" data-today>امروز</button><button type="button" class="secondary" data-clear>پاک کردن</button></div>`;
    document.body.append(dialog);
    const monthSelect = dialog.querySelector('select');
    const yearInput = dialog.querySelector('input');
    const days = dialog.querySelector('.calendar-days');

    function render() {
        const [year, month] = displayed;
        const first = dates.toGregorian(year, month, 1);
        if (!first) return;
        monthSelect.value = month;
        yearInput.value = dates.persian(year);
        dialog.querySelector('[data-calendar-status]').textContent = `${dates.months[month - 1]} ${dates.persian(year)}`;
        days.replaceChildren();
        const today = dates.today();
        const offset = (new Date(`${first}T12:00:00Z`).getUTCDay() + 1) % 7;
        for (let index = 0; index < offset; index++) days.append(document.createElement('span'));
        for (let day = 1; day <= 31; day++) {
            const iso = dates.toGregorian(year, month, day);
            if (!iso) break;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'secondary';
            button.dataset.iso = iso;
            button.textContent = dates.persian(day);
            button.setAttribute('aria-label', dates.format(iso));
            if (iso === active.source.value.slice(0, 10)) {
                button.classList.add('is-selected');
                button.setAttribute('aria-pressed', 'true');
            }
            if (iso === today) button.setAttribute('aria-current', 'date');
            button.disabled = Boolean((active.source.min && iso < active.source.min.slice(0, 10))
                || (active.source.max && iso > active.source.max.slice(0, 10)));
            days.append(button);
        }
    }
    function select(iso) {
        if (!active?.source.isConnected) return dialog.close();
        active.text.value = iso ? dates.format(iso) : '';
        active.sync();
        dialog.close();
    }
    dialog.addEventListener('close', () => {
        if (active?.text.isConnected) active.text.focus();
        active = null;
    });
    dialog.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.hasAttribute('data-close')) dialog.close();
        else if (button.dataset.iso) select(button.dataset.iso);
        else if (button.hasAttribute('data-today')) select(dates.today());
        else if (button.hasAttribute('data-clear')) select('');
        else if (button.dataset.monthStep) {
            let [year, month] = displayed;
            month += Number(button.dataset.monthStep);
            if (month === 0) { year--; month = 12; }
            if (month === 13) { year++; month = 1; }
            if (year >= 1 && year <= 3000) { displayed = [year, month]; render(); }
        }
    });
    function changeMonth() {
        const year = Number(dates.latin(yearInput.value));
        if (!Number.isInteger(year) || year < 1 || year > 3000) {
            yearInput.value = dates.persian(displayed[0]);
            return;
        }
        displayed = [year, Number(monthSelect.value)];
        render();
    }
    monthSelect.addEventListener('change', changeMonth);
    yearInput.addEventListener('change', changeMonth);
    days.addEventListener('keydown', event => {
        const iso = event.target.dataset.iso;
        const offset = {ArrowLeft: 1, ArrowRight: -1, ArrowUp: -7, ArrowDown: 7}[event.key];
        if (!iso || !offset) return;
        event.preventDefault();
        const next = dates.addDays(iso, offset);
        const parts = dates.fromGregorian(next);
        if (parts[0] < 1 || parts[0] > 3000) return;
        displayed = parts.slice(0, 2);
        render();
        days.querySelector(`[data-iso="${next}"]:not(:disabled)`)?.focus();
    });

    function enhance(source) {
        if (source.dataset.jalaliReady) return;
        source.dataset.jalaliReady = 'true';
        const withTime = source.type === 'datetime-local';
        const initialValue = source.value;
        const required = source.required;
        const wrapper = document.createElement('span');
        wrapper.className = 'jalali-field';
        const controls = document.createElement('span');
        controls.className = 'jalali-controls';
        const text = document.createElement('input');
        text.type = 'text';
        if (source.id) {
            text.id = source.id;
            source.id += '-source';
        }
        text.className = 'jalali-input';
        text.placeholder = '۲۳ آبان ۱۴۰۳ یا ۱۴۰۳/۰۸/۲۳';
        text.autocomplete = 'off';
        text.required = required;
        text.disabled = source.disabled;
        text.readOnly = source.readOnly;
        const hint = document.createElement('small');
        hint.className = 'jalali-hint';
        hint.id = `jalali-hint-${++serial}`;
        hint.setAttribute('aria-live', 'polite');
        text.setAttribute('aria-describedby', hint.id);
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'secondary jalali-open';
        open.textContent = 'تقویم';
        open.setAttribute('aria-label', 'باز کردن تقویم شمسی');
        open.setAttribute('aria-haspopup', 'dialog');
        open.disabled = source.disabled || source.readOnly;
        let time;
        if (withTime) {
            time = document.createElement('input');
            time.type = 'time';
            time.setAttribute('aria-label', 'ساعت');
            time.required = required;
            time.disabled = source.disabled;
            time.readOnly = source.readOnly;
            if (source.step) time.step = source.step;
        }
        const originalType = source.type;
        source.type = 'hidden';
        source.required = false;
        source.before(wrapper);
        controls.append(text, open);
        if (time) controls.append(time);
        wrapper.append(controls, hint, source);
        function sync() {
            const value = text.value.trim();
            if (time) {
                time.required = required || Boolean(value);
                text.required = required || Boolean(time.value);
            }
            const iso = value ? dates.parse(value) : '';
            let error = value && !iso ? errorText : '';
            const combined = iso ? iso + (time ? `T${time.value}` : '') : '';
            if (iso && ((source.min && combined < source.min) || (source.max && combined > source.max))) {
                error = 'تاریخ انتخاب‌شده خارج از بازه مجاز است.';
            }
            text.setCustomValidity(error);
            text.setAttribute('aria-invalid', String(Boolean(error)));
            const previous = source.value;
            source.value = !error && iso && (!time || time.value) ? combined : '';
            if (source.value !== previous) source.dispatchEvent(new Event('input', {bubbles: true}));
            hint.textContent = error;
            hint.hidden = !error;
            hint.classList.toggle('is-error', Boolean(error));
        }
        function refresh() {
            text.value = dates.format(source.value.slice(0, 10));
            if (time) time.value = source.value.split('T')[1] || '';
            sync();
        }
        refresh();
        text.addEventListener('input', sync);
        text.addEventListener('blur', () => {
            const iso = dates.parse(text.value);
            if (iso && text.validity.valid) text.value = dates.format(iso);
        });
        if (time) time.addEventListener('input', sync);
        source.addEventListener('change', refresh);
        source.form?.addEventListener('reset', () => setTimeout(() => {
            source.value = initialValue;
            refresh();
        }));
        open.addEventListener('click', () => {
            active = {source, text, sync};
            const iso = dates.parse(text.value) || dates.today();
            displayed = dates.fromGregorian(iso).slice(0, 2);
            render();
            dialog.showModal();
            (days.querySelector('.is-selected:not(:disabled)') || days.querySelector('button:not(:disabled)'))?.focus();
        });
        source.dataset.originalType = originalType;
    }
    function updateHeaderDate() {
        document.querySelectorAll('time[data-header-today]').forEach(element => {
            const value = dates.today();
            element.setAttribute('datetime', value);
            element.textContent = dates.format(value);
            element.title = `میلادی: ${value}`;
        });
    }
    function initialize() {
        if (dialog.open) dialog.close();
        document.querySelectorAll('input[type="date"], input[type="datetime-local"]').forEach(enhance);
        updateHeaderDate();
        document.querySelectorAll('time[data-jalali]').forEach(element => {
            const value = element.getAttribute('datetime');
            const formatted = dates.format(value.slice(0, 10));
            if (formatted) {
                element.textContent = formatted + (value.length > 10 ? ` · ${dates.persian(value.slice(11, 16))}` : '');
                element.title = `میلادی: ${value.replace('T', ' ')}`;
            }
        });
    }
    initialize();
    document.addEventListener('sobhe:page-render', initialize);
    document.addEventListener('sobhe:before-render', () => { if (dialog.open) dialog.close(); });
    setInterval(updateHeaderDate, 60000);
})();
