"""服务层端到端测试（标准库 + 内存 SQLite）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db as dbmod  # noqa: E402
from app import services  # noqa: E402


def fresh_conn():
    conn = dbmod.connect(":memory:")
    dbmod.init_db(conn)
    return conn


def intervals_of(chrono, code):
    for row in chrono["layers"]:
        if row["code"] == code:
            return row["feasible_intervals"], row
    raise KeyError(code)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def main():
    conn = fresh_conn()

    print("[1] 导入三层位：测年含分离区间 + 严格早于")
    r = services.submit(conn, [
        {"action": "add_layer", "code": "L1", "name": "下层"},
        {"action": "add_layer", "code": "L2", "name": "中层"},
        {"action": "add_layer", "code": "L3", "name": "上层"},
    ])
    check(r["accepted"], "首批层位入库")
    ids = {row["code"]: row["id"] for row in services.full_state(conn)["layers"]}

    r = services.submit(conn, [
        # L1 两段分离候选：[100,110] 与 [200,210]
        {"action": "add_dating", "layer_id": ids["L1"],
         "lower_year": 100, "upper_year": 110, "note": "碳样A"},
        {"action": "add_dating", "layer_id": ids["L1"],
         "lower_year": 200, "upper_year": 210, "note": "碳样A复测"},
        # L2 单段 [105, 205]
        {"action": "add_dating", "layer_id": ids["L2"],
         "lower_year": 105, "upper_year": 205, "note": "陶片编年"},
        # L3 [210, 300]
        {"action": "add_dating", "layer_id": ids["L3"],
         "lower_year": 210, "upper_year": 300, "note": "铜钱"},
        {"action": "add_superposition",
         "earlier_layer_id": ids["L1"], "later_layer_id": ids["L2"],
         "note": "H2 叠压 H1"},
        {"action": "add_superposition",
         "earlier_layer_id": ids["L2"], "later_layer_id": ids["L3"],
         "note": "H3 叠压 H2"},
    ])
    check(r["accepted"], "依据全部入库且同时成立")
    chrono = r["chronology"]
    iv1, row1 = intervals_of(chrono, "L1")
    check(row1["gap_count"] == 1, f"L1 保留 1 个空档，实际区间 {iv1}")
    check(iv1 == [[100, 110], [200, 204]], f"L1 可行年份为两段且第二段被 L2 约束收窄: {iv1}")
    iv2, _ = intervals_of(chrono, "L2")
    # 105 有 L1=104 支撑（L1 第一段内），故下界保持 105
    check(iv2 == [[105, 205]], f"L2 可行区间 {iv2}")
    iv3, _ = intervals_of(chrono, "L3")
    check(iv3 == [[210, 300]], f"L3 可行区间 {iv3}")

    print("[2] 登记拟采用区间（额外约束），可行域进一步收窄")
    r = services.submit(conn, [
        {"action": "add_registration", "layer_id": ids["L1"],
         "lower_year": 90, "upper_year": 150, "note": "拟采用早段"},
    ])
    check(r["accepted"], "登记提交成功")
    iv1, _ = intervals_of(r["chronology"], "L1")
    check(iv1 == [[100, 110]], f"登记与测年区间取交，晚段被排除: {iv1}")
    reg_id = services.full_state(conn)["registrations"][0]["id"]

    print("[3] 更换登记")
    r = services.submit(conn, [
        {"action": "replace_registration", "registration_id": reg_id,
         "lower_year": 195, "upper_year": 250, "note": "改用晚段"},
    ])
    check(r["accepted"], "更换登记成功")
    state = services.full_state(conn)
    statuses = [(x["id"], x["status"]) for x in state["registrations"]]
    check(any(s == "replaced" for _, s in statuses)
          and any(s == "active" for _, s in statuses), f"旧登记 replaced、新登记 active: {statuses}")
    iv1, _ = intervals_of(r["chronology"], "L1")
    check(iv1 == [[200, 204]], f"更换后 L1 只剩晚段可行: {iv1}")
    new_reg = [x for x in services.full_state(conn)["registrations"]
               if x["status"] == "active"][0]["id"]

    print("[4] 制造无解：收紧 L1 上界，使 L1<L2<L3 链断裂，提交应驳回且原表不变")
    before = services.current_chronology(conn)
    # L1 被限到 <=105，则 L2<=106、L3<=107，但 L3 测年自 210 起 -> 无解
    r = services.submit(conn, [
        {"action": "add_registration", "layer_id": ids["L1"],
         "lower_year": 100, "upper_year": 105, "note": "错误收紧"},
    ])
    check(not r["accepted"], "冲突提交被驳回")
    check(r["reason"] == "unsatisfiable", "驳回原因 unsatisfiable")
    check(r["conflict"]["layer_id"] in (ids["L1"], ids["L2"], ids["L3"]),
          f"指出冲突层位: {r['conflict'].get('layer_code')}")
    check(len(r["conflict"]["evidence_ids"]) >= 1, "列出相关依据 id")
    after = services.current_chronology(conn)
    check(after == before, "回滚后原编年表照常可查且不变")
    subs = services.full_state(conn)["submissions"]
    check(subs[0]["accepted"] is False, "驳回提交仍写入流水")

    print("[5] 撤回登记后恢复可行")
    r = services.submit(conn, [
        {"action": "withdraw_registration", "registration_id": new_reg},
    ])
    check(r["accepted"], "撤回登记成功")
    iv1, _ = intervals_of(r["chronology"], "L1")
    check(iv1 == [[100, 110], [200, 204]], f"撤回后恢复两段可行域: {iv1}")

    print("[6] 更正测年并联动撤回失效登记（withdraw_with 原子提交）")
    # 先加一个会因更正而失效的登记（与更正后的测年区间不相交）
    r = services.submit(conn, [
        {"action": "add_registration", "layer_id": ids["L2"],
         "lower_year": 150, "upper_year": 180, "note": "L2 拟采用"},
    ])
    check(r["accepted"], "L2 登记成功")
    l2_reg = [x for x in services.full_state(conn)["registrations"]
              if x["status"] == "active" and x["layer_id"] == ids["L2"]][0]["id"]
    l2_dating = [e for e in services.full_state(conn)["evidence"]
                 if e["kind"] == "datum" and e["layer_id"] == ids["L2"]][0]["id"]
    # L2 测年更正为 [120,140]：若保留 150-180 登记，取交为空，整批无解；
    # withdraw_with 同时撤回该登记，更正即可成立。
    r = services.submit(conn, [
        {"action": "withdraw_with", "evidence_id": l2_dating,
         "registration_ids": [l2_reg],
         "lower_year": 120, "upper_year": 140,
         "note": "复测更正 L2"},
    ])
    check(r["accepted"], "更正+联动撤回在同一提交内成立")
    state = services.full_state(conn)
    updated = [e for e in state["evidence"] if e["id"] == l2_dating][0]
    check((updated["lower_year"], updated["upper_year"]) == (120, 140),
          f"L2 测年已更正为 [120,140]: {updated['lower_year']}-{updated['upper_year']}")
    reg_row = [x for x in state["registrations"] if x["id"] == l2_reg][0]
    check(reg_row["status"] == "withdrawn", f"失效登记已联动撤回: {reg_row['status']}")
    iv2, _ = intervals_of(r["chronology"], "L2")
    check(iv2 == [[120, 140]], f"L2 编年按更正后测年计算: {iv2}")
    iv1, _ = intervals_of(r["chronology"], "L1")
    check(iv1 == [[100, 110]], f"L1 晚段因 L1<L2(<=140) 被剪: {iv1}")

    print("[7] 反向验证：不联动撤回则同批更正必然被驳回")
    # 新登记与现行测年相容
    r = services.submit(conn, [
        {"action": "add_registration", "layer_id": ids["L2"],
         "lower_year": 125, "upper_year": 130, "note": "新 L2 登记"},
    ])
    check(r["accepted"], "新登记成立")
    # 改回早段测年，与登记 [125,130] 不相交 -> 不撤回登记必然无解
    r = services.submit(conn, [
        {"action": "correct_dating", "evidence_id": l2_dating,
         "lower_year": 105, "upper_year": 115, "note": "又改回早段"},
    ])
    check(not r["accepted"], "与现行登记冲突的更正被驳回")
    check(r["conflict"] is not None, "驳回附带冲突诊断")
    # 原状态未受影响
    state = services.full_state(conn)
    updated = [e for e in state["evidence"] if e["id"] == l2_dating][0]
    check((updated["lower_year"], updated["upper_year"]) == (120, 140),
          "驳回后测年仍是更正前的 [120,140]")

    print("[8] 叠压环检测与撤回依据")
    r = services.submit(conn, [
        {"action": "add_superposition",
         "earlier_layer_id": ids["L3"], "later_layer_id": ids["L1"],
         "note": "错误叠压记录"},
    ])
    check(not r["accepted"] and "有向环" in r["conflict"]["reason"],
          f"有向环被拒绝: {r['conflict'].get('cycle_layer_codes')}")
    check(r["conflict"]["cycle_evidence"], "环涉及依据 id 已列出")

    print("[9] 单批内临时引用（先建层位再挂依据，临时 id 从 -1 起）")
    r = services.submit(conn, [
        {"action": "add_layer", "code": "L4", "name": "新探方层位"},
        {"action": "add_dating", "layer_id": -1,
         "lower_year": 50, "upper_year": 90, "note": "L4 测年"},
        {"action": "add_superposition",
         "earlier_layer_id": -1, "later_layer_id": ids["L1"],
         "note": "L4 早于 L1"},
    ])
    check(r["accepted"], "临时 id 引用在同一提交内解析成功")
    iv4, row4 = intervals_of(r["chronology"], "L4")
    check(iv4 and iv4[0][0] == 50, f"L4 编年已生成: {iv4}")

    print("[10] 校验类错误回滚（422 语义）")
    try:
        services.submit(conn, [
            {"action": "add_dating", "layer_id": 99999,
             "lower_year": 1, "upper_year": 2},
        ])
        raise AssertionError("应当抛 ValidationError")
    except services.ValidationError as e:
        check(e.code == "unknown_layer", f"未知层位被拦截: {e.code}")
    try:
        services.submit(conn, [
            {"action": "add_layer", "code": "L1"},
        ])
        raise AssertionError("应当抛 ValidationError")
    except services.ValidationError as e:
        check(e.code == "duplicate_code", f"重复编号被拦截: {e.code}")
    try:
        services.submit(conn, [
            {"action": "add_dating", "layer_id": ids["L1"],
             "lower_year": 100, "upper_year": 50},
        ])
        raise AssertionError("应当抛 ValidationError")
    except services.ValidationError as e:
        check(e.code == "bad_interval", f"倒挂区间被拦截: {e.code}")

    print("\n全部服务层测试通过。")


if __name__ == "__main__":
    main()
