(function () {
    'use strict';

    var STORAGE_KEY_PREFIX = 'table_preset_';

    function getStoredPreset(entity) {
        try { return localStorage.getItem(STORAGE_KEY_PREFIX + entity) || ''; } catch (e) { return ''; }
    }

    function saveStoredPreset(entity, name) {
        try { localStorage.setItem(STORAGE_KEY_PREFIX + entity, name); } catch (e) {}
    }

    function applyPreset(table, presets, name) {
        var preset = null;
        for (var i = 0; i < presets.length; i++) {
            if (presets[i].name === name) { preset = presets[i]; break; }
        }

        var visibleFields = (preset && preset.fields.length > 0) ? preset.fields : null;

        table.querySelectorAll('thead tr th[data-field]').forEach(function (th) {
            var field = th.dataset.field;
            th.style.display = (visibleFields && visibleFields.indexOf(field) === -1) ? 'none' : '';
        });

        table.querySelectorAll('tbody tr').forEach(function (row) {
            row.querySelectorAll('td[data-field]').forEach(function (td) {
                var field = td.dataset.field;
                td.style.display = (visibleFields && visibleFields.indexOf(field) === -1) ? 'none' : '';
            });
        });

        document.querySelectorAll('[data-preset-btn]').forEach(function (btn) {
            var isActive = btn.dataset.presetBtn === name;
            btn.classList.toggle('bg-brand-600', isActive);
            btn.classList.toggle('text-white', isActive);
            btn.classList.toggle('border-brand-600', isActive);
            btn.classList.toggle('text-slate-600', !isActive);
            btn.classList.toggle('dark:text-slate-300', !isActive);
            btn.classList.toggle('border-slate-200', !isActive);
            btn.classList.toggle('dark:border-slate-600', !isActive);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-presets-entity]').forEach(function (container) {
            var entity = container.dataset.presetsEntity;
            var table = document.querySelector('[data-density-table]');
            if (!table) return;

            var scriptId = container.dataset.presetsScriptId;
            var script = scriptId ? document.getElementById(scriptId) : null;
            var presets;
            try { presets = script ? JSON.parse(script.textContent) : []; } catch (e) { return; }

            var stored = getStoredPreset(entity);
            if (stored) applyPreset(table, presets, stored);

            container.querySelectorAll('[data-preset-btn]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var name = this.dataset.presetBtn;
                    saveStoredPreset(entity, name);
                    applyPreset(table, presets, name);
                });
            });
        });
    });
})();
