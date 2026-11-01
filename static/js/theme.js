(() => {
    'use strict';
    const root = document.documentElement;
    let theme = 'light';
    try { theme = localStorage.getItem('sobhe.theme') === 'dark' ? 'dark' : 'light'; } catch (_) {}
    function render() {
        root.dataset.theme = theme;
        const button = document.getElementById('theme-toggle');
        if (button) {
            button.hidden = false;
            button.setAttribute('aria-pressed', String(theme === 'dark'));
            button.title = theme === 'dark' ? 'حالت روشن' : 'حالت تیره';
        }
    }
    render();
    document.addEventListener('DOMContentLoaded', () => {
        render();
        root.dataset.themeReady = 'true';
        document.getElementById('theme-toggle')?.addEventListener('click', () => {
            theme = theme === 'dark' ? 'light' : 'dark';
            render();
            try { localStorage.setItem('sobhe.theme', theme); } catch (_) {}
        });
    });
})();
