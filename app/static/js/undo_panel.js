/**
 * Persistent Undo / Action Panel logic
 */

document.addEventListener('DOMContentLoaded', () => {
    // Initialise Alpine or vanilla listeners
    window.refreshUndoPanel = refreshUndoPanel;
    refreshUndoPanel();
});

function refreshUndoPanel() {
    const listContainer = document.getElementById('undo-actions-list');
    if (!listContainer) return;

    const classId = window.location.pathname.split('/')[2];
    if (!classId || isNaN(classId)) return;

    fetch(`/class/${classId}/grades/recent-actions`)
        .then(res => res.json())
        .then(actions => {
            listContainer.innerHTML = '';
            if (actions.length === 0) {
                listContainer.innerHTML = '<div class="p-2 text-muted text-center" style="font-size: 0.8rem;">Nenhuma ação recente.</div>';
                return;
            }

            actions.forEach(act => {
                const item = document.createElement('div');
                item.className = `undo-action-item ${act.reverted ? 'reverted' : ''}`;
                
                const desc = document.createElement('div');
                desc.className = 'undo-action-desc';
                desc.innerHTML = `<strong>[${act.timestamp}]</strong> ${act.description}`;
                item.appendChild(desc);

                if (!act.reverted) {
                    const undoBtn = document.createElement('button');
                    undoBtn.className = 'btn btn-secondary btn-undo';
                    undoBtn.textContent = 'Desfazer';
                    undoBtn.onclick = () => performUndo(classId, act.id, undoBtn);
                    item.appendChild(undoBtn);
                }

                listContainer.appendChild(item);
            });
        })
        .catch(err => console.error('Error fetching recent actions:', err));
}

function performUndo(classId, actionId, buttonEl) {
    buttonEl.disabled = true;
    buttonEl.textContent = 'Desfazendo...';

    const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

    fetch(`/class/${classId}/undo/${actionId}`, {
        method: 'POST',
        headers: {
            'X-CSRFToken': token
        }
    })
    .then(res => res.json())
    .then(data => {
        if (data.ok) {
            refreshUndoPanel();
            // Optional: trigger grid reload to see reverted grades instantly
            const grid = document.getElementById('grade-grid');
            if (grid) {
                // If on grade grid, simple reload keeps state consistent
                window.location.reload();
            }
        } else {
            alert('Falha ao desfazer ação: ' + (data.error || 'Erro desconhecido'));
            buttonEl.disabled = false;
            buttonEl.textContent = 'Desfazer';
        }
    })
    .catch(err => {
        console.error(err);
        buttonEl.disabled = false;
        buttonEl.textContent = 'Desfazer';
    });
}
