(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-width-pct]').forEach(function (el) {
            el.style.width = el.dataset.widthPct + '%';
        });
    });
})();
