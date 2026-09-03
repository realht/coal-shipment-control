(function () {
    'use strict';

    function openDeleteModal(btn) {
        document.getElementById('delete-pk').value = btn.dataset.pk;
        document.getElementById('delete-stype').value = btn.dataset.stype;
        document.getElementById('delete-label').textContent = btn.dataset.label;
        document.getElementById('delete-modal').classList.remove('hidden');
    }

    function closeDeleteModal() {
        document.getElementById('delete-modal').classList.add('hidden');
    }

    document.addEventListener('DOMContentLoaded', function () {
        var modal = document.getElementById('delete-modal');
        if (!modal) return;

        modal.addEventListener('click', function (e) {
            if (e.target === modal) closeDeleteModal();
        });

        document.querySelectorAll('[data-delete-trigger]').forEach(function (btn) {
            btn.addEventListener('click', function () { openDeleteModal(this); });
        });

        var closeBtn = document.getElementById('delete-modal-close');
        if (closeBtn) closeBtn.addEventListener('click', closeDeleteModal);

        document.querySelectorAll('input[type=radio][name=type]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                document.querySelectorAll('input[type=radio][name=type]').forEach(function (r) {
                    var label = r.closest('label');
                    if (!label) return;
                    if (r.checked) {
                        label.classList.add('bg-brand-600', 'text-white');
                        label.classList.remove('bg-white', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
                    } else {
                        label.classList.remove('bg-brand-600', 'text-white');
                        label.classList.add('bg-white', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
                    }
                });
            });
        });
    });
})();
