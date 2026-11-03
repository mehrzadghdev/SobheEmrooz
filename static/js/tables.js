(() => {
    'use strict';
    const collator = new Intl.Collator('fa', {numeric: true, sensitivity: 'base'});
    const normalize = value => value.replace(/[۰-۹٠-٩]/g, digit =>
        String('۰۱۲۳۴۵۶۷۸۹'.includes(digit) ? '۰۱۲۳۴۵۶۷۸۹'.indexOf(digit) : '٠١٢٣٤٥٦٧٨٩'.indexOf(digit)))
        .replace(/ي/g, 'ی').replace(/ك/g, 'ک').replace(/\u200c/g, ' ').trim().toLocaleLowerCase('fa');
    function value(cell) {
        if (!cell) return '';
        if (cell.dataset.sortValue !== undefined) return normalize(cell.dataset.sortValue);
        const input = cell.querySelector('input:not([type=hidden]):not([type=checkbox]), select');
        return normalize(input ? input.value : cell.textContent);
    }
    function compare(left, right) {
        const numeric = text => text.replace(/[,٬\s٪%]/g, '').replace(/٫/g, '.');
        const a = numeric(left), b = numeric(right);
        if (/^-?\d+(\.\d+)?$/.test(a) && /^-?\d+(\.\d+)?$/.test(b)) return Number(a) - Number(b);
        return collator.compare(left, right);
    }
    function initialize() {
        document.querySelectorAll('main table:not([data-server-table]):not([data-enhanced])').forEach(table => {
            const body = table.tBodies[0], header = table.tHead?.rows[0];
            if (!body || !header) return;
            table.dataset.enhanced = 'true';
            const rows = [...body.rows].filter(row => !row.querySelector('.empty-cell'));
            const empty = [...body.rows].find(row => row.querySelector('.empty-cell'));
            let page = 1, pageSize = 15, sortIndex = -1, ascending = true;
            const controls = document.createElement('div');
            controls.className = 'table-controls no-print';
            controls.innerHTML = '<label>جستجو در جدول<input type="search" placeholder="جستجو…" maxlength="200"></label><label>تعداد در صفحه<select><option>15</option><option>30</option><option>50</option><option>100</option></select></label>';
            const search = controls.querySelector('input'), size = controls.querySelector('select');
            const nav = document.createElement('nav');
            nav.className = 'pagination table-pagination no-print';
            nav.setAttribute('aria-label', 'صفحه‌های جدول');
            const buttons = ['اولین', 'قبلی', 'بعدی', 'آخرین'].map(label => {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'secondary'; button.textContent = label;
                return button;
            });
            const status = document.createElement('span');
            status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
            nav.append(buttons[0], buttons[1], status, buttons[2], buttons[3]);
            const wrapper = table.closest('.table-wrap') || table;
            wrapper.before(controls); wrapper.after(nav);
            const noResults = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = header.cells.length; cell.className = 'empty-cell';
            cell.textContent = 'نتیجه‌ای پیدا نشد.'; noResults.append(cell); body.append(noResults);
            let pages = 1;
            function render() {
                const term = normalize(search.value);
                let matching = rows.filter(row => [...row.cells].some(cell => value(cell).includes(term)));
                if (sortIndex >= 0) matching.sort((a, b) => (ascending ? 1 : -1) * compare(value(a.cells[sortIndex]), value(b.cells[sortIndex])));
                pages = Math.max(1, Math.ceil(matching.length / pageSize));
                page = Math.min(page, pages);
                const included = new Set(matching), visible = new Set(matching.slice((page - 1) * pageSize, page * pageSize));
                rows.forEach(row => {
                    row.hidden = !visible.has(row);
                    row.classList.toggle('table-filtered-out', !included.has(row));
                });
                matching.forEach(row => body.append(row));
                if (empty) empty.hidden = rows.length > 0;
                noResults.hidden = !rows.length || matching.length > 0;
                status.textContent = `صفحه ${page.toLocaleString('fa')} از ${pages.toLocaleString('fa')} · ${matching.length.toLocaleString('fa')} مورد`;
                buttons[0].disabled = buttons[1].disabled = page === 1;
                buttons[2].disabled = buttons[3].disabled = page === pages;
            }
            buttons.forEach((button, index) => button.addEventListener('click', () => {
                page = [1, page - 1, page + 1, pages][index]; render();
            }));
            [...header.cells].forEach((heading, index) => {
                if (heading.classList.contains('actions-column') || ['عملیات', 'جزئیات'].includes(heading.textContent.trim())) return;
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'table-sort secondary';
                const label = heading.textContent.trim();
                button.textContent = `${label} ↕`; heading.replaceChildren(button);
                heading.scope = 'col'; heading.setAttribute('aria-sort', 'none');
                button.addEventListener('click', () => {
                    ascending = sortIndex === index ? !ascending : true; sortIndex = index; page = 1;
                    [...header.cells].forEach(other => {
                        if (!other.querySelector('.table-sort')) return;
                        other.setAttribute('aria-sort', other === heading ? (ascending ? 'ascending' : 'descending') : 'none');
                        const control = other.querySelector('.table-sort');
                        control.textContent = control.textContent.replace(/ [↕↑↓]$/, '') + (other === heading ? (ascending ? ' ↑' : ' ↓') : ' ↕');
                    }); render();
                });
            });
            search.addEventListener('input', () => { page = 1; render(); });
            size.addEventListener('change', () => { pageSize = Number(size.value); page = 1; render(); });
            render();
        });
    }
    initialize();
    document.addEventListener('sobhe:page-render', initialize);
})();
