"""校样（外发 proof）服务层自测：生成、CSV、冲突、依据变动前后的下载资格。"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db as dbmod  # noqa: E402
from app import services  # noqa: E402


def fresh_conn():
    conn = dbmod.connect(":memory:")
    dbmod.init_db(conn)
    return conn


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def parse_csv(data: bytes):
    text = data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def seed(conn):
    r = services.submit(conn, [
        {"action": "add_layer", "code": "P1"},
        {"action": "add_layer", "code": "P2"},
        {"action": "add_layer", "code": "P3"},
    ])
    assert r["accepted"]
    ids = {x["code"]: x["id"] for x in services.full_state(conn)["layers"]}
    r = services.submit(conn, [
        # P1 分离候选 [100,110] ∪ [200,210]
        {"action": "add_dating", "layer_id": ids["P1"],
         "lower_year": 100, "upper_year": 110, "note": "P1 测年 A"},
        {"action": "add_dating", "layer_id": ids["P1"],
         "lower_year": 200, "upper_year": 210, "note": "P1 测年 B"},
        # P2 [105,205]
        {"action": "add_dating", "layer_id": ids["P2"],
         "lower_year": 105, "upper_year": 205, "note": "P2 测年"},
        # P3 [210,300]
        {"action": "add_dating", "layer_id": ids["P3"],
         "lower_year": 210, "upper_year": 300, "note": "P3 测年"},
        {"action": "add_superposition",
         "earlier_layer_id": ids["P1"], "later_layer_id": ids["P2"],
         "note": "P2 叠压 P1"},
        {"action": "add_superposition",
         "earlier_layer_id": ids["P2"], "later_layer_id": ids["P3"],
         "note": "P3 叠压 P2"},
    ])
    assert r["accepted"]
    return ids


def ev_id(state, note_prefix):
    return [e for e in state["evidence"] if e["note"].startswith(note_prefix)][0]["id"]


def main():
    conn = fresh_conn()
    ids = seed(conn)

    print("[P1] 只用所选依据重算：登记不参与，未选依据不参与")
    state = services.full_state(conn)
    p1a = ev_id(state, "P1 测年 A")
    p1b = ev_id(state, "P1 测年 B")
    p2 = ev_id(state, "P2 测年")
    sup12 = [e for e in state["evidence"]
             if e["kind"] == "superposition" and e["layer_id"] == ids["P1"]][0]["id"]
    # 内部登记把 P1 限到 [100,110]；校样不应采用登记。
    r = services.submit(conn, [
        {"action": "add_registration", "layer_id": ids["P1"],
         "lower_year": 100, "upper_year": 105, "note": "内部拟采用"},
    ])
    check(r["accepted"], "内部登记入库（不应影响校样）")
    chrono_p1 = [x for x in r["chronology"]["layers"] if x["code"] == "P1"][0]
    check(chrono_p1["feasible_intervals"] == [[100, 105]], "内部编年表采用登记收窄")

    proof = services.create_proof(conn, ids["P1"], [p1a, p1b])
    check(proof["eligible"], "校样生成后可下载")
    rows = {row["层位编号"]: row for row in parse_csv(
        services.build_proof_csv(conn, proof["id"])[1])}
    check(list(rows) == ["P1"], f"仅列出所选依据涉及的层位（P1 及其测年）: {list(rows)}")
    check(rows["P1"]["可行年份区间"] == "100 ~ 110；200 ~ 210",
          f"仅用所选两条测年、保留空档，不采用登记: {rows['P1']['可行年份区间']}")

    print("[P2] 叠压依据：涉及的两个层位都须入选")
    try:
        services.create_proof(conn, ids["P1"], [p1a, p1b, sup12])
        raise AssertionError("应当拒绝")
    except services.ProofError as e:
        check(e.code == "superposition_layer_not_selected",
              f"所选叠压的另一方未入选时拒绝: {e.code} / {e.message}")
    proof2 = services.create_proof(conn, ids["P1"], [p1a, p1b, p2, sup12])
    check(proof2["eligible"], "补选 P2 测年后叠压可引用")
    rows = {row["层位编号"]: row for row in parse_csv(
        services.build_proof_csv(conn, proof2["id"])[1])}
    check(set(rows) == {"P1", "P2"}, f"列出外发层位与叠压另一方: {sorted(rows)}")
    check(rows["P1"]["可行年份区间"] == "100 ~ 110；200 ~ 204",
          f"严格早于按原规则收窄 P1 晚段: {rows['P1']['可行年份区间']}")
    check(rows["P2"]["可行年份区间"] == "105 ~ 205",
          f"P2 可行区间: {rows['P2']['可行年份区间']}")

    print("[P3] 所选依据无法同时成立：指出冲突并停止出件")
    # 新增 P5/P6：全库可行（P5 取早段），但只选 P5 的晚段 + P6 + 叠压时无解，
    # 这正是“只用所选依据重算”与全库结果的差异所在。
    r = services.submit(conn, [
        {"action": "add_layer", "code": "P5"},
        {"action": "add_layer", "code": "P6"},
        {"action": "add_dating", "layer_id": -2,
         "lower_year": 100, "upper_year": 150, "note": "P5 早段"},
        {"action": "add_dating", "layer_id": -2,
         "lower_year": 500, "upper_year": 510, "note": "P5 晚段"},
        {"action": "add_dating", "layer_id": -1,
         "lower_year": 300, "upper_year": 400, "note": "P6 测年"},
        {"action": "add_superposition",
         "earlier_layer_id": -2, "later_layer_id": -1, "note": "P6 叠压 P5"},
    ])
    check(r["accepted"], "P5/P6 依据整体成立（P5 早段可行）")
    st = services.full_state(conn)
    p5_late = [e for e in st["evidence"] if e["note"] == "P5 晚段"][0]["id"]
    p6 = [e for e in st["evidence"] if e["note"] == "P6 测年"][0]["id"]
    p56 = [e for e in st["evidence"] if e["note"] == "P6 叠压 P5"][0]["id"]
    n_before = len(services.proof_rows(conn))
    try:
        services.create_proof(conn, ids["P1"], [p5_late, p6, p56])
        raise AssertionError("应当抛出 ProofError")
    except services.ProofError as e:
        check(e.status == 409 and e.code == "unsatisfiable", "校样冲突返回 409")
        check(e.detail.get("conflict", {}).get("layer_code"),
              f"指出冲突层位: {e.detail.get('conflict', {}).get('layer_code')}")
        check(e.detail["conflict"].get("evidence"),
              "列出冲突涉及的依据")
    check(len(services.proof_rows(conn)) == n_before, "冲突时未生成校样留档")

    print("[P4] 依据更正/撤回 -> 校样失效；其它依据与登记的改动不影响")
    # 重新生成一份干净校样（P1 只用测年 A/B）
    proofx = services.create_proof(conn, ids["P1"], [p1a, p1b])
    check(services.proof_rows(conn)[0]["eligible"], "初始可下载")

    # 改动无关依据：P3 测年
    p3 = ev_id(services.full_state(conn), "P3 测年")
    r = services.submit(conn, [
        {"action": "correct_dating", "evidence_id": p3,
         "lower_year": 250, "upper_year": 350, "note": "P3 测年更正"},
    ])
    check(r["accepted"], "无关依据更正通过")
    row = [p for p in services.proof_rows(conn) if p["id"] == proofx["id"]][0]
    check(row["eligible"], "未选依据的更正不影响下载资格")
    services.build_proof_csv(conn, proofx["id"])  # 不抛异常即合格

    # 改动登记：更换 P1 的内部登记（内部约束变化）
    p1_reg = [x for x in services.full_state(conn)["registrations"]
              if x["status"] == "active" and x["layer_id"] == ids["P1"]][0]["id"]
    r = services.submit(conn, [
        {"action": "replace_registration", "registration_id": p1_reg,
         "lower_year": 200, "upper_year": 210, "note": "内部改用晚段登记"},
    ])
    check(r["accepted"], "登记改动通过")
    row = [p for p in services.proof_rows(conn) if p["id"] == proofx["id"]][0]
    check(row["eligible"], "登记改动不影响校样资格")
    rows = {x["层位编号"]: x for x in parse_csv(
        services.build_proof_csv(conn, proofx["id"])[1])}
    check(rows["P1"]["可行年份区间"] == "100 ~ 110；200 ~ 210",
          "登记改动后校样仍按生成时所选依据出件")

    # 更正所选依据 p1a 为 [101,111]
    r = services.submit(conn, [
        {"action": "correct_dating", "evidence_id": p1a,
         "lower_year": 101, "upper_year": 111, "note": "P1 测年 A 更正"},
    ])
    check(r["accepted"], "所选依据更正通过")
    row = [p for p in services.proof_rows(conn) if p["id"] == proofx["id"]][0]
    check(not row["eligible"], "所选依据更正后校样失格")
    check(row["invalid_evidence"] and row["invalid_evidence"][0]["id"] == p1a,
          f"列出失效依据: {row['invalid_evidence']}")
    try:
        services.build_proof_csv(conn, proofx["id"])
        raise AssertionError("应当拒绝下载")
    except services.ProofError as e:
        check(e.status == 409 and e.code == "proof_ineligible", "下载被拒（409）")

    print("[P5] 更正后又改回原值：仍不可下载（版本号已前进）")
    r = services.submit(conn, [
        {"action": "correct_dating", "evidence_id": p1a,
         "lower_year": 100, "upper_year": 110, "note": "P1 测年 A 改回"},
    ])
    check(r["accepted"], "改回原值通过")
    row = [p for p in services.proof_rows(conn) if p["id"] == proofx["id"]][0]
    check(not row["eligible"], "改回原值后仍失格")
    try:
        services.build_proof_csv(conn, proofx["id"])
        raise AssertionError("应当拒绝下载")
    except services.ProofError:
        check(True, "改回原值后下载仍被拒")

    print("[P6] 撤回所选依据 -> 失格；重新生成的新校样可下载")
    # 当前 P1 内部登记已改为晚段 [200,210]，先撤回它，避免撤回 p1b 后无解。
    act_reg = [x for x in services.full_state(conn)["registrations"]
               if x["status"] == "active" and x["layer_id"] == ids["P1"]][0]["id"]
    r = services.submit(conn, [
        {"action": "withdraw_registration", "registration_id": act_reg},
    ])
    check(r["accepted"], "撤回内部登记通过（不影响校样）")
    r = services.submit(conn, [
        {"action": "withdraw_evidence", "evidence_id": p1b},
    ])
    check(r["accepted"], "撤回所选依据通过")
    row = [p for p in services.proof_rows(conn) if p["id"] == proofx["id"]][0]
    check(not row["eligible"], "撤回后校样失格")
    proof_new = services.create_proof(conn, ids["P1"], [p1a])
    check(proof_new["eligible"], "用当前有效依据重新生成的校样可下载")
    rows = {x["层位编号"]: x for x in parse_csv(
        services.build_proof_csv(conn, proof_new["id"])[1])}
    check(rows["P1"]["可行年份区间"] == "100 ~ 110", f"新校样区间: {rows['P1']['可行年份区间']}")

    print("[P7] 已撤回依据不可再被选入新校样")
    try:
        services.create_proof(conn, ids["P1"], [p1b])
        raise AssertionError("应当拒绝")
    except services.ProofError as e:
        check(e.status == 422 and e.code == "evidence_inactive", "撤回依据被拒选")

    print("[P8] 未知校样/未知层位")
    try:
        services.build_proof_csv(conn, 99999)
        raise AssertionError("应当拒绝")
    except services.ProofError as e:
        check(e.status == 404, "未知校样 404")
    try:
        services.create_proof(conn, 99999, [p1a])
        raise AssertionError("应当拒绝")
    except services.ProofError as e:
        check(e.status == 422 and e.code == "unknown_layer", "未知外发层位被拒")

    print("\n校样服务层测试全部通过。")


if __name__ == "__main__":
    main()
