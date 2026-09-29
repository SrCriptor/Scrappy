/* ── Modal de Confirmação Padronizado (estilo "Limpar Todos os Logs") ──────
   Substitui window.confirm() nativo em todo o painel por um modal Bootstrap
   consistente com o tema escuro do admin. Uso:

     showConfirmModal({
       message: 'Apagar TODO o log? Esta ação é irreversível.',
       onConfirm: () => { ...ação destrutiva... }
     });

   onConfirm só é chamado se o usuário clicar em "Confirmar e Apagar Tudo".
*/
(function () {
    function ensureModal() {
        let modal = document.getElementById('globalConfirmModal');
        if (modal) return modal;

        const wrap = document.createElement('div');
        wrap.innerHTML = `
        <div class="modal fade" id="globalConfirmModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content" style="background:#161b22;border-color:#30363d;">
                    <div class="modal-header border-bottom" style="border-color:#21262d !important;">
                        <h5 class="modal-title text-danger" id="globalConfirmTitle">
                            <i class="fas fa-trash-can text-danger me-2"></i>Confirmar Ação
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="alert alert-danger py-2" style="font-size:.82rem;" id="globalConfirmMessage">
                            <i class="fas fa-exclamation-triangle me-1"></i>
                            Esta ação é irreversível.
                        </div>
                    </div>
                    <div class="modal-footer border-top" style="border-color:#21262d !important;">
                        <button class="btn btn-secondary btn-sm" data-bs-dismiss="modal" id="globalConfirmCancelBtn">Cancelar</button>
                        <button class="btn btn-danger btn-sm" id="globalConfirmActionBtn">
                            <i class="fas fa-trash me-1"></i><span id="globalConfirmActionLabel">Confirmar e Apagar Tudo</span>
                        </button>
                    </div>
                </div>
            </div>
        </div>`;
        document.body.appendChild(wrap.firstElementChild);
        modal = document.getElementById('globalConfirmModal');
        return modal;
    }

    /**
     * @param {Object} opts
     * @param {string} [opts.title]        Título do cabeçalho (padrão: "Confirmar Ação")
     * @param {string} opts.message        Texto de aviso exibido no box vermelho
     * @param {string} [opts.confirmText]   Rótulo do botão principal (padrão: "Confirmar e Apagar Tudo")
     * @param {Function} opts.onConfirm     Callback disparado somente ao confirmar
     */
    window.showConfirmModal = function (opts) {
        const modalEl = ensureModal();
        const titleEl   = document.getElementById('globalConfirmTitle');
        const msgEl     = document.getElementById('globalConfirmMessage');
        const actionBtn = document.getElementById('globalConfirmActionBtn');
        const labelEl   = document.getElementById('globalConfirmActionLabel');

        titleEl.innerHTML = `<i class="fas fa-trash-can text-danger me-2"></i>${opts.title || 'Confirmar Ação'}`;
        msgEl.innerHTML = `<i class="fas fa-exclamation-triangle me-1"></i>${opts.message || 'Esta ação é irreversível.'}`;
        labelEl.textContent = opts.confirmText || 'Confirmar e Apagar Tudo';

        // Replace the button node to drop any previously-bound listener
        const freshBtn = actionBtn.cloneNode(true);
        actionBtn.parentNode.replaceChild(freshBtn, actionBtn);
        freshBtn.addEventListener('click', () => {
            const inst = bootstrap.Modal.getInstance(modalEl);
            if (inst) inst.hide();
            if (typeof opts.onConfirm === 'function') opts.onConfirm();
        });

        const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
        modalInstance.show();
    };

    /**
     * Helper for <form onsubmit="return confirmFormSubmit(this, 'message')">.
     * Always returns false to block the native submit; submits the form
     * programmatically only if the user confirms in the modal.
     */
    window.confirmFormSubmit = function (form, message, opts) {
        opts = opts || {};
        showConfirmModal({
            title: opts.title,
            message: message,
            confirmText: opts.confirmText,
            onConfirm: () => form.submit()
        });
        return false;
    };
})();
