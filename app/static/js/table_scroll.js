(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-table-scroll]').forEach(function (container) {
            var table = container.querySelector('.overflow-x-auto');
            if (!table) return;

            var topBar = document.createElement('div');
            topBar.className = 'overflow-x-auto';
            topBar.style.marginBottom = '2px';

            var inner = document.createElement('div');
            inner.style.height = '1px';
            topBar.appendChild(inner);

            container.insertBefore(topBar, table);

            function syncInnerWidth() {
                inner.style.width = table.scrollWidth + 'px';
            }
            syncInnerWidth();

            var syncing = false;
            topBar.addEventListener('scroll', function () {
                if (syncing) return;
                syncing = true;
                table.scrollLeft = topBar.scrollLeft;
                syncing = false;
            });
            table.addEventListener('scroll', function () {
                if (syncing) return;
                syncing = true;
                topBar.scrollLeft = table.scrollLeft;
                syncing = false;
            });

            if (typeof ResizeObserver !== 'undefined') {
                new ResizeObserver(syncInnerWidth).observe(table);
            }
        });
    });
})();
