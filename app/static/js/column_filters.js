(function() {
    const popup = document.createElement('div');
    popup.id = 'col-filter-popup';
    popup.className = 'hidden fixed flex flex-col col-filter-popup bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-600 rounded-lg shadow-xl';

    const tplValue = `
        <div class="px-3 pb-2 pt-2 border-b border-slate-100 dark:border-slate-700 space-y-1.5">
            <input id="popup-search" type="text" placeholder="Поиск..."
                   class="w-full px-2 py-1 text-xs rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-brand-400">
            <label class="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400 cursor-pointer">
                <input type="checkbox" id="popup-select-all" class="rounded border-slate-300 dark:border-slate-600">
                Выбрать все
            </label>
        </div>
        <div id="popup-list" class="px-3 py-1.5 space-y-1 col-filter-popup-list"></div>
        <div id="popup-error" class="hidden px-3 pt-1 text-xs text-red-600 dark:text-red-300"></div>
        <div class="px-3 pt-2 border-t border-slate-100 dark:border-slate-700 flex gap-2 col-filter-popup-actions">
            <button id="popup-reset" class="flex-1 px-3 py-1.5 text-xs font-medium border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 rounded-md transition-colors">Сбросить</button>
            <button id="popup-apply" class="flex-1 px-3 py-1.5 text-xs font-medium bg-brand-600 hover:bg-brand-700 text-white rounded-md transition-colors">Применить</button>
        </div>`;

    const tplRange = `
        <div class="px-3 pt-2 pb-2 space-y-2">
            <div class="space-y-1">
                <label class="text-xs text-slate-500 dark:text-slate-400">От</label>
                <input id="popup-from" type="text" placeholder="—"
                       class="w-full px-2 py-1 text-xs rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-brand-400">
            </div>
            <div class="space-y-1">
                <label class="text-xs text-slate-500 dark:text-slate-400">До</label>
                <input id="popup-to" type="text" placeholder="—"
                       class="w-full px-2 py-1 text-xs rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-brand-400">
            </div>
        </div>
        <div class="px-3 pt-1 border-t border-slate-100 dark:border-slate-700 flex gap-2 col-filter-popup-actions">
            <button id="popup-reset" class="flex-1 px-3 py-1.5 text-xs font-medium border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 rounded-md transition-colors">Сбросить</button>
            <button id="popup-apply" class="flex-1 px-3 py-1.5 text-xs font-medium bg-brand-600 hover:bg-brand-700 text-white rounded-md transition-colors">Применить</button>
        </div>`;

    const tplText = `
        <div class="px-3 pt-2 pb-2 space-y-2">
            <div class="space-y-1">
                <label class="text-xs text-slate-500 dark:text-slate-400">Текст</label>
                <input id="popup-text" type="text" placeholder="Введите текст"
                       class="w-full px-2 py-1 text-xs rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-brand-400">
            </div>
        </div>
        <div class="px-3 pt-1 border-t border-slate-100 dark:border-slate-700 flex gap-2 col-filter-popup-actions">
            <button id="popup-reset" class="flex-1 px-3 py-1.5 text-xs font-medium border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 rounded-md transition-colors">Сбросить</button>
            <button id="popup-apply" class="flex-1 px-3 py-1.5 text-xs font-medium bg-brand-600 hover:bg-brand-700 text-white rounded-md transition-colors">Применить</button>
        </div>`;

    document.body.appendChild(popup);

    let activeField = null;
    let activeType = 'value';
    let allVals = [];
    let filterHasMore = false;
    let filterUrl = null;
    let searchDebounceTimer = null;
    const filterLimitSource = document.querySelector('[data-filter-query-safe-limit]');
    const parsedFilterLimit = filterLimitSource ? parseInt(filterLimitSource.dataset.filterQuerySafeLimit || '', 10) : NaN;
    const filterQuerySafeLimit = Number.isFinite(parsedFilterLimit) ? parsedFilterLimit : 3694;

    async function fetchFilterValues(url, q) {
        const fetchUrl = q ? `${url}?q=${encodeURIComponent(q)}` : url;
        try {
            const resp = await fetch(fetchUrl);
            if (!resp.ok) return { values: [], hasMore: false };
            const data = await resp.json();
            return { values: data.values || [], hasMore: data.has_more === true };
        } catch (e) {
            return { values: [], hasMore: false };
        }
    }

    function getElements() {
        return {
            searchInput: popup.querySelector('#popup-search'),
            selectAll: popup.querySelector('#popup-select-all'),
            popupList: popup.querySelector('#popup-list'),
            applyBtn: popup.querySelector('#popup-apply'),
            resetBtn: popup.querySelector('#popup-reset'),
            errorBox: popup.querySelector('#popup-error'),
            fromInput: popup.querySelector('#popup-from'),
            toInput: popup.querySelector('#popup-to'),
            textInput: popup.querySelector('#popup-text'),
        };
    }

    function clearFilterError() {
        const { errorBox } = getElements();
        if (!errorBox) return;
        errorBox.textContent = '';
        errorBox.classList.add('hidden');
    }

    function showFilterError(message) {
        const { errorBox } = getElements();
        if (!errorBox) return;
        errorBox.textContent = message;
        errorBox.classList.remove('hidden');
    }

    function applySearchParams(p) {
        const newSearch = p.toString();
        const newUrl = `${window.location.pathname}${newSearch ? '?' + newSearch : ''}`;
        if (newUrl.length > filterQuerySafeLimit) {
            showFilterError('Выбрано слишком много значений для фильтра. Сократите выбор или используйте поиск.');
            return;
        }
        window.location.search = newSearch;
    }

    function renderList(vals, checked) {
        const { popupList, selectAll } = getElements();
        if (!popupList) return;
        popupList.textContent = '';
        vals.forEach(v => {
            const label = document.createElement('label');
            label.className = 'flex items-center gap-2 text-xs text-slate-700 dark:text-slate-300 cursor-pointer hover:text-brand-600 dark:hover:text-brand-400';
            const cb = document.createElement('input');
            cb.type = 'checkbox'; cb.value = v; cb.checked = checked.includes(v);
            cb.className = 'rounded border-slate-300 dark:border-slate-600 text-brand-600';
            const span = document.createElement('span');
            span.className = 'truncate'; span.title = v; span.textContent = v;
            label.appendChild(cb); label.appendChild(span);
            popupList.appendChild(label);
        });
        if (selectAll) {
            const all = [...popupList.querySelectorAll('input[type=checkbox]')];
            selectAll.checked = all.length > 0 && all.every(cb => cb.checked);
        }
    }

    function bindValueEvents() {
        const { searchInput, selectAll, popupList, applyBtn, resetBtn } = getElements();

        if (selectAll) {
            selectAll.addEventListener('change', function() {
                clearFilterError();
                if (popupList) popupList.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = this.checked; });
            });
        }

        if (popupList) {
            popupList.addEventListener('change', function() {
                clearFilterError();
                if (!selectAll) return;
                const all = [...popupList.querySelectorAll('input[type=checkbox]')];
                selectAll.checked = all.length > 0 && all.every(cb => cb.checked);
            });
        }

        if (searchInput) {
            searchInput.addEventListener('input', function() {
                clearFilterError();
                const q = this.value;
                const currentChecked = popupList ? [...popupList.querySelectorAll('input[type=checkbox]:checked')].map(c => c.value) : [];
                clearTimeout(searchDebounceTimer);
                if (filterUrl) {
                    searchDebounceTimer = setTimeout(() => {
                        fetchFilterValues(filterUrl, q).then(data => {
                            allVals = data.values;
                            filterHasMore = data.hasMore;
                            renderList(allVals, currentChecked);
                        });
                    }, 300);
                } else {
                    const qLower = q.toLowerCase();
                    const filtered = qLower ? allVals.filter(v => v.toLowerCase().includes(qLower)) : allVals;
                    renderList(filtered, currentChecked);
                }
            });
        }

        if (applyBtn) {
            applyBtn.addEventListener('click', function() {
                const checked = popupList ? [...popupList.querySelectorAll('input[type=checkbox]:checked')].map(c => c.value) : [];
                const p = new URLSearchParams(window.location.search);
                p.delete(`f_${activeField}`);
                const searchValue = searchInput ? searchInput.value.trim() : '';
                const allLoadedSelected = checked.length === allVals.length && allVals.length > 0;
                if (!(allLoadedSelected && filterHasMore === false && searchValue === '')) {
                    checked.forEach((v) => p.append(`f_${activeField}`, v));
                }
                p.delete('page');
                applySearchParams(p);
            });
        }

        if (resetBtn) {
            resetBtn.addEventListener('click', function() {
                const p = new URLSearchParams(window.location.search);
                p.delete(`f_${activeField}`);
                p.delete('page');
                window.location.search = p.toString();
            });
        }
    }

    function bindRangeEvents() {
        const { applyBtn, resetBtn, fromInput, toInput } = getElements();

        if (applyBtn) {
            applyBtn.addEventListener('click', function() {
                const p = new URLSearchParams(window.location.search);
                p.delete(`f_${activeField}_from`);
                p.delete(`f_${activeField}_to`);
                p.delete('page');
                const from = fromInput ? fromInput.value.trim() : '';
                const to = toInput ? toInput.value.trim() : '';
                if (from) p.set(`f_${activeField}_from`, from);
                if (to) p.set(`f_${activeField}_to`, to);
                window.location.search = p.toString();
            });
        }

        if (resetBtn) {
            resetBtn.addEventListener('click', function() {
                const p = new URLSearchParams(window.location.search);
                p.delete(`f_${activeField}_from`);
                p.delete(`f_${activeField}_to`);
                p.delete('page');
                window.location.search = p.toString();
            });
        }
    }

    function bindTextEvents() {
        const { applyBtn, resetBtn, textInput } = getElements();

        if (applyBtn) {
            applyBtn.addEventListener('click', function() {
                const p = new URLSearchParams(window.location.search);
                p.delete(`f_${activeField}`);
                p.delete('page');
                const value = textInput ? textInput.value.trim() : '';
                if (value) p.set(`f_${activeField}`, value);
                window.location.search = p.toString();
            });
        }

        if (resetBtn) {
            resetBtn.addEventListener('click', function() {
                const p = new URLSearchParams(window.location.search);
                p.delete(`f_${activeField}`);
                p.delete('page');
                window.location.search = p.toString();
            });
        }

        if (textInput) {
            textInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && applyBtn) {
                    e.preventDefault();
                    applyBtn.click();
                }
            });
        }
    }

    function positionPopup(rect) {
        const popupW = 240;
        let left = rect.left;
        if (left + popupW > window.innerWidth - 8) left = window.innerWidth - popupW - 8;
        if (left < 8) left = 8;
        popup.style.left = left + 'px';
        const spaceBelow = window.innerHeight - 16 - rect.bottom - 6;
        const spaceAbove = rect.top - 6 - 16;
        let top, maxH;
        if (spaceBelow >= spaceAbove) {
            top = rect.bottom + 6;
            maxH = spaceBelow;
        } else {
            maxH = spaceAbove;
            top = rect.top - 6 - maxH;
        }
        popup.style.top = top + 'px';
        popup.style.maxHeight = maxH + 'px';
    }

    function openPopup(btn) {
        activeField = btn.dataset.filterField;
        activeType = btn.dataset.filterType || 'value';
        filterUrl = btn.dataset.filterUrl || null;
        allVals = [];
        filterHasMore = false;
        const checked = btn.dataset.filterChecked ? btn.dataset.filterChecked.split('|||').filter(Boolean) : [];
        const rect = btn.getBoundingClientRect();

        if (activeType === 'date' || activeType === 'number') {
            popup.innerHTML = tplRange;
            const { fromInput, toInput } = getElements();
            if (activeType === 'date') {
                if (fromInput) fromInput.type = 'date';
                if (toInput) toInput.type = 'date';
            } else {
                if (fromInput) { fromInput.type = 'number'; fromInput.step = 'any'; fromInput.placeholder = '0'; }
                if (toInput) { toInput.type = 'number'; toInput.step = 'any'; toInput.placeholder = '∞'; }
            }
            const p = new URLSearchParams(window.location.search);
            if (fromInput) fromInput.value = p.get(`f_${activeField}_from`) || '';
            if (toInput) toInput.value = p.get(`f_${activeField}_to`) || '';
            bindRangeEvents();
            popup.classList.remove('hidden');
            positionPopup(rect);
            setTimeout(() => { if (fromInput) fromInput.focus(); }, 50);
        } else if (activeType === 'text') {
            popup.innerHTML = tplText;
            const { textInput } = getElements();
            const p = new URLSearchParams(window.location.search);
            if (textInput) textInput.value = p.get(`f_${activeField}`) || btn.dataset.filterText || '';
            bindTextEvents();
            popup.classList.remove('hidden');
            positionPopup(rect);
            setTimeout(() => { if (textInput) textInput.focus(); }, 50);
        } else {
            popup.innerHTML = tplValue;
            bindValueEvents();
            if (filterUrl) {
                const { popupList, searchInput } = getElements();
                if (popupList) popupList.innerHTML = '<p class="text-xs text-slate-400 px-1 py-2">Загрузка…</p>';
                popup.classList.remove('hidden');
                positionPopup(rect);
                fetchFilterValues(filterUrl, '').then(data => {
                    allVals = data.values;
                    filterHasMore = data.hasMore;
                    renderList(allVals, checked);
                    positionPopup(rect);
                    if (searchInput) searchInput.focus();
                });
            } else {
                renderList(allVals, checked);
                popup.classList.remove('hidden');
                positionPopup(rect);
                const { searchInput } = getElements();
                setTimeout(() => { if (searchInput) searchInput.focus(); }, 50);
            }
        }
    }

    function closePopup() {
        popup.classList.add('hidden');
        activeField = null;
    }

    document.querySelectorAll('.col-filter-btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (!popup.classList.contains('hidden') && activeField === this.dataset.filterField) {
                closePopup();
            } else {
                openPopup(this);
            }
        });
    });

    document.addEventListener('click', function(e) {
        if (!popup.contains(e.target)) closePopup();
    });

    popup.addEventListener('click', e => e.stopPropagation());

    // Row checkboxes + export selected
    const fullExportLink = document.getElementById('full-export-link');
    const partialExportToggle = document.getElementById('partial-export-toggle');
    const selectAllRows = document.getElementById('select-all-rows');
    const exportSelectedBtn = document.getElementById('export-selected-btn');
    const exportSelectedForm = document.getElementById('export-selected-form');
    const exportSelectedLabel = document.getElementById('export-selected-label');
    const partialSelectionCells = document.querySelectorAll('[data-partial-selection]');
    const selectionEntity = partialExportToggle ? partialExportToggle.dataset.selectionEntity : null;
    const selectedStorageKey = selectionEntity ? `coalShipments:selected:${selectionEntity}` : null;
    const modeStorageKey = selectionEntity ? `coalShipments:partialMode:${selectionEntity}` : null;

    function loadSelection() {
        if (!selectedStorageKey) return new Set();
        try {
            const raw = window.localStorage.getItem(selectedStorageKey);
            const ids = raw ? JSON.parse(raw) : [];
            return new Set(Array.isArray(ids) ? ids.map(String) : []);
        } catch (e) {
            return new Set();
        }
    }

    function saveSelection(selectedIds) {
        if (!selectedStorageKey) return;
        try {
            window.localStorage.setItem(selectedStorageKey, JSON.stringify([...selectedIds]));
        } catch (e) {}
    }

    function clearSelection() {
        if (!selectedStorageKey) return;
        try {
            window.localStorage.removeItem(selectedStorageKey);
        } catch (e) {}
    }

    function isPartialMode() {
        if (!modeStorageKey) return false;
        try {
            return window.localStorage.getItem(modeStorageKey) === '1';
        } catch (e) {
            return false;
        }
    }

    function setPartialMode(enabled) {
        if (!modeStorageKey) return;
        try {
            if (enabled) {
                window.localStorage.setItem(modeStorageKey, '1');
            } else {
                window.localStorage.removeItem(modeStorageKey);
            }
        } catch (e) {}
    }

    function applySelectionToPage(selectedIds) {
        document.querySelectorAll('.row-checkbox').forEach(cb => {
            cb.checked = selectedIds.has(String(cb.value));
        });
    }

    function updateSelectAllState(selectedIds) {
        if (!selectAllRows) return;
        const pageCheckboxes = [...document.querySelectorAll('.row-checkbox')];
        const selectedOnPage = pageCheckboxes.filter(cb => selectedIds.has(String(cb.value)));
        selectAllRows.checked = pageCheckboxes.length > 0 && selectedOnPage.length === pageCheckboxes.length;
        selectAllRows.indeterminate = selectedOnPage.length > 0 && selectedOnPage.length < pageCheckboxes.length;
    }

    function renderPartialMode() {
        const enabled = isPartialMode();
        const selectedIds = loadSelection();
        partialSelectionCells.forEach(el => el.classList.toggle('hidden', !enabled));
        if (fullExportLink) fullExportLink.classList.toggle('hidden', enabled);
        if (exportSelectedBtn) {
            exportSelectedBtn.classList.toggle('hidden', !enabled);
            exportSelectedBtn.classList.toggle('flex', enabled);
        }
        if (partialExportToggle) {
            partialExportToggle.classList.toggle('bg-white', !enabled);
            partialExportToggle.classList.toggle('dark:bg-slate-800', !enabled);
            partialExportToggle.classList.toggle('text-slate-700', !enabled);
            partialExportToggle.classList.toggle('dark:text-slate-300', !enabled);
            partialExportToggle.classList.toggle('border-slate-200', !enabled);
            partialExportToggle.classList.toggle('dark:border-slate-600', !enabled);
            partialExportToggle.classList.toggle('hover:bg-slate-50', !enabled);
            partialExportToggle.classList.toggle('dark:hover:bg-slate-700', !enabled);
            partialExportToggle.classList.toggle('bg-brand-600', enabled);
            partialExportToggle.classList.toggle('text-white', enabled);
            partialExportToggle.classList.toggle('border-brand-600', enabled);
            partialExportToggle.classList.toggle('hover:bg-brand-700', enabled);
            partialExportToggle.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        }
        applySelectionToPage(selectedIds);
        updateSelectAllState(selectedIds);
        updateExportBtn(selectedIds);
    }

    function updateExportBtn(selectedIds) {
        const ids = selectedIds || loadSelection();
        if (exportSelectedBtn) {
            exportSelectedBtn.disabled = ids.size === 0;
            if (exportSelectedLabel) exportSelectedLabel.textContent = ids.size > 0 ? `Выбранные (${ids.size})` : 'Выбранные';
        }
    }

    if (partialExportToggle) {
        partialExportToggle.addEventListener('click', function() {
            const enabled = isPartialMode();
            setPartialMode(!enabled);
            if (enabled) clearSelection();
            renderPartialMode();
        });
    }

    if (selectAllRows) {
        selectAllRows.addEventListener('change', function() {
            const selectedIds = loadSelection();
            document.querySelectorAll('.row-checkbox').forEach(cb => {
                const id = String(cb.value);
                cb.checked = this.checked;
                if (this.checked) {
                    selectedIds.add(id);
                } else {
                    selectedIds.delete(id);
                }
            });
            saveSelection(selectedIds);
            updateSelectAllState(selectedIds);
            updateExportBtn(selectedIds);
        });
    }

    document.querySelectorAll('.row-checkbox').forEach(cb => {
        cb.addEventListener('change', function() {
            const selectedIds = loadSelection();
            const id = String(this.value);
            if (this.checked) {
                selectedIds.add(id);
            } else {
                selectedIds.delete(id);
            }
            saveSelection(selectedIds);
            updateSelectAllState(selectedIds);
            updateExportBtn(selectedIds);
        });
    });

    if (exportSelectedBtn && exportSelectedForm) {
        exportSelectedBtn.addEventListener('click', function() {
            const checked = [...loadSelection()];
            exportSelectedForm.querySelectorAll('input[name="ids"]').forEach(el => el.remove());
            checked.forEach(id => {
                const inp = document.createElement('input');
                inp.type = 'hidden'; inp.name = 'ids'; inp.value = id;
                exportSelectedForm.appendChild(inp);
            });
            clearSelection();
            setPartialMode(false);
            exportSelectedForm.submit();
        });
    }

    renderPartialMode();

    document.querySelectorAll('[data-remove-field]').forEach(el => {
        el.addEventListener('click', function() {
            const field = this.dataset.removeField;
            const val = this.dataset.removeValue;
            const p = new URLSearchParams(window.location.search);
            if (field.endsWith('_from') || field.endsWith('_to')) {
                p.delete(field);
            } else if (val === undefined || val === null) {
                p.delete(field);
            } else {
                const existing = p.getAll(field).filter(v => v !== val);
                p.delete(field);
                existing.forEach(v => p.append(field, v));
            }
            p.delete('page');
            window.location.search = p.toString();
        });
    });
})();
