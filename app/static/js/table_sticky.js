(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-sticky-table]').forEach(function (table) {
            var stickyFields = (table.dataset.stickyTable || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
            if (!stickyFields.length) return;

            var headers = table.querySelectorAll('thead tr th');
            var stickyIndexes = [];

            headers.forEach(function (th, idx) {
                var field = th.dataset.field;
                if (field && stickyFields.indexOf(field) !== -1) {
                    stickyIndexes.push(idx);
                } else if (!field && idx === 0) {
                    stickyIndexes.push(idx);
                }
            });

            function applySticky() {
                var cumulativeLeft = 0;
                stickyIndexes.forEach(function (idx) {
                    var th = headers[idx];
                    if (!th) return;
                    th.style.position = 'sticky';
                    th.style.left = cumulativeLeft + 'px';
                    th.style.zIndex = '10';
                    th.classList.add('bg-slate-50', 'dark:bg-slate-700');
                    cumulativeLeft += th.offsetWidth;

                    table.querySelectorAll('tbody tr').forEach(function (row) {
                        var td = row.cells[idx];
                        if (!td) return;
                        td.style.position = 'sticky';
                        td.style.left = (idx === 0 ? 0 : (function () {
                            var left = 0;
                            for (var i = 0; i < stickyIndexes.indexOf(idx); i++) {
                                var prevTh = headers[stickyIndexes[i]];
                                if (prevTh) left += prevTh.offsetWidth;
                            }
                            return left;
                        })()) + 'px';
                        td.style.zIndex = '5';
                        td.classList.add('bg-white', 'dark:bg-slate-800');
                    });
                });
            }

            applySticky();
            if (typeof ResizeObserver !== 'undefined') {
                new ResizeObserver(applySticky).observe(table);
            }
        });
    });
})();
