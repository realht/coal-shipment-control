(function () {
    'use strict';

    function initHybrid(selectAttr, otherAttr) {
        document.querySelectorAll('[' + selectAttr + ']').forEach(function (sel) {
            var key = sel.getAttribute(selectAttr);
            var other = document.querySelector('[' + otherAttr + '="' + key + '"]');
            if (!other) return;
            function toggle() {
                other.style.display = sel.value === '__other__' ? '' : 'none';
            }
            toggle();
            sel.addEventListener('change', toggle);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initHybrid('data-hybrid-select', 'data-hybrid-other');
        });
    } else {
        initHybrid('data-hybrid-select', 'data-hybrid-other');
    }
})();
