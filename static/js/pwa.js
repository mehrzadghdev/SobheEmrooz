(() => {
    'use strict';
    const script = document.currentScript;
    const button = document.getElementById('pwa-install');
    const help = document.getElementById('pwa-install-help');
    const connection = document.getElementById('pwa-connection');
    const standalone = window.matchMedia('(display-mode: standalone)');
    const isInstalled = () => standalone.matches || navigator.standalone === true;
    const isAppleMobile = /iPad|iPhone|iPod/.test(navigator.userAgent)
        || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    let installPrompt;

    function showInstall() {
        if (button) button.hidden = isInstalled() || !window.isSecureContext || (!installPrompt && !isAppleMobile);
    }
    function showConnection() {
        if (connection) connection.hidden = navigator.onLine !== false;
    }
    window.addEventListener('beforeinstallprompt', event => {
        event.preventDefault();
        installPrompt = event;
        showInstall();
    });
    window.addEventListener('appinstalled', () => {
        installPrompt = null;
        if (button) button.hidden = true;
        if (help?.open) help.close();
    });
    standalone.addEventListener?.('change', showInstall);
    window.addEventListener('online', showConnection);
    window.addEventListener('offline', showConnection);
    showInstall();
    showConnection();

    button?.addEventListener('click', async () => {
        if (installPrompt) {
            const prompt = installPrompt;
            installPrompt = null;
            button.disabled = true;
            try {
                await prompt.prompt();
                await prompt.userChoice;
            } catch (_) { /* The browser can dismiss or revoke an installation prompt. */ }
            finally { button.disabled = false; showInstall(); }
        } else if (isAppleMobile && help?.showModal) {
            help.showModal();
        }
    });
    document.querySelectorAll('[data-pwa-close]').forEach(close => {
        close.addEventListener('click', () => help?.close());
    });
    document.querySelector('[data-pwa-retry]')?.addEventListener('click', () => location.reload());

    if ('serviceWorker' in navigator && window.isSecureContext && script?.dataset.workerUrl) {
        const register = () => navigator.serviceWorker.register(script.dataset.workerUrl, {
            scope: script.dataset.scope, updateViaCache: 'none',
        }).catch(() => { /* Browsing still works if installation is unavailable. */ });
        if (document.readyState === 'complete') register();
        else window.addEventListener('load', register, {once: true});
    }
})();
