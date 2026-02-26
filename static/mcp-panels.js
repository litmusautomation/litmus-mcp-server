// Inject side panels into the page
(function () {
    const chevron = `<svg class="mcp-panel-chevron" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`;

    document.body.insertAdjacentHTML("afterbegin", `
        <div class="side-panels" id="side-panels">
            <div class="mcp-panel" id="tools-panel">
                <button class="mcp-panel-header" onclick="togglePanel('tools-panel')" aria-expanded="true">
                    <span class="mcp-panel-title">Tools <span class="mcp-panel-count" id="tools-count"></span></span>
                    ${chevron}
                </button>
                <div class="mcp-panel-body" id="tools-body">
                    <div class="mcp-panel-loading">Loading\u2026</div>
                </div>
            </div>
            <div class="mcp-panel" id="resources-panel">
                <button class="mcp-panel-header" onclick="togglePanel('resources-panel')" aria-expanded="true">
                    <span class="mcp-panel-title">Resources <span class="mcp-panel-count" id="resources-count"></span></span>
                    ${chevron}
                </button>
                <div class="mcp-panel-body" id="resources-body">
                    <div class="mcp-panel-loading">Loading\u2026</div>
                </div>
            </div>
        </div>
    `);
})();

function togglePanel(panelId) {
    const panel    = document.getElementById(panelId);
    const body     = panel.querySelector(".mcp-panel-body");
    const btn      = panel.querySelector(".mcp-panel-header");
    const expanded = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!expanded));
    body.style.maxHeight = expanded ? "0" : "";
    panel.classList.toggle("collapsed", expanded);
}

function showDescToast(e, text) {
    if (!text) return;

    // Replace any existing toast
    document.querySelectorAll(".desc-toast").forEach(t => t.remove());

    const toast = document.createElement("div");
    toast.className = "desc-toast";
    toast.textContent = text;
    document.body.appendChild(toast);

    const pad = 14;
    let x = e.clientX + pad;
    let y = e.clientY + pad;

    requestAnimationFrame(() => {
        const r = toast.getBoundingClientRect();
        if (x + r.width  > window.innerWidth  - 8) x = e.clientX - r.width  - pad;
        if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
        toast.style.left = x + "px";
        toast.style.top  = y + "px";
    });
}

function openResource(el) {
    hideDescToast();
    const url = el.dataset.url;
    if (url) window.open(url, "_blank", "noopener,noreferrer");
}

function hideDescToast() {
    document.querySelectorAll(".desc-toast").forEach(t => {
        t.style.opacity = "0";
        setTimeout(() => t.remove(), 420);
    });
}

(async function loadMcpInfo() {
    try {
        const res  = await fetch("/mcp-info");
        const data = await res.json();

        const toolsBody  = document.getElementById("tools-body");
        const toolsCount = document.getElementById("tools-count");
        toolsCount.textContent = data.tools.length;
        toolsBody.innerHTML = data.tools.length
            ? data.tools.map(t => `
                <div class="mcp-item"
                     data-desc="${(t.description || "").replace(/"/g, "&quot;")}"
                     onmouseenter="showDescToast(event, this.dataset.desc)"
                     onmouseleave="hideDescToast()"
                     onclick="showDescToast(event, this.dataset.desc)">
                    <span class="mcp-item-dot"></span>
                    <span class="mcp-item-name">${t.name}</span>
                </div>`).join("")
            : `<div class="mcp-panel-empty">No tools found</div>`;

        const resBody  = document.getElementById("resources-body");
        const resCount = document.getElementById("resources-count");
        resCount.textContent = data.resources.length;
        resBody.innerHTML = data.resources.length
            ? data.resources.map(r => `
                <div class="mcp-item mcp-item--link"
                     data-desc="${(r.description || r.uri).replace(/"/g, "&quot;")}"
                     data-url="${(r.url || "").replace(/"/g, "&quot;")}"
                     onmouseenter="showDescToast(event, this.dataset.desc)"
                     onmouseleave="hideDescToast()"
                     onclick="openResource(this)">
                    <span class="mcp-item-dot mcp-item-dot--resource"></span>
                    <span class="mcp-item-name">${r.name}</span>
                    <svg class="mcp-item-ext" xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                </div>`).join("")
            : `<div class="mcp-panel-empty">No resources found</div>`;

    } catch (_) {
        ["tools-body", "resources-body"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = `<div class="mcp-panel-empty">Unavailable</div>`;
        });
    }
})();
