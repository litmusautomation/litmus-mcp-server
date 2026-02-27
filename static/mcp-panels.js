// ── Left panel toggle ───────────────────────────────────────────────────────

function togglePanel(panelId) {
    const panel    = document.getElementById(panelId);
    const body     = panel.querySelector(".mcp-panel-body");
    const btn      = panel.querySelector(".mcp-panel-header");
    const expanded = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!expanded));
    body.style.maxHeight = expanded ? "0" : "";
    panel.classList.toggle("collapsed", expanded);
}

// ── Description toast ───────────────────────────────────────────────────────

function showDescToast(e, text) {
    if (!text) return;
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

// ── Helpers ──────────────────────────────────────────────────────────────────

function escHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Minimal markdown renderer ────────────────────────────────────────────────

function _inline(s) {
    s = escHtml(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    return s;
}

function renderMarkdown(md) {
    if (!md) return '<p class="md-empty">No description available.</p>';
    const lines = md.split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
        const ln = lines[i];
        // Fenced code block
        if (ln.startsWith("```")) {
            const code = [];
            i++;
            while (i < lines.length && !lines[i].startsWith("```")) code.push(escHtml(lines[i++]));
            out.push(`<pre><code>${code.join("\n")}</code></pre>`);
            i++; continue;
        }
        if (!ln.trim()) { i++; continue; }
        // Heading
        const hm = ln.match(/^(#{1,3}) (.+)/);
        if (hm) { out.push(`<h${hm[1].length}>${_inline(hm[2])}</h${hm[1].length}>`); i++; continue; }
        // Unordered list
        if (/^[-*] /.test(ln)) {
            const items = [];
            while (i < lines.length && /^[-*] /.test(lines[i])) items.push(`<li>${_inline(lines[i++].slice(2))}</li>`);
            out.push(`<ul>${items.join("")}</ul>`); continue;
        }
        // Ordered list
        if (/^\d+\. /.test(ln)) {
            const items = [];
            while (i < lines.length && /^\d+\. /.test(lines[i])) items.push(`<li>${_inline(lines[i++].replace(/^\d+\. /, ""))}</li>`);
            out.push(`<ol>${items.join("")}</ol>`); continue;
        }
        // Paragraph
        const p = [];
        while (i < lines.length && lines[i].trim() && !lines[i].startsWith("```") && !/^#{1,3} /.test(lines[i]) && !/^[-*\d]/.test(lines[i])) {
            p.push(_inline(lines[i++]));
        }
        if (p.length) out.push(`<p>${p.join("<br>")}</p>`);
    }
    return out.join("") || '<p class="md-empty">No description available.</p>';
}

// ── Tool detail modal ────────────────────────────────────────────────────────

function openToolModal(name, desc) {
    document.getElementById("tool-modal-name").textContent = name;
    document.getElementById("tool-modal-body").innerHTML = renderMarkdown(desc);
    const bd = document.getElementById("tool-modal-backdrop");
    bd.classList.add("open");
    bd.removeAttribute("aria-hidden");
}

function closeToolModal() {
    const bd = document.getElementById("tool-modal-backdrop");
    bd.classList.remove("open");
    bd.setAttribute("aria-hidden", "true");
}

document.addEventListener("keydown", e => { if (e.key === "Escape") closeToolModal(); });

// ── Load tools + resources (cached per session) ─────────────────────────────

(async function loadMcpInfo() {
    try {
        let data;
        const cached = sessionStorage.getItem("mcp_info");
        if (cached) {
            data = JSON.parse(cached);
        } else {
            const res = await fetch("/mcp-info");
            data = await res.json();
            sessionStorage.setItem("mcp_info", JSON.stringify(data));
        }

        const toolsBody  = document.getElementById("tools-body");
        const toolsCount = document.getElementById("tools-count");
        toolsCount.textContent = data.tools.length;
        toolsBody.innerHTML = data.tools.length
            ? data.tools.map(t => `
                <div class="mcp-item"
                     data-name="${escAttr(t.name)}"
                     data-desc="${escAttr(t.description || "")}"
                     onmouseenter="showDescToast(event, this.dataset.desc)"
                     onmouseleave="hideDescToast()"
                     onclick="openToolModal(this.dataset.name, this.dataset.desc)">
                    <span class="mcp-item-dot"></span>
                    <span class="mcp-item-name">${escHtml(t.name)}</span>
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

// ── Right panel: toggle ─────────────────────────────────────────────────────

function toggleMcpConfig() {
    document.getElementById("mcp-config-panel").classList.toggle("collapsed");
}

// ── Right panel: config JSON editor ────────────────────────────────────────

let _mcpCfg = {};
let _mcpCfgCurrentTab = "cursor";

function _mcpCfgHeaders() {
    const h = {
        "EDGE_URL":               _mcpCfg.edge_url        || "",
        "EDGE_API_CLIENT_ID":     _mcpCfg.client_id       || "",
        "EDGE_API_CLIENT_SECRET": _mcpCfg.client_secret   || "",
    };
    if (_mcpCfg.nats_source)     h["NATS_SOURCE"]     = _mcpCfg.nats_source;
    if (_mcpCfg.nats_port)       h["NATS_PORT"]        = _mcpCfg.nats_port;
    if (_mcpCfg.nats_user)       h["NATS_USER"]        = _mcpCfg.nats_user;
    if (_mcpCfg.nats_password)   h["NATS_PASSWORD"]    = _mcpCfg.nats_password;
    if (_mcpCfg.influx_host)     h["INFLUX_HOST"]      = _mcpCfg.influx_host;
    if (_mcpCfg.influx_port)     h["INFLUX_PORT"]      = _mcpCfg.influx_port;
    if (_mcpCfg.influx_db_name)  h["INFLUX_DB_NAME"]   = _mcpCfg.influx_db_name;
    if (_mcpCfg.influx_username) h["INFLUX_USERNAME"]  = _mcpCfg.influx_username;
    if (_mcpCfg.influx_password) h["INFLUX_PASSWORD"]  = _mcpCfg.influx_password;
    return h;
}

function _mcpCfgBuildJson(tab) {
    if (tab === "cursor") {
        return { mcpServers: { "litmus-mcp-server": { url: _mcpCfg.mcp_sse_url, headers: _mcpCfgHeaders() } } };
    }
    if (tab === "claude_code") {
        return { mcpServers: { "litmus-mcp-server": { type: "sse", url: _mcpCfg.mcp_sse_url, headers: _mcpCfgHeaders() } } };
    }
    const env = { PYTHONPATH: "/path/to/litmus-mcp-server/src" };
    Object.assign(env, _mcpCfgHeaders());
    return {
        mcpServers: {
            "litmus-mcp-server": {
                command: "/path/to/.venv/bin/python3",
                args:    ["/path/to/litmus-mcp-server/src/server.py"],
                env
            }
        }
    };
}

function mcpCfgReset() {
    const ta = document.getElementById("mcp-cfg-editor");
    if (!ta) return;
    ta.value = JSON.stringify(_mcpCfgBuildJson(_mcpCfgCurrentTab), null, 2);
}

function mcpCfgSwitchTab(tab) {
    _mcpCfgCurrentTab = tab;
    document.querySelectorAll("#mcp-config-panel .config-tab").forEach(b =>
        b.classList.toggle("active", b.dataset.tab === tab)
    );
    mcpCfgReset();
}

function mcpCfgCopy() {
    const ta = document.getElementById("mcp-cfg-editor");
    if (!ta) return;
    navigator.clipboard.writeText(ta.value).then(() => {
        const btn = document.getElementById("mcp-cfg-copy-btn");
        const orig = btn.innerHTML;
        btn.textContent = "Copied!";
        setTimeout(() => { btn.innerHTML = orig; }, 1500);
    });
}

(async function loadMcpClientConfig() {
    try {
        const res = await fetch("/api/mcp-client-config");
        _mcpCfg   = await res.json();
    } catch (_) {}
    mcpCfgReset();
})();
