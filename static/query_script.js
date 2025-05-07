document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("query-form");
  const textarea = document.getElementById("query");
  const loading = document.getElementById("loading");
  const submitButton = form?.querySelector("button[type='submit']");
  const clearHistoryForm = document.getElementById("clear-history-form");

  // Add a confirmation for clearing history
  if (clearHistoryForm) {
    clearHistoryForm.addEventListener("submit", function (e) {
      if (!confirm("Are you sure you want to clear all chat history?")) {
        e.preventDefault();
      }
    });
  }

  if (!form || !textarea || !loading || !submitButton) {
    console.warn("Essential elements for non-streaming form not found");
    return;
  }

  // Auto-resize textarea
  function resizeTextarea() {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }

  resizeTextarea();
  textarea.addEventListener("input", resizeTextarea);

  let isSubmitting = false;

  function handlePreSubmit() {
    const query = textarea.value.trim();
    if (!query || isSubmitting) return false;

    isSubmitting = true;
    submitButton.disabled = true;       // ✅ Disable only button
    loading.style.display = "flex";
    return true;
  }

  // Handle Enter key submission
  textarea.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (handlePreSubmit()) {
        form.requestSubmit(); // Will POST and reload
      }
    }
  });

  // Handle manual submit click
  form.addEventListener("submit", function () {
    if (!isSubmitting) {
      handlePreSubmit();
    }
  });
});
