(function () {
    'use strict';

    function init() {
        var toggle = document.querySelector('[data-navbar-toggle]');
        var menu = document.getElementById('navbar-menu');
        if (!toggle || !menu) return;

        toggle.addEventListener('click', function () {
            var hidden = menu.classList.contains('hidden');
            menu.classList.toggle('hidden', !hidden);
            menu.classList.toggle('flex', hidden);
            menu.classList.toggle('flex-col', hidden);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
