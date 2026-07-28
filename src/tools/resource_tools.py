"""
Resource tools for providing documentation and contextual information.

Resources in MCP are used to expose information that the AI can reference,
such as documentation, configuration files, or other contextual data.
"""

import logging
from typing import Any

import httpx
from mcp.types import TextContent

logger = logging.getLogger(__name__)

# Documentation structure for Litmus platform
LITMUS_DOCS_BASE = "https://docs.litmus.io"
LITMUS_API_BASE = "https://api.litmus.io"

DOCUMENTATION_RESOURCES = {
    # Overview documentation. Served from this server rather than fetched:
    # the docs.litmus.io landing page carries no body of its own (its
    # markdown twin is frontmatter only, its HTML is ~96 KB of navigation),
    # so there is nothing useful upstream to return for this entry.
    "litmus://docs/overview": {
        "name": "Litmus Platform Overview",
        "description": "High-level overview of the Litmus Industrial DataOps platform",
        "uri": f"{LITMUS_DOCS_BASE}",
        "mimeType": "text/markdown",
        "index": True,
    },
    # Litmus Edge documentation
    "litmus://docs/edge": {
        "name": "Litmus Edge Documentation",
        "description": "Complete documentation for Litmus Edge platform",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge",
        "mimeType": "text/markdown",
    },
    "litmus://docs/edge/devicehub": {
        "name": "DeviceHub Documentation",
        "description": "How to connect and manage industrial devices using DeviceHub",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/devicehub",
        "mimeType": "text/markdown",
    },
    "litmus://docs/edge/digitaltwins": {
        "name": "Digital Twins Documentation",
        "description": "Creating and managing digital twin models and instances",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/digital-twins",
        "mimeType": "text/markdown",
    },
    "litmus://docs/edge/datahub": {
        "name": "DataHub Documentation",
        "description": "Pub/sub messaging and data flow with DataHub",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/datahub",
        "mimeType": "text/markdown",
    },
    "litmus://docs/edge/marketplace": {
        "name": "Marketplace Documentation",
        "description": "Deploying and managing containerized applications",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/applications",
        "mimeType": "text/markdown",
    },
    # Edge Manager documentation
    "litmus://docs/edgemanager": {
        "name": "Litmus Edge Manager Documentation",
        "description": "Centralized management and monitoring of edge deployments",
        "uri": f"{LITMUS_DOCS_BASE}/edgemanager",
        "mimeType": "text/markdown",
    },
    "litmus://docs/edgemanager/marketplace": {
        "name": "Edge Manager Marketplace Catalogs",
        "description": "Managing marketplace catalogs and applications from Edge Manager",
        "uri": f"{LITMUS_DOCS_BASE}/edgemanager/lem-user-ui/product-features/marketplace-catalogs-and-applications",
        "mimeType": "text/markdown",
    },
    "litmus://docs/edgemanager/grafana": {
        "name": "Grafana Dashboards Documentation",
        "description": "Creating and managing Grafana dashboards for visualization",
        "uri": f"{LITMUS_DOCS_BASE}/edgemanager/lem-user-ui/product-features/grafana-dashboards",
        "mimeType": "text/markdown",
    },
    # Solutions documentation
    "litmus://docs/solutions": {
        "name": "Litmus Solutions Documentation",
        "description": "Industry-specific solution packages and templates",
        "uri": f"{LITMUS_DOCS_BASE}/solutions",
        "mimeType": "text/markdown",
    },
    # UNS documentation
    "litmus://docs/uns": {
        "name": "Litmus UNS Documentation",
        "description": "Unified Namespace implementation and configuration",
        "uri": f"{LITMUS_DOCS_BASE}/uns",
        "mimeType": "text/markdown",
    },
    # API documentation. Points at the API portal's agent router rather than
    # the portal itself: the router is markdown built for this purpose and
    # links onward to per-product routers, while the portal is a ~57 KB HTML
    # page. The router deliberately does not link /collections.json, which is
    # ~30 MB and would exhaust a client's context.
    "litmus://docs/api": {
        "name": "Litmus API Documentation",
        "description": "REST API reference for Litmus platform",
        "uri": f"{LITMUS_API_BASE}/agents.md",
        "mimeType": "text/markdown",
    },
}


# Shortest markdown body accepted as a real page. Archbee answers a docs URL
# whose page has no body of its own with frontmatter and nothing else, which
# is well under this.
_MARKDOWN_MIN_BYTES = 200


def _markdown_url(url: str) -> str:
    """Markdown twin of a documentation page.

    docs.litmus.io (Archbee) serves one at "<page>.md" for every page: a few
    KB of prose instead of a ~300 KB HTML bundle whose <main> is mostly
    navigation and inlined JavaScript. URLs that already name a .md file (the
    api.litmus.io agent routers) are returned unchanged.
    """
    return url if url.endswith(".md") else f"{url}.md"


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block.

    Archbee prefixes every markdown page with title, slug and description,
    all of which the resource header already states.
    """
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


async def _fetch_markdown(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch the markdown twin of a page, or None if there isn't a usable one.

    Archbee answers an unknown ".md" path with HTTP 200 and the HTML
    single-page-app shell, so the status code alone proves nothing: the
    content type and the first non-space byte are both checked before the
    body is accepted, and a body that is only frontmatter is rejected.
    """
    markdown_url = _markdown_url(url)
    try:
        response = await client.get(markdown_url)
    except httpx.HTTPError as e:
        logger.warning(f"Markdown fetch failed for {markdown_url}: {e}")
        return None
    if response.status_code != 200:
        return None
    if "markdown" not in response.headers.get("content-type", "").lower():
        return None
    body = response.text.lstrip()
    if body.startswith("<"):
        return None
    body = _strip_frontmatter(body).strip()
    return body if len(body) >= _MARKDOWN_MIN_BYTES else None


def _main_content(html: str) -> str:
    """Best-effort extraction of a page's <main> element."""
    if "<main" in html:
        start = html.find("<main")
        end = html.find("</main>", start)
        if end != -1:
            return html[start : end + 7]
    return html


async def fetch_documentation_content(url: str) -> str:
    """
    Fetch a documentation page, preferring its markdown twin.

    Args:
        url: The URL of the documentation page

    Returns:
        Page text: markdown when one is published, otherwise the HTML
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            markdown = await _fetch_markdown(client, url)
            if markdown is not None:
                return markdown

            logger.info(f"No markdown twin for {url}, falling back to HTML")
            response = await client.get(url)
            response.raise_for_status()
            return _main_content(response.text)

    except httpx.HTTPError as e:
        logger.error(f"Error fetching documentation from {url}: {e}")
        return f"Error fetching documentation: {e!s}"
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {e}")
        return f"Error: {e!s}"


def _resource_index() -> str:
    """Body of the overview resource: an index of the others.

    Generated from DOCUMENTATION_RESOURCES so it cannot drift out of step
    with what the server actually advertises.
    """
    lines = [
        "Litmus is an Industrial DataOps platform. Litmus Edge runs on the plant "
        "floor and collects, normalizes and contextualizes machine data; Litmus "
        "Edge Manager centrally manages a fleet of Edge instances; Litmus Unify "
        "publishes that data as a Unified Namespace.",
        "",
        "## Documentation resources on this server",
        "",
    ]
    for resource_uri, info in DOCUMENTATION_RESOURCES.items():
        if info.get("index"):
            continue
        lines.append(f"- `{resource_uri}` - {info['name']}: {info['description']}")
    lines += [
        "",
        "## Elsewhere",
        "",
        f"- Product documentation: {LITMUS_DOCS_BASE}",
        f"- API reference and per-product routers: {LITMUS_API_BASE}/agents.md",
        "- Tools on this server: call `litmus_sdk_discover` to browse the full "
        "litmus-cli function catalog when no dedicated tool covers an operation.",
    ]
    return "\n".join(lines)


async def read_documentation_resource(uri: str) -> list[TextContent]:
    """
    Read a documentation resource by URI.

    Args:
        uri: The resource URI (e.g., "litmus://docs/edge/devicehub")

    Returns:
        List containing a TextContent object with the documentation
    """
    if uri not in DOCUMENTATION_RESOURCES:
        return [
            TextContent(
                type="text",
                text=f"Unknown documentation resource: {uri}\n\nAvailable resources:\n"
                + "\n".join(
                    f"  - {k}: {v['name']}" for k, v in DOCUMENTATION_RESOURCES.items()
                ),
            )
        ]

    doc_info = DOCUMENTATION_RESOURCES[uri]
    doc_url = doc_info["uri"]

    if doc_info.get("index"):
        content = _resource_index()
    else:
        logger.info(f"Fetching documentation: {doc_info['name']} from {doc_url}")
        content = await fetch_documentation_content(doc_url)

    # Format the response
    result_text = f"""# {doc_info['name']}

**Source:** {doc_url}
**Description:** {doc_info['description']}

---

{content}

---
*For the most up-to-date information, visit: {doc_url}*
"""

    return [TextContent(type="text", text=result_text)]


def get_documentation_resource_list() -> list[dict[str, Any]]:
    """
    Get the list of all available documentation resources.

    Returns:
        List of resource definitions
    """
    return [
        {
            "uri": uri,
            "name": info["name"],
            "description": info["description"],
            "mimeType": info["mimeType"],
        }
        for uri, info in DOCUMENTATION_RESOURCES.items()
    ]
