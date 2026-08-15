/**
 * Spreadsheet-style Keyboard Navigation and AJAX save for Grade Grid
 */

document.addEventListener('DOMContentLoaded', () => {
    const grid = document.getElementById('grade-grid');
    if (!grid) return;

    // Listen to changes on inputs
    grid.addEventListener('keydown', (e) => {
        const input = e.target;
        if (!input.classList.contains('grade-input')) return;

        const cell = input.parentElement;
        const row = cell.parentElement;
        const colIndex = Array.from(row.children).indexOf(cell);
        
        let targetInput = null;

        if (e.key === 'Enter') {
            e.preventDefault();
            // Move to same column in the next row
            const nextRow = row.nextElementSibling;
            if (nextRow) {
                targetInput = nextRow.children[colIndex].querySelector('.grade-input');
            }
        } else if (e.key === 'Tab') {
            // Let normal Tab navigation work or handle shift-tab
        } else if (e.key === 'ArrowDown') {
            const nextRow = row.nextElementSibling;
            if (nextRow) {
                targetInput = nextRow.children[colIndex].querySelector('.grade-input');
            }
        } else if (e.key === 'ArrowUp') {
            const prevRow = row.previousElementSibling;
            if (prevRow) {
                targetInput = prevRow.children[colIndex].querySelector('.grade-input');
            }
        } else if (e.key === 'ArrowRight' && input.selectionEnd === input.value.length) {
            const nextCell = cell.nextElementSibling;
            if (nextCell) {
                targetInput = nextCell.querySelector('.grade-input');
            }
        } else if (e.key === 'ArrowLeft' && input.selectionStart === 0) {
            const prevCell = cell.previousElementSibling;
            if (prevCell) {
                targetInput = prevCell.querySelector('.grade-input');
            }
        }

        if (targetInput) {
            targetInput.focus();
            targetInput.select();
        }
    });

    // Handle AJAX Save on blur or value change
    grid.addEventListener('change', (e) => {
        const input = e.target;
        if (!input.classList.contains('grade-input')) return;
        saveCell(input);
    });

    // Custom status toggles (missed / graded) via dropdown or context menu
    // We add an helper menu when clicking grade status buttons or via dropdown
});

function saveCell(input) {
    const cell = input.parentElement;
    const enrollmentId = cell.dataset.enrollmentId;
    const componentId = cell.dataset.componentId;
    const value = input.value.trim();
    const classId = window.location.pathname.split('/')[2];
    
    let status = 'graded';
    let numericValue = value === '' ? null : parseFloat(value.replace(',', '.'));

    if (value.toUpperCase() === 'FJ') {
        status = 'missed_justified';
        numericValue = null;
    } else if (value.toUpperCase() === 'FI') {
        status = 'missed_unjustified';
        numericValue = null;
    }

    // CSS Feedback
    input.style.opacity = '0.5';

    fetch(`/class/${classId}/grades/cell`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
            enrollment_id: enrollmentId,
            component_id: componentId,
            value: numericValue,
            status: status
        })
    })
    .then(res => res.json())
    .then(data => {
        input.style.opacity = '1';
        if (data.ok) {
            // Update color coding classes
            cell.classList.remove('grade-cell-missed-justified', 'grade-cell-missed-unjustified');
            if (status === 'missed_justified') {
                cell.classList.add('grade-cell-missed-justified');
                input.value = 'FJ';
            } else if (status === 'missed_unjustified') {
                cell.classList.add('grade-cell-missed-unjustified');
                input.value = 'FI';
            } else {
                input.value = numericValue !== null ? numericValue.toFixed(1) : '';
            }

            // Update row average element if it exists
            const avgCell = document.getElementById(`avg-${enrollmentId}`);
            if (avgCell && data.avg !== undefined) {
                avgCell.textContent = data.avg !== null ? data.avg.toFixed(1) : '—';
            }

            // Update component miss counters in footer
            const footerJustified = document.getElementById(`miss-justified-${componentId}`);
            const footerUnjustified = document.getElementById(`miss-unjustified-${componentId}`);
            if (footerJustified && footerUnjustified && data.component_misses) {
                footerJustified.textContent = data.component_misses.justified;
                footerUnjustified.textContent = data.component_misses.unjustified;
            }

            // Refresh undo panel list
            if (window.refreshUndoPanel) {
                window.refreshUndoPanel();
            }

            // Mark session as dirty for auto-backup
            window.isGradeDirty = true;
        } else {
            alert('Erro ao salvar nota: ' + (data.error || 'Erro desconhecido'));
        }
    })
    .catch(err => {
        input.style.opacity = '1';
        console.error(err);
    });
}

function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// Auto Backup Trigger when leaving page
window.isGradeDirty = false;
window.addEventListener('beforeunload', (e) => {
    if (window.isGradeDirty) {
        const classId = window.location.pathname.split('/')[2];
        const token = getCsrfToken();
        // Use keepalive / sendBeacon to trigger backup on leave
        const url = `/backup/trigger/${classId}`;
        const headers = { 'X-CSRFToken': token };
        navigator.sendBeacon(url);
    }
});
