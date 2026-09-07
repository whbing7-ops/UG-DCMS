"""Installed UG-DCMS functional smoke test using independent simulated roles.

This deliberately drives the public HTTP API used by the UI buttons.  It creates
fresh, uniquely named demo data on every CI runner and fails on the first broken
business contract; no critical operation is silently skipped.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080/api/v1"
STAMP = str(int(time.time()))[-8:]
PASSWORDS = {
    "admin": ("Admin@12345", "Changed!Root" + STAMP),
    "engineer": ("UG.Engineer!" + STAMP, "UG.Engineer2!" + STAMP),
    "approver": ("UG.Approver!" + STAMP, "UG.Approver2!" + STAMP),
    "cm": ("UG.ConfigMgr!" + STAMP, "UG.ConfigMgr2!" + STAMP),
}
if len(sys.argv) > 2:
    PASSWORDS["admin"] = (sys.argv[2], "Changed!Root" + STAMP)


class Client:
    def __init__(self, token: str | None = None):
        self.token = token

    def call(self, method: str, path: str, body=None, expected=(200, 201)):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8")
                if response.status not in expected:
                    raise AssertionError(f"{method} {path}: HTTP {response.status}")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise AssertionError(f"{method} {path}: HTTP {exc.code}: {detail}") from exc

    def get(self, path): return self.call("GET", path)
    def post(self, path, body=None): return self.call("POST", path, body)
    def put(self, path, body=None): return self.call("PUT", path, body)
    def patch(self, path, body=None): return self.call("PATCH", path, body)
    def delete(self, path): return self.call("DELETE", path)


def login(username: str, password: str) -> tuple[Client, dict]:
    result = Client().post("/auth/login", {"username": username, "password": password})
    return Client(result["access_token"]), result


def change_initial(username: str, old: str, new: str) -> Client:
    client, result = login(username, old)
    if result["user"]["must_change_password"]:
        client.post("/auth/password", {"old_password": old, "new_password": new})
    return client


def q(path: str, **params) -> str:
    return path + "?" + urllib.parse.urlencode(params)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main() -> None:
    results: list[str] = []
    admin = change_initial("admin", *PASSWORDS["admin"])
    me = admin.get("/auth/me")
    check("user_manage" in me["permissions"], "admin user management permission missing")
    results.append("authentication/admin-password-change")

    accounts = {
        "engineer": ["ENGINEER"],
        "approver": ["APPROVER"],
        "cm": ["CONFIGURATION_MANAGER"],
    }
    usernames = {}
    for role, roles in accounts.items():
        username = f"ci_{role}_{STAMP}"
        usernames[role] = username
        admin.post("/admin/users", {
            "username": username, "full_name": "CI " + role,
            "password": PASSWORDS[role][0], "roles": roles,
        })
    results.append("admin/create-independent-role-accounts")

    engineer = change_initial(usernames["engineer"], *PASSWORDS["engineer"])
    approver = change_initial(usernames["approver"], *PASSWORDS["approver"])
    cm = change_initial(usernames["cm"], *PASSWORDS["cm"])
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps({
            "username": usernames["engineer"], "password": PASSWORDS["engineer"][1],
            "admin_username": "admin", "admin_password": PASSWORDS["admin"][1],
        }), encoding="utf-8")

    # Every navigation page must load for the role that owns it.
    for path in ("/reports/dashboard", "/recent?limit=10", "/quality/issues?limit=5",
                 "/families", "/external-parts", "/software", "/approvals/mine?include_closed=true",
                 "/dictionary", "/dictionary/physical-class", "/dictionary/core-term",
                 "/dictionary/function-item", "/dictionary/object-level"):
        engineer.get(path)
    admin.get("/admin/users")
    admin.get("/admin/sessions")
    admin.get("/admin/license")
    admin.get("/admin/audit?limit=10")
    results.append("navigation/all-top-level-data-contracts")

    pcs = engineer.get("/dictionary/physical-class")
    terms = engineer.get("/dictionary/core-term")
    funcs = engineer.get("/dictionary/function-item")
    levels = engineer.get("/dictionary/object-level")
    pc = next(x for x in pcs if x["primary_class_code"] == "T1")
    term = next(x for x in terms if x["primary_class_code"] == "T1")
    engineer.post("/families/similar-search", {
        "primary_class_code": "T1", "physical_class_id": pc["id"],
        "core_term_id": term["id"], "qualifier_ids": [],
    })
    family = engineer.post("/families", {
        "primary_class_code": "T1", "physical_class_id": pc["id"],
        "object_level_code": levels[0]["code"], "core_term_id": term["id"],
        "primary_function_id": funcs[0]["id"], "family_definition": "CI shared-PN family",
        "allowed_variation": "dash dimensions", "excluded_variation": "working principle",
        "new_family_reason": "functional smoke isolation",
    })
    engineer.post(f"/families/{family['id']}/submit")
    approved = approver.post(q(f"/families/{family['id']}/approve", comments="CI independent approval"))
    check(approved["status"] == "ACTIVE", "family was not activated")
    results.append("family/search-create-submit-independent-approve-number")

    parent = engineer.post(f"/families/{family['id']}/dashes", {
        "formal_name_cn": "CI演示成品", "formal_name_en": "CI DEMO ASSEMBLY",
        "object_level_code": levels[0]["code"], "difference_summary": "parent",
    })
    child_a = engineer.post(f"/families/{family['id']}/dashes", {
        "formal_name_cn": "CI演示子件A", "formal_name_en": "CI DEMO CHILD A",
        "object_level_code": levels[0]["code"], "difference_summary": "configuration A",
    })
    child_b = engineer.post(f"/families/{family['id']}/dashes", {
        "formal_name_cn": "CI演示子件B", "formal_name_en": "CI DEMO CHILD B",
        "object_level_code": levels[0]["code"], "difference_summary": "configuration B",
    })

    rule_a = f"CI-MODEL-A-{STAMP}"
    rule_b = f"CI-MODEL-B-{STAMP}"
    engineer.post("/applicability/rules", {"rule_code": rule_a, "name_cn": "CI A构型",
        "expression": {"field": "model", "op": "eq", "value": "A"}})
    engineer.post("/applicability/rules", {"rule_code": rule_b, "name_cn": "CI B构型",
        "expression": {"field": "model", "op": "eq", "value": "B"}})
    engineer.post("/configuration/contexts", {"context_code": f"CI-CTX-A-{STAMP}",
        "name_cn": "CI A上下文", "attributes": {"model": "A"}})
    line_a = engineer.post(f"/bom/{parent['full_part_number']}/lines", {
        "item_number": "010", "child_object_code": child_a["full_part_number"], "quantity": 1})
    line_b = engineer.post(f"/bom/{parent['full_part_number']}/lines", {
        "item_number": "010", "child_object_code": child_b["full_part_number"], "quantity": 1})
    engineer.put(f"/bom/lines/{line_a['id']}/applicability", {"rule_code": rule_a})
    engineer.put(f"/bom/lines/{line_b['id']}/applicability", {"rule_code": rule_b})
    resolved_a = engineer.post(f"/bom/{parent['full_part_number']}/resolve", {"attributes": {"model": "A"}})
    resolved_b = engineer.post(f"/bom/{parent['full_part_number']}/resolve", {"attributes": {"model": "B"}})
    check([x["child_object_code"] for x in resolved_a["lines"]] == [child_a["full_part_number"]], "A applicability resolution wrong")
    check([x["child_object_code"] for x in resolved_b["lines"]] == [child_b["full_part_number"]], "B applicability resolution wrong")
    cm.post(f"/bom/{parent['full_part_number']}/resolved-snapshot", {"attributes": {"model": "A"}})
    where = engineer.get(f"/where-used/{urllib.parse.quote(child_a['full_part_number'])}")
    check(where["working"], "where-used did not find shared master BOM")
    results.append("bom/shared-parent-pn-applicability-resolve-snapshot-where-used")

    exact = engineer.get(q("/search", q=parent["full_part_number"], limit=100))
    check(exact["results"] and exact["results"][0]["match_type"] == "EXACT", "exact P/N search failed")
    fragment = engineer.get(q("/search", q="CI演示", limit=100))
    check(fragment["total"] >= 3, "Chinese fragment search failed")
    results.append("search/exact-and-Chinese-fragment")

    sw_num = "UG-SW-CI-" + STAMP
    software = engineer.post("/software", {"software_number": sw_num, "name_cn": "CI测试软件", "software_type": "SOFTWARE"})
    version = engineer.post(f"/software/{sw_num}/versions", {"version": "1.0.0", "build": "ci", "hash_sha256": "a" * 64})
    approver.post(q(f"/software-versions/{version['id']}/release", comments="CI release"))
    results.append("software/create-version-release")

    namespaces = engineer.get("/dictionary/namespace")
    ext = engineer.post("/external-parts", {"namespace_code": namespaces[0]["code"],
        "external_part_number": "CI-EXT-" + STAMP, "name_cn": "CI外部件"})
    state = engineer.post(f"/external-parts/{urllib.parse.quote(ext['object_code'])}/states", {"supplier_revision": "A"})
    approver.post(q(f"/external-states/{state['id']}/accept", comments="CI accept"))
    results.append("external-part/create-state-accept")

    # Destructive row button is exercised last and must really remove the row.
    engineer.delete(f"/bom/lines/{line_b['id']}")
    after_delete = engineer.get(f"/bom/{parent['full_part_number']}")
    check(all(x["id"] != line_b["id"] for x in after_delete["lines"]), "BOM delete button contract failed")
    results.append("bom/delete-row")

    print("FULL FUNCTIONAL SMOKE PASS")
    for name in results:
        print("PASS", name)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FULL FUNCTIONAL SMOKE FAIL:", exc, file=sys.stderr)
        raise
