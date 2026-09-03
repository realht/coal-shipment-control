(function () {
    'use strict';

    var STORAGE_KEY = 'theme';
    var root = document.documentElement;

    function getStoredTheme() {
        try {
            var value = localStorage.getItem(STORAGE_KEY);
            if (value === 'dark' || value === 'light') return value;
        } catch (e) {}
        return 'light';
    }

    function applyTheme(theme) {
        root.classList.toggle('dark', theme === 'dark');
        root.classList.toggle('light', theme !== 'dark');
    }

    function toggleTheme() {
        var next = root.classList.contains('dark') ? 'light' : 'dark';
        applyTheme(next);
        try { localStorage.setItem(STORAGE_KEY, next); } catch (e) {}
    }

    function bindButtons() {
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.addEventListener('click', toggleTheme);
        });
    }

    applyTheme(getStoredTheme());

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindButtons);
    } else {
        bindButtons();
    }
})();
