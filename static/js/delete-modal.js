// Shared delete confirmation modal (deletes are admin only).
// Any button with data-delete-action opens the modal and, on submit, posts
// to that action URL.
(function () {
    const modal = document.getElementById("delete-modal");
    if (!modal) return;

    const form = document.getElementById("delete-form");
    const text = document.getElementById("delete-modal-text");
    const cancelBtn = document.getElementById("delete-cancel");

    function open(action, label) {
        form.action = action;
        text.textContent = "Are you sure you want to delete the " + label + "?";
        modal.hidden = false;
    }

    function close() {
        modal.hidden = true;
    }

    document.querySelectorAll("[data-delete-action]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            open(btn.dataset.deleteAction, btn.dataset.deleteLabel || "item");
        });
    });

    cancelBtn.addEventListener("click", close);
    modal.addEventListener("click", function (e) {
        if (e.target === modal) close();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.hidden) close();
    });
})();
