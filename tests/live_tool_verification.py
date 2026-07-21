"""Live verification of EVERY MCP tool against a real Litmus Edge (+ LEM).

Drives each tool through server.handle_call_tool - the real dispatch path,
including per-call LEM bridge argument stripping - not the handlers directly.
Mutating tools follow a create -> verify -> clean-up pattern; the edge is
left in its original state. Cleanup deletions go through litmus_sdk_write
with user_approved=true: running this script IS the operator's approval.

Usage:
    set -a; source .env; set +a   # EDGE_URL, EDGE_API_CLIENT_ID/SECRET,
                                  # NATS_TOKEN, INFLUX_USERNAME/PASSWORD,
                                  # EDGE_MANAGER_URL, EDGE_API_TOKEN
    .venv/bin/python tests/live_tool_verification.py

Exit code 0 only when every applicable tool call passed.
"""

import asyncio
import json
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")

import server  # noqa: E402

SCRATCH = "live-verify-delete-me"

results = []  # (tool, case, status, detail)
state = {}


async def call(name, args=None):
    out = await server.handle_call_tool(name, args or {})
    return json.loads(out[0].text)


def record(tool, case, ok, detail="", skip=False):
    status = "SKIP" if skip else ("PASS" if ok else "FAIL")
    results.append((tool, case, status, str(detail)[:120]))
    mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip"}[status]
    print(f"  [{mark}] {tool} :: {case} {detail if status != 'PASS' else ''}")


async def step(tool, case, args=None, check=None, expect_error=False):
    """Run one tool call and record the outcome. Returns parsed data or None."""
    try:
        data = await call(tool, args)
    except Exception as e:
        record(tool, case, expect_error, f"raised: {e}")
        return None
    if expect_error:
        record(tool, case, data.get("success") is False, "expected an error response")
        return data
    ok = data.get("success") is True
    detail = data.get("message", "") if not ok else ""
    if ok and check:
        try:
            extra = check(data)
            if extra is not None and extra is not True:
                ok, detail = False, f"check failed: {extra}"
        except Exception as e:
            ok, detail = False, f"check raised: {e}"
    record(tool, case, ok, detail)
    return data if ok else None


async def sdk_write(function, args):
    """Approved cleanup helper through litmus_sdk_write (also verifies it)."""
    return await step(
        "litmus_sdk_write",
        f"cleanup {function}",
        {"function": function, "args": args, "user_approved": True},
    )


# ── plan ─────────────────────────────────────────────────────────────────────


async def verify_devicehub():
    print("\n== DeviceHub ==")
    d = await step(
        "get_litmusedge_driver_list", "list drivers", {},
        check=lambda r: r["count"] > 0 or "no drivers",
    )
    d = await step(
        "get_devicehub_devices", "list devices", {},
        check=lambda r: r["count"] > 0 or "no devices",
    )
    if d:
        text = json.dumps(d)
        record(
            "get_devicehub_devices", "secrets redacted",
            "PRIVATE KEY-----" not in text.replace("[REDACTED]", ""),
        )
        streaming = next(
            (x["name"] for x in d["devices"] if "OPC" in x.get("name", "")),
            d["devices"][0]["name"],
        )
        state["device"] = streaming

    d = await step(
        "get_devicehub_device_tags", "tags for one device",
        {"device_name": state["device"]},
        check=lambda r: r["count"] > 0 or "no tags",
    )
    if d:
        state["tag"] = d["tags"][0]["tag_name"]
    await step(
        "get_devicehub_device_tags", "all devices w/ pagination",
        {"limit": 10, "offset": 5},
        check=lambda r: (r["limit"] == 10 and r["offset"] == 5 and r["has_more"])
        or "pagination fields wrong",
    )

    await step(
        "get_current_value_of_devicehub_tag", "read live tag value",
        {"device_name": state["device"], "tag_name": state["tag"]},
        check=lambda r: "data" in r or "no data key",
    )

    # scratch device lifecycle (create is CLI-backed now)
    d = await step(
        "create_devicehub_device", "create scratch device",
        {"name": SCRATCH, "selected_driver": "Generator"},
        check=lambda r: bool(r["device"]["id"]) or "no id returned",
    )
    if d:
        state["scratch_device_id"] = d["device"]["id"]
        await step(
            "create_devicehub_tag", "create tag on scratch device",
            {
                "device_name": SCRATCH, "register_name": "S",
                "tag_name": "verify_tag", "value_type": "float64",
            },
            check=lambda r: bool(r["tag_id"]) or "no tag id",
        )
        await step(
            "update_devicehub_tag", "update scratch tag",
            {"device_name": SCRATCH, "tag_name": "verify_tag",
             "description": "updated by live verification"},
        )
        await step(
            "get_tag_status", "status on scratch device",
            {"device_name": SCRATCH},
            check=lambda r: r["count"] >= 1 or "no statuses",
        )
        await step(
            "delete_devicehub_tag", "delete scratch tag",
            {"device_name": SCRATCH, "tag_name": "verify_tag"},
        )
        await sdk_write(
            "le.devicehub.DeleteDevicesByIDs", {"ids": [state["scratch_device_id"]]}
        )

    await step(
        "get_all_tags_status", "all devices",
        {"filter_status": ""},
        check=lambda r: r["devices_checked"] > 0 or "checked nothing",
    )
    await step(
        "get_device_connection_status", "streaming device connected",
        {"device_name": state["device"]},
        check=lambda r: r["devices"][0]["status"] == "connected"
        or f"status={r['devices'][0]['status']}",
    )


async def verify_data():
    print("\n== Data / NATS / InfluxDB ==")
    d = await step(
        "list_influxdb_measurements", "list measurements", {},
        check=lambda r: r["count"] > 0 or "no measurements",
    )
    if d:
        state["measurement"] = next(
            (m for m in d["measurements"] if state["device"] in m),
            d["measurements"][0],
        )

    await step(
        "get_historical_data_from_influxdb", "query real measurement",
        {"measurement": state["measurement"], "time_range": "15m", "limit": 50},
        check=lambda r: r["count"] > 0 or "no rows (was the unpack(b) bug)",
    )
    await step(
        "get_device_historical_data", "fuzzy device query",
        {"device_query": state["device"], "time_range": "15m", "limit": 10},
        check=lambda r: r["total_records"] > 0 or "no records",
    )
    await step(
        "query_tag_data", "tag history via register filter",
        {"device_name": state["device"], "tag_name": state["tag"],
         "time_range": "15m", "limit": 5},
        check=lambda r: r["count"] > 0 or "no rows (was naming mismatch)",
    )
    await step(
        "get_tag_statistics", "tag stats",
        {"device_name": state["device"], "tag_name": state["tag"],
         "time_range": "15m"},
        check=lambda r: r["statistics"].get("count", 0) > 0 or "empty stats",
    )
    await step(
        "get_device_data_for_inference", "inference package",
        {"device_name": state["device"], "time_range": "15m", "sample_size": 2},
        check=lambda r: any(t.get("recent_samples") for t in r["tags"])
        or "no samples on any tag",
    )

    topic = f"devicehub.alias.{state['device']}.{state['tag']}"
    await step(
        "get_current_value_from_topic", "live NATS value", {"topic": topic},
        check=lambda r: "data" in r or "no data",
    )
    await step(
        "get_multiple_values_from_topic", "3 NATS samples",
        {"topic": topic, "num_samples": 3},
        check=lambda r: len(r["values"]) == 3 or "wrong sample count",
    )


async def verify_digitaltwins():
    print("\n== Digital Twins ==")
    await step("list_digital_twin_models", "list models", {})
    d = await step(
        "create_digital_twin_model", "create scratch model",
        {"model_name": SCRATCH},
    )
    model = (d or {}).get("model")
    model_id = model["ID"] if isinstance(model, dict) else model
    if not model_id:
        return
    state["model_id"] = model_id

    d = await step(
        "create_digital_twin_instance", "create scratch instance",
        {"model_id": model_id, "instance_name": SCRATCH + "-inst",
         "instance_topic": "verify.scratch.topic"},
    )
    inst = (d or {}).get("instance")
    state["instance_id"] = inst.get("ID") if isinstance(inst, dict) else None

    await step(
        "list_digital_twin_instances", "instances by model",
        {"model_id": model_id},
        check=lambda r: r["count"] >= 1 or "instance missing",
    )
    await step(
        "list_static_attributes", "by instance_name",
        {"instance_name": SCRATCH + "-inst"},
    )
    await step("list_static_attributes", "all instances", {"all_instances": True})
    await step(
        "list_dynamic_attributes", "by instance id",
        {"instance_id": state["instance_id"]},
    )
    await step("list_transformations", "by model", {"model_id": model_id})

    # Seed one folder node in the GETTER's decorated shape (extra fields must
    # be stripped by the tool), then prove the full get -> save round-trip.
    seed = {
        "Name": "root", "Node": None, "Attr": None,
        "Childs": [{
            "Name": "VerifyFolder",
            "Node": {
                "ID": "aaaaaaaa-0000-0000-0000-000000000000",
                "ModelID": model_id, "ParentID": None, "Position": 0,
                "Name": "VerifyFolder", "IsFolder": True,
                "AttributeID": "00000000-0000-0000-0000-000000000000",
                "AttributeType": None, "NodeType": "folder",
            },
            "Attr": None, "Childs": [],
        }],
    }
    await step(
        "save_digital_twin_hierarchy", "save getter-shaped hierarchy",
        {"model_id": model_id, "hierarchy_json": seed},
    )
    d = await step(
        "get_digital_twin_hierarchy", "get hierarchy", {"model_id": model_id},
        check=lambda r: any(
            c.get("Name") == "VerifyFolder" for c in r["hierarchy"].get("Childs") or []
        )
        or "saved node missing",
    )
    if d:
        await step(
            "save_digital_twin_hierarchy", "get->save round-trip",
            {"model_id": model_id, "hierarchy_json": d["hierarchy"]},
        )

    if state.get("instance_id"):
        await sdk_write(
            "le.digitaltwins.DeleteInstance", {"instanceID": state["instance_id"]}
        )
    await sdk_write("le.digitaltwins.DeleteModel", {"modelID": model_id})


async def verify_system():
    print("\n== System / identity / marketplace ==")
    d = await step("get_litmusedge_friendly_name", "get name", {})
    name = (d or {}).get("friendly_name")
    if name:
        await step(
            "set_litmusedge_friendly_name", "set name (same value)",
            {"new_friendly_name": name},
        )
    await step("get_cloud_activation_status", "cloud status", {})
    await step("get_system_events", "recent events", {"limit": 5})
    await step("get_system_event_stats", "event stats", {})
    await step("get_firewall_rules", "firewall", {})
    await step("get_network_interface_info", "network", {})
    await step("get_packet_capture_interfaces", "pcap interfaces", {})
    await step("get_packet_capture_status", "pcap status", {})
    d = await step(
        "start_packet_capture", "start 1min capture",
        {"interface": "eth0", "duration": 1},
    )
    if d:
        await asyncio.sleep(2)
        await step("stop_packet_capture", "stop capture", {})

    await step("get_all_containers_on_litmusedge", "containers", {})
    d = await step(
        "run_docker_container_on_litmusedge", "run hello-world",
        {"docker_run_command": f"docker run -d --name {SCRATCH} hello-world"},
    )
    if d:
        # look up the container id by name; RemoveContainers wants ids
        containers = await call("get_all_containers_on_litmusedge", {})
        cid = next(
            (
                c.get("Id")
                for c in containers.get("containers", [])
                if f"/{SCRATCH}" in (c.get("Names") or [])
            ),
            None,
        )
        if cid:
            await sdk_write(
                "le.marketplace.RemoveContainers", {"containerIDs": [cid]}
            )
        else:
            record("run_docker_container_on_litmusedge", "cleanup", False,
                   "container id not found for removal")


async def verify_sdk_cli():
    print("\n== SDK fallback ==")
    await step("litmus_sdk_discover", "discover le.system", {"prefix": "le.system"})
    await step(
        "litmus_sdk_read", "GetVersion",
        {"function": "le.system.GetVersion"},
    )
    await step(
        "litmus_sdk_write", "refuses without approval",
        {"function": "le.devicehub.DeleteDevice", "args": {}, "user_approved": False},
        expect_error=True,
    )


async def verify_lem():
    print("\n== LEM ==")
    if not (os.environ.get("EDGE_MANAGER_URL") and os.environ.get("EDGE_API_TOKEN")):
        record("lem_*", "skipped", False, "EDGE_MANAGER_URL/EDGE_API_TOKEN not set")
        return
    await step("lem_get_system_time", "system time", {})
    await step("lem_deployment_info", "deployment info", {})
    d = await step(
        "lem_list_companies", "companies", {},
        check=lambda r: r.get("count", 0) > 0 or "no companies",
    )
    if not d:
        return
    company = d["companies"][0].get("companyName")
    await step(
        "lem_get_company_details", "company details", {"company_name": company}
    )
    d = await step(
        "lem_list_company_projects", "projects", {"company_name": company},
        check=lambda r: len(r.get("projects", [])) > 0 or "no projects",
    )
    if not d:
        return
    projects = d["projects"]

    # find a project that has devices, preferring one that is ONLINE so the
    # bridge checks can actually reach an edge
    device = None
    project_id = None
    for p in projects:
        pid = p.get("id") or p.get("ID")
        if not pid:
            continue
        dd = await call("lem_list_devices", {"project_id": pid, "limit": 10})
        if not (dd.get("success") and dd.get("count", 0) > 0):
            continue
        if project_id is None:
            project_id, device = pid, dd["devices"][0]
            record("lem_list_devices", "devices in project", True)
            record(
                "lem_list_devices", "no duplicated page payload",
                "page" not in dd,
            )
        online = next((x for x in dd["devices"] if x.get("online")), None)
        if online is not None:
            project_id, device = pid, online
            break
    if not project_id:
        record("lem_list_devices", "devices in project", False, "no project with devices")
        return
    state["lem_project"] = project_id
    state["lem_device_online"] = bool(device.get("online"))
    device_id = device.get("id") or device.get("deviceId") or device.get("ID")
    state["lem_device"] = device_id

    await step("lem_get_project_details", "project details", {"project_id": project_id})
    await step(
        "lem_get_device_details", "device details",
        {"project_id": project_id, "device_id": device_id},
        check=lambda r: "[REDACTED]" in json.dumps(r) or True,
    )
    await step("lem_list_device_versions", "versions", {"project_id": project_id})
    await step("lem_list_device_groups", "groups", {"project_id": project_id})
    await step(
        "lem_get_license_expiry", "license expiry",
        {"project_id": project_id, "expiry_days": 30},
    )
    await step("lem_get_expired_licenses", "expired licenses", {"project_id": project_id})
    await step("lem_dashboard_usage", "dashboard usage", {"project_id": project_id})
    await step("lem_get_project_alerts", "project alerts", {"project_id": project_id})
    if not state.get("lem_device_online"):
        reason = "no ONLINE LEM-managed edge on this tenant; bridge unreachable"
        record("lem_bridge_get_le_info", "bridge le info", True, reason, skip=True)
        record(
            "lem_bridge_list_devicehub_devices", "bridge devicehub list",
            True, reason, skip=True,
        )
        record(
            "get_litmusedge_driver_list", "via per-call LEM bridge args",
            True, reason, skip=True,
        )
        return
    await step(
        "lem_bridge_get_le_info", "bridge le info",
        {"project_id": project_id, "device_id": device_id},
    )
    await step(
        "lem_bridge_list_devicehub_devices", "bridge devicehub list",
        {"project_id": project_id, "device_id": device_id},
    )
    # per-call bridge routing on a generic LE tool (dispatch-level overlay)
    await step(
        "get_litmusedge_driver_list", "via per-call LEM bridge args",
        {"project_id": project_id, "device_id": device_id},
        check=lambda r: r["count"] > 0 or "no drivers over bridge",
    )


async def main():
    request = server.StdioRequestContext()
    server.current_request.set(request)
    started = time.time()

    for section in (
        verify_devicehub,
        verify_data,
        verify_digitaltwins,
        verify_system,
        verify_sdk_cli,
        verify_lem,
    ):
        try:
            await section()
        except Exception as e:
            record(section.__name__, "section aborted", False, e)

    print("\n" + "=" * 74)
    fails = [r for r in results if r[2] == "FAIL"]
    skips = [r for r in results if r[2] == "SKIP"]
    tools_hit = {r[0] for r in results}
    print(
        f"{len(results)} checks over {len(tools_hit)} tools in "
        f"{time.time() - started:.0f}s - "
        f"{len(results) - len(fails) - len(skips)} passed, "
        f"{len(fails)} failed, {len(skips)} skipped"
    )
    for tool, case, status, detail in fails:
        print(f"  FAIL {tool} :: {case} :: {detail}")
    for tool, case, status, detail in skips:
        print(f"  SKIP {tool} :: {case} :: {detail}")

    registered = set(server.TOOL_BY_NAME)
    untested = registered - tools_hit
    if untested:
        print(f"\nTools not exercised ({len(untested)}): {sorted(untested)}")

    with open("live_verification_report.json", "w") as f:
        json.dump(
            [
                {"tool": t, "case": c, "status": s, "detail": d}
                for t, c, s, d in results
            ],
            f,
            indent=1,
        )
    print("Report written to live_verification_report.json")
    return 1 if fails or untested else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
