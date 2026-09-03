(function () {
    'use strict';

    var STORAGE_KEY = 'table_density';
    var COMPACT_CLASSES = ['text-xs', 'py-1', 'px-2'];
    var NORMAL_CLASSES  = ['text-sm', 'py-3', 'px-4'];

    function getStored() {
        try { return localStorage.getItem(STORAGE_KEY) || 'comfortable'; } catch (e) { return 'comfortable'; }
    }

    function saveStored(v) {
        try { localStorage.setItem(STORAGE_KEY, v); } catch (e) {}
    }

    function applyDensity(density) {
        var compact = density === 'compact';
        document.querySelectorAll('[data-density-table] tbody td, [data-density-table] thead th').forEach(function (cell) {
            if (compact) {
                cell.classList.remove('py-3', 'px-4', 'text-sm');
                cell.classList.add('py-1', 'px-2', 'text-xs');
            } else {
                cell.classList.remove('py-1', 'px-2', 'text-xs');
                cell.classList.add('py-3', 'px-4', 'text-sm');
            }
        });
        document.querySelectorAll('[data-density-btn]').forEach(function (btn) {
            var isActive = btn.dataset.densityBtn === density;
            btn.classList.toggle('bg-brand-600', isActive);
            btn.classList.toggle('text-white', isActive);
            btn.classList.toggle('bg-white', !isActive);
            btn.classList.toggle('dark:bg-slate-800', !isActive);
            btn.classList.toggle('text-slate-600', !isActive);
            btn.classList.toggle('dark:text-slate-300', !isActive);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        applyDensity(getStored());

        document.querySelectorAll('[data-density-btn]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var density = this.dataset.densityBtn;
                saveStored(density);
                applyDensity(density);
            });
        });
    });
})();
