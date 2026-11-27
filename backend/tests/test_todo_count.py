"""待办数：电脑版侧栏、工作台按 by_kind 在对应功能上标数字（只算待办事项，不含通知）。"""
from .helpers import make_req


def test_todo_count_by_kind(org, ref):
    assert org.ok(org.get("lead_a", "/api/todos/count")) == {"tasks": 0, "notices": 0, "by_kind": {}}

    req = make_req(org, ref, who="a1")
    c = org.ok(org.get("lead_a", "/api/todos/count"))
    assert c["by_kind"] == {"APPROVE": 1} and c["tasks"] == 1

    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    c = org.ok(org.get("lead_a", "/api/todos/count"))
    assert c["by_kind"].get("APPROVE", 0) == 0

    # 申请人收到的「已通过」是通知：计入 notices，不进 by_kind
    mine = org.ok(org.get("a1", "/api/todos/count"))
    notices = [t for t in org.ok(org.get("a1", "/api/todos")) if t["is_notice"]]
    assert mine["notices"] == len(notices) >= 1
    assert "NOTICE" not in mine["by_kind"] and mine["tasks"] == sum(mine["by_kind"].values())
