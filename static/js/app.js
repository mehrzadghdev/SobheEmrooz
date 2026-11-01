(() => {
    'use strict';
    const root = document.documentElement;
    const toggle = document.getElementById('navigation-toggle');
    const backdrop = document.getElementById('sidebar-backdrop');
    root.classList.add('nav-ready');
    function close() {
        root.classList.remove('sidebar-open');
        document.body.classList.remove('drawer-open');
        backdrop.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
    }
    toggle?.addEventListener('click', () => {
        if (window.matchMedia('(min-width:960px)').matches) {
            root.classList.toggle('sidebar-collapsed');
            toggle.setAttribute('aria-expanded', String(!root.classList.contains('sidebar-collapsed')));
        } else {
            const open = !root.classList.contains('sidebar-open');
            root.classList.toggle('sidebar-open', open);
            document.body.classList.toggle('drawer-open', open);
            backdrop.hidden = !open;
            toggle.setAttribute('aria-expanded', String(open));
        }
    });
    backdrop?.addEventListener('click', close);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
    document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
    document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => {
        if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    }));
    const deleteDialog = document.getElementById('ad-delete-dialog');
    let deleteTrigger;
    if (deleteDialog) {
        document.querySelectorAll('[data-delete-ad]').forEach(button => button.addEventListener('click', () => {
            deleteTrigger = button;
            const form = document.getElementById('ad-delete-form');
            form.action = button.dataset.deleteUrl;
            form.elements.namedItem('version').value = button.dataset.adVersion;
            form.elements.namedItem('reason').value = '';
            document.getElementById('ad-delete-title').textContent = `حذف آگهی م/الف ${button.dataset.adReference}`;
            deleteDialog.showModal();
            form.elements.namedItem('reason').focus();
        }));
        deleteDialog.querySelectorAll('[data-dialog-close]').forEach(button => button.addEventListener('click', () => deleteDialog.close()));
        deleteDialog.addEventListener('close', () => deleteTrigger?.focus());
    }
    document.querySelectorAll('[data-select-all]').forEach(button => button.addEventListener('click', () => {
        const checkboxes = [...button.form.querySelectorAll(`input[name="${button.dataset.selectAll}"]`)];
        const checked = !checkboxes.every(box => box.checked);
        checkboxes.forEach(box => { box.checked = checked; });
    }));
    const targets = document.getElementById('targets-form');
    if (targets) {
        function updateTotals() {
            const totals = {local_one:0,local_two:0,national:0};
            targets.querySelectorAll('[data-target-row]').forEach(row => {
                if (!row.querySelector('[data-include]').checked) return;
                const raw = window.SobheDates.latin(row.querySelector('[data-share]').value).replace('٫','.');
                totals[row.querySelector('[data-category]').value] += Math.round((Number(raw) || 0) * 100);
            });
            for (const [key,total] of Object.entries(totals)) {
                const display = targets.querySelector(`[data-share-total="${key}"]`);
                display.textContent = `${(total / 100).toLocaleString('fa-IR')}٪`;
                display.classList.toggle('danger-text', total !== 10000);
            }
        }
        targets.addEventListener('input', updateTotals);
        targets.addEventListener('change', updateTotals);
        updateTotals();
    }
})();
