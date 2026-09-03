(function () {
    'use strict';

    var container = document.getElementById('sortable-rows');
    var orderInput = document.getElementById('field-order-input');
    var form = document.getElementById('field-settings-form');

    if (!container || !orderInput || !form) return;

    var dragSrc = null;

    function updateOrder() {
        var rows = container.querySelectorAll('[data-field]');
        orderInput.value = Array.from(rows).map(function (r) { return r.dataset.field; }).join(',');
    }

    function handleDragStart(e) {
        dragSrc = this;
        this.style.opacity = '0.4';
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', this.dataset.field);
    }

    function handleDragEnd() {
        this.style.opacity = '';
        container.querySelectorAll('[data-field]').forEach(function (r) {
            r.classList.remove('bg-brand-50', 'dark:bg-brand-900/20');
        });
    }

    function handleDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        return false;
    }

    function handleDragEnter() {
        if (this !== dragSrc) {
            this.classList.add('bg-brand-50', 'dark:bg-brand-900/20');
        }
    }

    function handleDragLeave() {
        this.classList.remove('bg-brand-50', 'dark:bg-brand-900/20');
    }

    function handleDrop(e) {
        e.stopPropagation();
        e.preventDefault();
        if (dragSrc !== this) {
            var rows = Array.from(container.querySelectorAll('[data-field]'));
            var srcIdx = rows.indexOf(dragSrc);
            var tgtIdx = rows.indexOf(this);
            if (srcIdx < tgtIdx) {
                container.insertBefore(dragSrc, this.nextSibling);
            } else {
                container.insertBefore(dragSrc, this);
            }
            updateOrder();
        }
        this.classList.remove('bg-brand-50', 'dark:bg-brand-900/20');
        return false;
    }

    container.querySelectorAll('[data-field]').forEach(function (row) {
        row.addEventListener('dragstart', handleDragStart);
        row.addEventListener('dragend', handleDragEnd);
        row.addEventListener('dragover', handleDragOver);
        row.addEventListener('dragenter', handleDragEnter);
        row.addEventListener('dragleave', handleDragLeave);
        row.addEventListener('drop', handleDrop);
    });

    updateOrder();

    form.addEventListener('submit', updateOrder);
})();
