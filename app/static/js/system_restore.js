(function () {
    'use strict';

    function initRestoreIncrementalFilter() {
        var fullSelect = document.getElementById('restore-full-backup');
        var incrementalSelect = document.getElementById('restore-incremental-backup');
        var dataElement = document.getElementById('restore-incremental-entries');
        if (!fullSelect || !incrementalSelect || !dataElement) {
            return;
        }

        var incrementalEntries = [];
        try {
            incrementalEntries = JSON.parse(dataElement.textContent);
        } catch (e) {
            incrementalEntries = [];
        }

        function addOption(value, text, disabled) {
            var option = document.createElement('option');
            option.value = value;
            option.textContent = text;
            option.disabled = Boolean(disabled);
            incrementalSelect.appendChild(option);
        }

        function refreshIncrementalOptions() {
            var selectedOption = fullSelect.options[fullSelect.selectedIndex];
            var manifestPath = selectedOption ? selectedOption.dataset.manifestPath : '';
            var matchingEntries;

            incrementalSelect.innerHTML = '';

            if (!manifestPath) {
                addOption('', 'Сначала выберите full backup', false);
                incrementalSelect.disabled = true;
                return;
            }

            matchingEntries = incrementalEntries.filter(function (entry) {
                return entry.baseline_manifest === manifestPath;
            });

            if (!matchingEntries.length) {
                addOption('', 'Нет incremental для выбранного full backup', false);
                incrementalSelect.disabled = true;
                return;
            }

            addOption('', 'Без incremental', false);
            matchingEntries.forEach(function (entry) {
                addOption(entry.key, entry.created_at + ' — incremental', false);
            });
            incrementalSelect.disabled = false;
        }

        fullSelect.addEventListener('change', refreshIncrementalOptions);
        refreshIncrementalOptions();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initRestoreIncrementalFilter);
    } else {
        initRestoreIncrementalFilter();
    }
})();
