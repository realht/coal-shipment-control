(function () {
    'use strict';

    function readPreviewData() {
        var script = document.getElementById('import-preview-data');
        if (!script) {
            return { valid: [], duplicates: [], errors: [] };
        }
        try {
            var parsed = JSON.parse(script.textContent || '{}');
            return {
                valid: Array.isArray(parsed.valid) ? parsed.valid : [],
                duplicates: Array.isArray(parsed.duplicates) ? parsed.duplicates : [],
                errors: Array.isArray(parsed.errors) ? parsed.errors : []
            };
        } catch (err) {
            return { valid: [], duplicates: [], errors: [] };
        }
    }

    function value(row, field) {
        var data = row && row.data ? row.data : {};
        var raw = data[field];
        return raw === null || raw === undefined ? '' : String(raw);
    }

    function appendCell(tr, className, text) {
        var td = document.createElement('td');
        td.className = className;
        td.textContent = text === null || text === undefined ? '' : String(text);
        tr.appendChild(td);
        return td;
    }

    function clearNode(node) {
        while (node && node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function pageSlice(rows, page, pageSize) {
        var start = (page - 1) * pageSize;
        return rows.slice(start, start + pageSize);
    }

    function pageCount(rows, pageSize) {
        return Math.max(1, Math.ceil(rows.length / pageSize));
    }

    function setPager(section, page, rows, pageSize) {
        var pages = pageCount(rows, pageSize);
        var label = document.querySelector('[data-import-page-label="' + section + '"]');
        var prev = document.querySelector('[data-import-prev="' + section + '"]');
        var next = document.querySelector('[data-import-next="' + section + '"]');
        if (label) {
            label.textContent = rows.length ? ('Страница ' + page + ' из ' + pages) : 'Нет строк';
        }
        if (prev) {
            prev.disabled = page <= 1;
            prev.classList.toggle('opacity-50', prev.disabled);
        }
        if (next) {
            next.disabled = page >= pages;
            next.classList.toggle('opacity-50', next.disabled);
        }
    }

    function updateSelectAll(selectAll, selectedRows, validRows) {
        if (!selectAll) {
            return;
        }
        selectAll.checked = validRows.length > 0 && selectedRows.size === validRows.length;
        selectAll.indeterminate = selectedRows.size > 0 && selectedRows.size < validRows.length;
    }

    function renderValidRows(rows, state) {
        var tbody = document.getElementById('import-valid-body');
        if (!tbody) {
            return;
        }
        clearNode(tbody);
        pageSlice(rows, state.pages.valid, state.pageSize).forEach(function (row) {
            var tr = document.createElement('tr');
            tr.className = 'hover:bg-slate-50 dark:hover:bg-slate-700/30';
            appendCell(tr, 'px-3 py-2 text-slate-400', row.row_num);

            var checkCell = document.createElement('td');
            checkCell.className = 'px-3 py-2';
            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'row-check rounded';
            checkbox.value = String(row.row_num);
            checkbox.checked = state.selectedRows.has(checkbox.value);
            checkbox.addEventListener('change', function () {
                if (checkbox.checked) {
                    state.selectedRows.add(checkbox.value);
                } else {
                    state.selectedRows.delete(checkbox.value);
                }
                updateSelectAll(state.selectAll, state.selectedRows, state.data.valid);
            });
            checkCell.appendChild(checkbox);
            tr.appendChild(checkCell);

            if (state.shipmentType === 'auto') {
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300 whitespace-nowrap', value(row, 'shipment_date'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300 max-w-xs truncate', value(row, 'customer_object'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'coal_grade'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'quantity'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'base_code'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'ttn_number'));
            } else {
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300 whitespace-nowrap', value(row, 'departure_date'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'wagon_number'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'cargo'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300 max-w-xs truncate', value(row, 'receiver'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300', value(row, 'volume'));
                appendCell(tr, 'px-3 py-2 text-slate-700 dark:text-slate-300 max-w-xs truncate', value(row, 'destination_station'));
            }
            tbody.appendChild(tr);
        });
        setPager('valid', state.pages.valid, rows, state.pageSize);
    }

    function detailUrl(state, id) {
        var template = state.shipmentType === 'auto' ? state.autoDetailUrlTemplate : state.railDetailUrlTemplate;
        return template.replace('__id__', encodeURIComponent(id));
    }

    function renderDuplicateRows(rows, state) {
        var tbody = document.getElementById('import-duplicate-body');
        if (!tbody) {
            return;
        }
        clearNode(tbody);
        pageSlice(rows, state.pages.duplicates, state.pageSize).forEach(function (row) {
            var tr = document.createElement('tr');
            tr.className = 'bg-amber-50/50 dark:bg-amber-900/10';
            appendCell(tr, 'px-3 py-2 text-amber-600 dark:text-amber-400 font-medium', row.row_num);
            if (state.shipmentType === 'auto') {
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400', value(row, 'shipment_date'));
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400 max-w-xs truncate', value(row, 'customer_object'));
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400', value(row, 'coal_grade'));
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400', value(row, 'quantity'));
            } else {
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400', value(row, 'departure_date'));
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400', value(row, 'wagon_number'));
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400 max-w-xs truncate', value(row, 'receiver'));
                appendCell(tr, 'px-3 py-2 text-slate-600 dark:text-slate-400', value(row, 'volume'));
            }

            var linksCell = document.createElement('td');
            linksCell.className = 'px-3 py-2 text-xs';
            (row.duplicate_ids || []).forEach(function (dupId, index) {
                var link = document.createElement('a');
                link.href = detailUrl(state, dupId);
                link.className = 'text-brand-600 dark:text-brand-400 hover:underline';
                link.textContent = '#' + dupId;
                linksCell.appendChild(link);
                if (index < row.duplicate_ids.length - 1) {
                    linksCell.appendChild(document.createTextNode(' '));
                }
            });
            tr.appendChild(linksCell);
            tbody.appendChild(tr);
        });
        setPager('duplicates', state.pages.duplicates, rows, state.pageSize);
    }

    function renderErrorRows(rows, state) {
        var list = document.getElementById('import-error-list');
        if (!list) {
            return;
        }
        clearNode(list);
        pageSlice(rows, state.pages.errors, state.pageSize).forEach(function (row) {
            var wrapper = document.createElement('div');
            wrapper.className = 'px-5 py-3';
            var title = document.createElement('div');
            title.className = 'text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1';
            title.textContent = 'Строка ' + row.row_num;
            wrapper.appendChild(title);

            var ul = document.createElement('ul');
            ul.className = 'space-y-0.5';
            (row.errors || []).forEach(function (err) {
                var li = document.createElement('li');
                li.className = 'text-xs text-red-600 dark:text-red-400';
                li.textContent = err;
                ul.appendChild(li);
            });
            wrapper.appendChild(ul);
            list.appendChild(wrapper);
        });
        setPager('errors', state.pages.errors, rows, state.pageSize);
    }

    function renderAll(state) {
        renderValidRows(state.data.valid, state);
        renderDuplicateRows(state.data.duplicates, state);
        renderErrorRows(state.data.errors, state);
        updateSelectAll(state.selectAll, state.selectedRows, state.data.valid);
    }

    function bindPager(state, section, rows) {
        var prev = document.querySelector('[data-import-prev="' + section + '"]');
        var next = document.querySelector('[data-import-next="' + section + '"]');
        if (prev) {
            prev.addEventListener('click', function () {
                state.pages[section] = Math.max(1, state.pages[section] - 1);
                renderAll(state);
            });
        }
        if (next) {
            next.addEventListener('click', function () {
                state.pages[section] = Math.min(pageCount(rows, state.pageSize), state.pages[section] + 1);
                renderAll(state);
            });
        }
    }

    function addSelectedInputs(state) {
        var holder = document.getElementById('import-selected-inputs');
        if (!holder) {
            return;
        }
        clearNode(holder);
        Array.from(state.selectedRows).sort(function (a, b) {
            return Number(a) - Number(b);
        }).forEach(function (rowNum) {
            var hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'import_ids';
            hidden.value = rowNum;
            holder.appendChild(hidden);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        var root = document.querySelector('[data-import-preview]');
        if (!root) {
            return;
        }

        var data = readPreviewData();
        var selectedRows = new Set(data.valid.map(function (row) {
            return String(row.row_num);
        }));
        var state = {
            data: data,
            selectedRows: selectedRows,
            selectAll: document.getElementById('select-all'),
            shipmentType: root.getAttribute('data-shipment-type') || 'auto',
            pageSize: parseInt(root.getAttribute('data-page-size'), 10) || 200,
            autoDetailUrlTemplate: root.getAttribute('data-auto-detail-url-template') || '',
            railDetailUrlTemplate: root.getAttribute('data-rail-detail-url-template') || '',
            pages: {
                valid: 1,
                duplicates: 1,
                errors: 1
            }
        };

        if (state.selectAll) {
            state.selectAll.addEventListener('change', function () {
                if (state.selectAll.checked) {
                    state.data.valid.forEach(function (row) {
                        state.selectedRows.add(String(row.row_num));
                    });
                } else {
                    state.selectedRows.clear();
                }
                renderAll(state);
            });
        }

        bindPager(state, 'valid', state.data.valid);
        bindPager(state, 'duplicates', state.data.duplicates);
        bindPager(state, 'errors', state.data.errors);
        renderAll(state);

        var form = document.getElementById('import-form');
        if (form) {
            form.addEventListener('submit', function (e) {
                if (state.selectedRows.size === 0) {
                    e.preventDefault();
                    var msg = document.getElementById('import-empty-warning');
                    if (msg) {
                        msg.classList.remove('hidden');
                    }
                    return;
                }
                addSelectedInputs(state);
            });
        }
    });
})();
