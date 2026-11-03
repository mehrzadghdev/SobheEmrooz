(() => {
    'use strict';
    const active = new Map();
    const nativeForms = new WeakSet();
    function restoreAttribute(control, name, value) {
        if (value === null) control.removeAttribute(name);
        else control.setAttribute(name, value);
    }
    function start(control) {
        if (!(control instanceof Element) || control.closest('#side-navigation')) return () => {};
        let state = active.get(control);
        if (!state) {
            state = {tokens: new Set(), busy: control.getAttribute('aria-busy'),
                disabled: control.getAttribute('aria-disabled'), label: control.getAttribute('aria-label')};
            active.set(control, state);
            control.classList.add('is-loading');
            control.setAttribute('aria-busy', 'true');
            control.setAttribute('aria-disabled', 'true');
            if (state.label === null) control.setAttribute('aria-label',
                control.title || control.textContent.trim() || control.value || 'در حال بارگذاری');
        }
        const token = {};
        state.tokens.add(token);
        return () => {
            if (!state.tokens.delete(token) || state.tokens.size || active.get(control) !== state) return;
            control.classList.remove('is-loading');
            restoreAttribute(control, 'aria-busy', state.busy);
            restoreAttribute(control, 'aria-disabled', state.disabled);
            restoreAttribute(control, 'aria-label', state.label);
            active.delete(control);
        };
    }
    function reset() {
        for (const [control, state] of active) {
            control.classList.remove('is-loading');
            restoreAttribute(control, 'aria-busy', state.busy);
            restoreAttribute(control, 'aria-disabled', state.disabled);
            restoreAttribute(control, 'aria-label', state.label);
        }
        active.clear();
    }
    window.SobheLoading = {start, reset};

    // Keep names and values in native form submissions; aria-disabled locks the
    // control without excluding its value from the browser's form entry list.
    document.addEventListener('click', event => {
        const control = event.target.closest('button, a, input[type="submit"]');
        if (control && active.has(control)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    document.addEventListener('submit', event => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (nativeForms.has(form)) {
            event.preventDefault();
            return;
        }
        const submitter = event.submitter || form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
        const method = (submitter?.getAttribute('formmethod') || form.method).toUpperCase();
        const target = submitter?.getAttribute('formtarget') || form.target;
        if (method === 'DIALOG' || (target && target !== '_self')) return;
        // Confirmations and client navigation get first refusal. Invalid forms
        // never reach submit, and canceled confirmations never show a spinner.
        setTimeout(() => {
            if (event.defaultPrevented || !form.isConnected) return;
            nativeForms.add(form);
            start(submitter);
        });
    });
    document.addEventListener('click', event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest('a[href]');
        if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)
                || (url.pathname === location.pathname && url.search === location.search && url.hash)) return;
        setTimeout(() => { if (!event.defaultPrevented && link.isConnected) start(link); });
    });
    window.addEventListener('pagehide', reset);
    window.addEventListener('pageshow', event => {
        if (!event.persisted) return;
        reset();
        document.querySelectorAll('form').forEach(form => nativeForms.delete(form));
    });
})();
