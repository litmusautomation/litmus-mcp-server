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
    # Overview documentation
    "litmus://docs/overview": {
        "name": "Litmus Platform Overview",
        "description": "High-level overview of the Litmus Industrial DataOps platform",
        "uri": f"{LITMUS_DOCS_BASE}",
        "mimeType": "text/html",
    },
    # Litmus Edge documentation
    "litmus://docs/edge": {
        "name": "Litmus Edge Documentation",
        "description": "Complete documentation for Litmus Edge platform",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge",
        "mimeType": "text/html",
    },
    "litmus://docs/edge/devicehub": {
        "name": "DeviceHub Documentation",
        "description": "How to connect and manage industrial devices using DeviceHub",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/devicehub",
        "mimeType": "text/html",
    },
    "litmus://docs/edge/digitaltwins": {
        "name": "Digital Twins Documentation",
        "description": "Creating and managing digital twin models and instances",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/digital-twins",
        "mimeType": "text/html",
    },
    "litmus://docs/edge/datahub": {
        "name": "DataHub Documentation",
        "description": "Pub/sub messaging and data flow with DataHub",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/datahub",
        "mimeType": "text/html",
    },
    "litmus://docs/edge/marketplace": {
        "name": "Marketplace Documentation",
        "description": "Deploying and managing containerized applications",
        "uri": f"{LITMUS_DOCS_BASE}/litmusedge/product-features/applications",
        "mimeType": "text/html",
    },
    # Edge Manager documentation
    "litmus://docs/edgemanager": {
        "name": "Litmus Edge Manager Documentation",
        "description": "Centralized management and monitoring of edge deployments",
        "uri": f"{LITMUS_DOCS_BASE}/edgemanager",
        "mimeType": "text/html",
    },
    "litmus://docs/edgemanager/marketplace": {
        "name": "Edge Manager Marketplace Catalogs",
        "description": "Managing marketplace catalogs and applications from Edge Manager",
        "uri": f"{LITMUS_DOCS_BASE}/edgemanager/lem-user-ui/product-features/marketplace-catalogs-and-applications",
        "mimeType": "text/html",
    },
    "litmus://docs/edgemanager/grafana": {
        "name": "Grafana Dashboards Documentation",
        "description": "Creating and managing Grafana dashboards for visualization",
        "uri": f"{LITMUS_DOCS_BASE}/edgemanager/lem-user-ui/product-features/grafana-dashboards",
        "mimeType": "text/html",
    },
    # Solutions documentation
    "litmus://docs/solutions": {
        "name": "Litmus Solutions Documentation",
        "description": "Industry-specific solution packages and templates",
        "uri": f"{LITMUS_DOCS_BASE}/solutions",
        "mimeType": "text/html",
    },
    # UNS documentation
    "litmus://docs/uns": {
        "name": "Litmus UNS Documentation",
        "description": "Unified Namespace implementation and configuration",
        "uri": f"{LITMUS_DOCS_BASE}/uns",
        "mimeType": "text/html",
    },
    # API documentation
    "litmus://docs/api": {
        "name": "Litmus API Documentation",
        "description": "REST API reference for Litmus platform",
        "uri": f"{LITMUS_API_BASE}",
        "mimeType": "text/html",
    },
}


async def fetch_documentation_content(url: str) -> str:
    """
    Fetch and extract the main content from a documentation page.

    Args:
        url: The URL of the documentation page

    Returns:
        Extracted text content from the page
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            # Return the HTML content
            # In a production system, you might want to:
            # 1. Parse HTML and extract main content
            # 2. Convert to markdown for better readability
            # 3. Cache results to avoid repeated fetches
            content = response.text

            # Simple heuristic: try to extract main content
            # This is a basic implementation - you may want to use BeautifulSoup
            # or other HTML parsing for better content extraction
            if "<main" in content:
                start = content.find("<main")
                end = content.find("</main>", start)
                if end != -1:
                    content = content[start : end + 7]

            return content

    except httpx.HTTPError as e:
        logger.error(f"Error fetching documentation from {url}: {e}")
        return f"Error fetching documentation: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {e}")
        return f"Error: {str(e)}"


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

    logger.info(f"Fetching documentation: {doc_info['name']} from {doc_url}")

    # Fetch the documentation content
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
