(() => {
    'use strict';
    const root = document.documentElement;
    const toggle = document.getElementById('navigation-toggle');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const backdrop = document.getElementById('sidebar-backdrop');
    const desktop = window.matchMedia('(min-width:960px)');
    root.classList.add('nav-ready');
    function syncNavigation() {
        const expanded = desktop.matches ? !root.classList.contains('sidebar-collapsed')
            : root.classList.contains('sidebar-open');
        toggle?.setAttribute('aria-expanded', String(expanded));
        toggle?.setAttribute('aria-label', expanded ? 'بستن فهرست' : 'نمایش فهرست');
        if (sidebarToggle) {
            const label = desktop.matches ? (expanded ? 'جمع کردن فهرست' : 'باز کردن فهرست') : 'بستن فهرست';
            sidebarToggle.setAttribute('aria-expanded', String(expanded));
            sidebarToggle.setAttribute('aria-label', label);
            sidebarToggle.title = label;
        }
    }
    function close() {
        const restoreFocus = root.classList.contains('sidebar-open')
            && document.getElementById('side-navigation')?.contains(document.activeElement);
        root.classList.remove('sidebar-open');
        document.body.classList.remove('drawer-open');
        if (backdrop) backdrop.hidden = true;
        syncNavigation();
        if (restoreFocus) toggle?.focus();
    }
    function toggleNavigation() {
        if (desktop.matches) {
            root.classList.toggle('sidebar-collapsed');
            syncNavigation();
        } else if (root.classList.contains('sidebar-open')) {
            close();
        } else {
            root.classList.add('sidebar-open');
            document.body.classList.add('drawer-open');
            if (backdrop) backdrop.hidden = false;
            syncNavigation();
        }
    }
    toggle?.addEventListener('click', toggleNavigation);
    sidebarToggle?.addEventListener('click', toggleNavigation);
    desktop.addEventListener('change', close);
    syncNavigation();
    backdrop?.addEventListener('click', close);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
    const confirmationDialog = document.getElementById('confirmation-dialog');
    const confirmationAccept = document.getElementById('confirmation-accept');
    let pendingConfirmation;
    let confirmedSubmission;
    function dismissConfirmation() {
        pendingConfirmation = null;
        confirmationDialog?.close();
    }
    document.addEventListener('submit', event => {
        const form = event.target;
        const submitter = event.submitter;
        if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
        if (confirmedSubmission?.form === form && confirmedSubmission.submitter === submitter) {
            confirmedSubmission = null;
            return;
        }
        const setting = name => submitter?.dataset[name] ?? form.dataset[name];
        const message = setting('confirm');
        if (!message) return;
        event.preventDefault();
        if (!confirmationDialog || confirmationDialog.open) return;
        const variant = setting('confirmVariant') || 'primary';
        document.getElementById('confirmation-title').textContent = setting('confirmTitle') || 'تأیید عملیات';
        document.getElementById('confirmation-description').textContent = message;
        confirmationDialog.dataset.variant = variant;
        confirmationAccept.textContent = setting('confirmLabel') || 'تأیید';
        confirmationAccept.classList.toggle('danger', variant === 'danger');
        confirmationAccept.disabled = false;
        pendingConfirmation = {form, submitter, trigger: submitter || document.activeElement};
        confirmationDialog.showModal();
        document.getElementById('confirmation-cancel').focus();
    });
    confirmationAccept?.addEventListener('click', () => {
        const pending = pendingConfirmation;
        if (!pending) return;
        const {form, submitter} = pending;
        dismissConfirmation();
        if (!form.isConnected || (submitter && (!submitter.isConnected || submitter.form !== form || submitter.matches(':disabled')))) return;
        confirmationAccept.disabled = true;
        confirmedSubmission = {form, submitter};
        try {
            // Resume the original submission so validation, button values, and navigation still apply.
            if (submitter) form.requestSubmit(submitter);
            else form.requestSubmit();
        } finally {
            confirmedSubmission = null;
        }
    });
    confirmationDialog?.addEventListener('close', () => {
        const trigger = pendingConfirmation?.trigger;
        pendingConfirmation = null;
        if (trigger?.isConnected) trigger.focus();
    });
    document.addEventListener('sobhe:navigation-start', dismissConfirmation);
    document.addEventListener('sobhe:before-render', dismissConfirmation);
    window.addEventListener('pagehide', dismissConfirmation);
    let deleteTrigger;
    document.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.hasAttribute('data-print')) window.print();
        if (button.hasAttribute('data-delete-ad')) {
            const deleteDialog = document.getElementById('ad-delete-dialog');
            if (!deleteDialog) return;
            deleteTrigger = button;
            const form = document.getElementById('ad-delete-form');
            form.action = button.dataset.deleteUrl;
            form.elements.namedItem('version').value = button.dataset.adVersion;
            form.elements.namedItem('reason').value = '';
            document.getElementById('ad-delete-title').textContent = `حذف آگهی م/الف ${button.dataset.adReference}`;
            deleteDialog.showModal();
            form.elements.namedItem('reason').focus();
        }
        if (button.hasAttribute('data-dialog-close')) button.closest('dialog')?.close();
        if (button.hasAttribute('data-select-all') && button.form) {
            const checkboxes = [...button.form.querySelectorAll(`input[name="${button.dataset.selectAll}"]`)];
            const checked = !checkboxes.every(box => box.checked);
            checkboxes.forEach(box => { box.checked = checked; });
            button.form.dispatchEvent(new Event('change', {bubbles: true}));
        }
    });
    document.addEventListener('close', event => {
        if (event.target.id === 'ad-delete-dialog' && deleteTrigger?.isConnected) deleteTrigger.focus();
    }, true);
    const enhancedMedia = new WeakSet();
    const enhancedReports = new WeakSet();
    function initialize() {
        const report = document.getElementById('report-filter-form');
        if (report && !enhancedReports.has(report)) {
            enhancedReports.add(report);
            const updateColumns = () => {
                const count = report.querySelector('[data-column-count]');
                if (count) count.textContent = `${report.querySelectorAll('[name="columns"]:checked').length.toLocaleString('fa-IR')} ستون`;
            };
            report.addEventListener('change', updateColumns);
            updateColumns();
        }
        const media = document.querySelector('[data-media-form]');
        if (!media || enhancedMedia.has(media)) return;
        enhancedMedia.add(media);
        const mediaType = media.elements.namedItem('media_type');
        const localGroup = media.elements.namedItem('local_group');
        function updateMediaType() {
            const local = mediaType.value === 'local';
            media.querySelector('[data-local-group]').hidden = !local;
            localGroup.required = local;
            localGroup.disabled = !local;
        }
        mediaType.addEventListener('change', updateMediaType);
        updateMediaType();
    }
    initialize();
    document.addEventListener('sobhe:page-render', initialize);
    document.addEventListener('sobhe:navigation-end', close);
})();
