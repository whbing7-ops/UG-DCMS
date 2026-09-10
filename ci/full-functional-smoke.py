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

    def upload(self, path: str, filename: str, content: bytes, role="REFERENCE"):
        boundary = "----UGDCMS" + STAMP
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"role\"\r\n\r\n{role}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers={"Content-Type":f"multipart/form-data; boundary={boundary}","Authorization":"Bearer "+self.token}
        req=urllib.request.Request(BASE+path,data=body,headers=headers,method="POST")
        with urllib.request.urlopen(req,timeout=20) as response:
            return json.loads(response.read().decode())


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
    check("draft_write" in me["permissions"], "preloaded admin business write permission missing")
    check("baseline_release" in me["permissions"], "preloaded admin configuration permission missing")
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
    for path in ("/external-parts?page=1&page_size=100", "/software?page=1&page_size=100",
                 "/files?page=1&page_size=100", "/families?page=1&page_size=100"):
        paged = engineer.get(path)
        check(set(("items", "total", "page", "page_size")).issubset(paged),
              f"paged list contract incomplete: {path}")
        check(len(paged["items"]) <= 100 and paged["page"] == 1,
              f"paged list is not bounded: {path}")
    results.append("large-lists/server-pagination-bounded")
    backup_state = admin.get("/system/backups")
    check("schedule" in backup_state and "backups" in backup_state and "restore_pending" in backup_state
          and "restore_status" in backup_state,
          "backup management contract incomplete")
    restore_state = engineer.get("/system/restore/status")
    check(set(("state", "progress", "message")).issubset(restore_state),
          "restore progress contract incomplete")
    admin.put("/system/backup-schedule", {
        "enabled": False, "frequency": "DAILY", "hour": 2, "weekday": 6, "retention": 14,
    })
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
    candidates = engineer.get("/approvals/candidates")
    target = next(x for x in candidates if x["username"] == usernames["approver"])
    engineer.post(f"/families/{family['id']}/submit", {"approver_user_id": target["id"]})
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

    file_types=engineer.get("/dictionary/file-type")
    file_number="UG-CI-FILE-"+STAMP
    engineer.post("/files",{"file_number":file_number,"file_type_code":file_types[0]["code"],"title_cn":"CI检索附件文件"})
    rev=engineer.post(f"/files/{file_number}/revisions",{"change_summary":"CI initial"})
    engineer.upload(f"/revisions/{rev['id']}/attachments","CI检索证明.txt","唯一附件检索词UGATTACHMENT".encode("utf-8"))
    attachment_search=engineer.get(q("/search",q="UGATTACHMENT",kinds="ATTACHMENT"))
    check(any(x["kind"]=="ATTACHMENT" for x in attachment_search["results"]),"attachment content search failed")
    target=next(x for x in engineer.get("/approvals/candidates") if x["username"]==usernames["approver"])
    engineer.post(f"/revisions/{rev['id']}/submit",{"approver_user_id":target["id"]})
    approver.post(q(f"/revisions/{rev['id']}/release",comments="CI file release"))
    results.append("file/attachment-content-search-submit-release")

    sw_num = "UG-SW-CI-" + STAMP
    software = engineer.post("/software", {"software_number": sw_num, "name_cn": "CI测试软件", "software_type": "SOFTWARE"})
    version = engineer.post(f"/software/{sw_num}/versions", {"version": "1.0.0", "build": "ci", "hash_sha256": "a" * 64})
    engineer.post(f"/software-versions/{version['id']}/submit",{"approver_user_id":target["id"]})
    approver.post(q(f"/software-versions/{version['id']}/release", comments="CI release"))
    results.append("software/create-version-release")

    namespaces = engineer.get("/dictionary/namespace")
    ext = engineer.post("/external-parts", {"namespace_code": namespaces[0]["code"],
        "external_part_number": "CI-EXT-" + STAMP, "name_cn": "CI外部件",
        "external_class_code": "E08", "project_code": "CI-PROJECT-" + STAMP,
        "project_applicability": "CI 项目级准入测试",
        "project_evaluation_basis": "CI供应商规格及符合性资料"})
    state = engineer.post(f"/external-parts/{urllib.parse.quote(ext['object_code'])}/states", {"supplier_revision": "A"})
    engineer.post(f"/external-states/{state['id']}/submit",{"approver_user_id":target["id"]})
    approver.post(q(f"/external-states/{state['id']}/accept", comments="CI accept"))
    ext_detail=engineer.get(f"/external-parts/{urllib.parse.quote(ext['object_code'])}")
    control=ext_detail["project_controls"][0]
    engineer.post(f"/external-project-controls/{control['id']}/submit",{"approver_user_id":target["id"]})
    approver.post(f"/external-project-controls/{control['id']}/approve")
    results.append("external-part/classify-state-approve-project-control")

    baseline=engineer.post(f"/parts/{parent['full_part_number']}/baselines",{
        "project_code":"CI-PROJECT-"+STAMP,"baseline_type":"DESIGN",
        "reason":"CI正式基线","scope_note":"CI项目设计定义范围","copy_from_current":False})
    engineer.post(f"/baselines/{baseline['id']}/items",{"item_type":"FILE_REVISION",
        "target":file_number+" Rev.00","item_role":"PRIMARY_DEFINITION"})
    engineer.post(f"/baselines/{baseline['id']}/items",{"item_type":"EXTERNAL_TECHNICAL_STATE",
        "target":ext['object_code']+" TS1","item_role":"SUPPORTING_DEFINITION"})
    cm_target=next(x for x in engineer.get("/approvals/candidates?required_role=CONFIGURATION_MANAGER") if x["username"]==usernames["cm"])
    engineer.post(f"/baselines/{baseline['id']}/submit",{"approver_user_id":cm_target["id"]})
    cm.post(f"/baselines/{baseline['id']}/release")
    results.append("baseline/project-scope-validate-submit-release")

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
