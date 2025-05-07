document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("query-form");
  if (!form) return;

  const textarea = document.getElementById("query");
  const loading = document.getElementById("loading");
  const userQuery = document.getElementById("user-query");
  const responseStream = document.getElementById("response-stream");
  const currentQueryContainer = document.getElementById("current-query-container");
  const currentResponseContainer = document.getElementById("current-response-container");
  const submitButton = form.querySelector("button[type='submit']");
  const clearHistoryForm = document.getElementById("clear-stream-history-form");

  // Add a confirmation for clearing history
  if (clearHistoryForm) {
      clearHistoryForm.addEventListener("submit", function(e) {
          if (!confirm("Are you sure you want to clear all chat history?")) {
              e.preventDefault();
          }
      });
  }

  if (!textarea || !loading || !userQuery || !responseStream ||
      !currentQueryContainer || !currentResponseContainer || !submitButton) {
    console.error("Required streaming mode elements not found");
    return;
  }

  let isSubmitting = false;

  function resizeTextarea() {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }

  resizeTextarea();
  textarea.addEventListener("input", resizeTextarea);

  let cursorInterval;

  function stopCursor() {
    if (cursorInterval) {
      clearInterval(cursorInterval);
      cursorInterval = null;
      if (responseStream.textContent.endsWith("▍")) {
        responseStream.textContent = responseStream.textContent.slice(0, -1);
      }
    }
  }

  function startCursor() {
    stopCursor();
    cursorInterval = setInterval(() => {
      if (responseStream.textContent.endsWith("▍")) {
        responseStream.textContent = responseStream.textContent.slice(0, -1);
      } else {
        responseStream.textContent += "▍";
      }
      window.scrollTo(0, document.body.scrollHeight);
    }, 500);
  }

  async function submitQuery() {
    const query = textarea.value.trim();
    if (!query || isSubmitting) return;

    try {
      isSubmitting = true;

      loading.style.display = "flex";
      textarea.disabled = true;
      submitButton.disabled = true;

      userQuery.textContent = query;
      responseStream.textContent = "";
      currentQueryContainer.style.display = "block";
      currentResponseContainer.style.display = "block";

      const formData = new FormData();
      formData.append("query", query);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/streaming", true);

      let responseText = "";
      let lastProcessedLength = 0;

      xhr.onprogress = function () {
        if (xhr.status === 200) {
          const newChunk = xhr.responseText.substring(lastProcessedLength);
          lastProcessedLength = xhr.responseText.length;
          if (newChunk) {
            stopCursor();
            responseStream.textContent += newChunk;
            startCursor();
            responseText += newChunk;
            window.scrollTo(0, document.body.scrollHeight);
          }
        }
      };

      xhr.onload = function () {
        if (xhr.status === 200) {
          const finalChunk = xhr.responseText.substring(lastProcessedLength);
          if (finalChunk) {
            stopCursor();
            responseStream.textContent += finalChunk;
          }
        } else {
          console.error("Request failed with status:", xhr.status);
          responseStream.textContent = `Error ${xhr.status}: Request failed`;
        }
        finishSubmission();
      };

      xhr.onerror = function () {
        console.error("Network error occurred");
        responseStream.textContent = "Network error occurred. Please try again.";
        finishSubmission();
      };

      xhr.ontimeout = function () {
        console.error("Request timed out");
        responseStream.textContent = "Request timed out. Please try again.";
        finishSubmission();
      };

      xhr.send(formData);
      startCursor();

    } catch (error) {
      console.error("Error submitting query:", error);
      responseStream.textContent = `Error: ${error.message}`;
      finishSubmission();
    }
  }

  function finishSubmission() {
    stopCursor();
    loading.style.display = "none";
    textarea.disabled = false;
    submitButton.disabled = false;
    textarea.value = "";
    resizeTextarea();
    textarea.focus();
    isSubmitting = false;
    window.scrollTo(0, document.body.scrollHeight);
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    submitQuery();
  });

  textarea.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitQuery();
    } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submitQuery();
    }
  });
});
