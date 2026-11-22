"""业务服务层（仅标准库，FastAPI 之外也可独立调用/测试）。

一次提交（submission）携带一批动作，在单个 SQLite 事务内：
  1. 顺序应用全部动作并做引用/范围校验（任一失败 -> 整批回滚）；
  2. 用应用后的“最新入库状态”装配 CSP 并求解，核对全部依据与登记能否同时成立；
  3. 无解 -> 整批回滚，返回冲突层位与依据，原编年表照常可查；
     有解 -> 提交，返回每层可行年份区间（保留空档）。
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import db as dbmod
from . import solver

VALID_ACTIONS = {
    "add_layer", "add_dating", "add_superposition",
    "correct_dating", "correct_superposition",
    "withdraw_evidence",
    "add_registration", "replace_registration", "withdraw_registration",
    "withdraw_with",
}

# 校样资格：这些状态的原因说明
PROOF_INACTIVE_REASON = "所引依据已更正或撤回（或曾被更正后又改回原值），校样作废"


class ProofError(Exception):
    """校样类错误（HTTP 层映射为 409/404/422）。"""

    def __init__(self, status: int, code: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        d.update(self.detail)
        return d


class ValidationError(Exception):
    """输入/引用类错误（HTTP 层映射为 422）。"""

    def __init__(self, code: str, message: str, index: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.index = index

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.index is not None:
            d["action_index"] = self.index
        return d


# --------------------------------------------------------------------------- #
# 事务内的可变快照（提交成功前不落库为最终状态）
# --------------------------------------------------------------------------- #
@dataclass
class LayerRec:
    id: int
    code: str
    name: str


@dataclass
class EvidenceRec:
    id: int
    kind: str                      # 'datum' | 'superposition'
    layer_id: int
    layer2_id: Optional[int]
    lower_year: Optional[int]
    upper_year: Optional[int]
    note: str
    active: bool = True
    version: int = 1               # 每次更正/撤回 +1（校样资格核对用）


@dataclass
class RegistrationRec:
    id: int
    layer_id: int
    lower_year: int
    upper_year: int
    note: str
    status: str = "active"         # active | withdrawn | replaced
    replaced_by: Optional[int] = None


@dataclass
class Snapshot:
    layers: Dict[int, LayerRec] = field(default_factory=dict)
    evidence: Dict[int, EvidenceRec] = field(default_factory=dict)
    registrations: Dict[int, RegistrationRec] = field(default_factory=dict)
    # 本批新建对象的临时负 id（从 -1 起）-> 落库真实 id
    layer_rid: Dict[int, int] = field(default_factory=dict)
    evidence_rid: Dict[int, int] = field(default_factory=dict)
    reg_rid: Dict[int, int] = field(default_factory=dict)
    tmp_layer_seq: int = 0
    tmp_evidence_seq: int = 0
    tmp_reg_seq: int = 0

    def next_layer_tmp(self) -> int:
        self.tmp_layer_seq -= 1
        return self.tmp_layer_seq

    def next_evidence_tmp(self) -> int:
        self.tmp_evidence_seq -= 1
        return self.tmp_evidence_seq

    def next_reg_tmp(self) -> int:
        self.tmp_reg_seq -= 1
        return self.tmp_reg_seq

    def resolve_layer(self, ref: Any, index: int) -> int:
        try:
            ref = int(ref)
        except (TypeError, ValueError):
            raise ValidationError("bad_layer_ref", f"层位引用非法: {ref!r}", index)
        if ref < 0:
            if ref not in self.layer_rid:
                raise ValidationError("unknown_layer", f"引用了本批未登记的临时层位 {ref}", index)
            return self.layer_rid[ref]
        if ref not in self.layers:
            raise ValidationError("unknown_layer", f"层位 {ref} 不存在", index)
        return ref

    def resolve_evidence(self, ref: Any, index: int) -> int:
        try:
            ref = int(ref)
        except (TypeError, ValueError):
            raise ValidationError("bad_evidence_ref", f"依据引用非法: {ref!r}", index)
        if ref < 0:
            if ref not in self.evidence_rid:
                raise ValidationError("unknown_evidence", f"引用了本批未登记的临时依据 {ref}", index)
            return self.evidence_rid[ref]
        if ref not in self.evidence:
            raise ValidationError("unknown_evidence", f"依据 {ref} 不存在", index)
        return ref

    def resolve_reg(self, ref: Any, index: int) -> int:
        try:
            ref = int(ref)
        except (TypeError, ValueError):
            raise ValidationError("bad_registration_ref", f"登记引用非法: {ref!r}", index)
        if ref < 0:
            if ref not in self.reg_rid:
                raise ValidationError("unknown_registration", f"引用了本批未登记的临时登记 {ref}", index)
            return self.reg_rid[ref]
        if ref not in self.registrations:
            raise ValidationError("unknown_registration", f"登记 {ref} 不存在", index)
        return ref


def load_snapshot(conn: sqlite3.Connection) -> Snapshot:
    snap = Snapshot()
    for row in dbmod.list_layers(conn):
        snap.layers[row["id"]] = LayerRec(row["id"], row["code"], row["name"])
    for row in conn.execute("SELECT * FROM evidence").fetchall():
        snap.evidence[row["id"]] = EvidenceRec(
            row["id"], row["kind"], row["layer_id"], row["layer2_id"],
            row["lower_year"], row["upper_year"], row["note"],
            active=bool(row["active"]), version=row["version"],
        )
    for row in conn.execute("SELECT * FROM registration").fetchall():
        snap.registrations[row["id"]] = RegistrationRec(
            row["id"], row["layer_id"], row["lower_year"], row["upper_year"],
            row["note"], status=row["status"], replaced_by=row["replaced_by"],
        )
    return snap


# --------------------------------------------------------------------------- #
# 动作应用（仅改快照；最后统一落库）
# --------------------------------------------------------------------------- #
def _require_fields(act: dict, fields: Tuple[str, ...], index: int) -> None:
    for f in fields:
        if f not in act or act[f] is None or act[f] == "":
            raise ValidationError("missing_field", f"动作缺少字段 {f}", index)


def _year(v: Any, field_name: str, index: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValidationError("bad_year", f"{field_name} 必须是整数年: {v!r}", index)
    if not (solver.YEAR_MIN <= v <= solver.YEAR_MAX):
        raise ValidationError(
            "year_out_of_range",
            f"{field_name}={v} 超出允许范围 [{solver.YEAR_MIN}, {solver.YEAR_MAX}]",
            index,
        )
    return v


def _closed_range(act: dict, index: int) -> Tuple[int, int]:
    lo = _year(act.get("lower_year"), "lower_year", index)
    hi = _year(act.get("upper_year"), "upper_year", index)
    if lo > hi:
        raise ValidationError("bad_interval", f"闭区间下界 {lo} 大于上界 {hi}", index)
    return lo, hi


def apply_action(snap: Snapshot, act: dict, index: int) -> None:
    if not isinstance(act, dict):
        raise ValidationError("bad_action", "动作必须是对象", index)
    kind = act.get("action")
    if kind not in VALID_ACTIONS:
        raise ValidationError("unknown_action", f"未知动作 {kind!r}", index)
    note = str(act.get("note", ""))

    if kind == "add_layer":
        _require_fields(act, ("code",), index)
        code = str(act["code"]).strip()
        if not code:
            raise ValidationError("bad_code", "层位编号不能为空", index)
        if any(l.code == code for l in snap.layers.values()):
            raise ValidationError("duplicate_code", f"层位编号 {code} 已存在", index)
        tmp = snap.next_layer_tmp()
        snap.layers[tmp] = LayerRec(tmp, code, str(act.get("name", "")))
        snap.layer_rid[tmp] = tmp  # 临时键先指向自身，落库时再改写为真实 id
        act["_tmp_layer"] = tmp
        return

    if kind == "add_dating":
        _require_fields(act, ("layer_id",), index)
        lid = snap.resolve_layer(act["layer_id"], index)
        lo, hi = _closed_range(act, index)
        tmp = snap.next_evidence_tmp()
        snap.evidence[tmp] = EvidenceRec(tmp, "datum", lid, None, lo, hi, note)
        snap.evidence_rid[tmp] = tmp
        act["_tmp_evidence"] = tmp
        return

    if kind == "add_superposition":
        _require_fields(act, ("earlier_layer_id", "later_layer_id"), index)
        earlier = snap.resolve_layer(act["earlier_layer_id"], index)
        later = snap.resolve_layer(act["later_layer_id"], index)
        if earlier == later:
            raise ValidationError("self_relation", "严格早于关系的两端不能是同一层位", index)
        tmp = snap.next_evidence_tmp()
        snap.evidence[tmp] = EvidenceRec(
            tmp, "superposition", earlier, later, None, None, note
        )
        snap.evidence_rid[tmp] = tmp
        act["_tmp_evidence"] = tmp
        return

    if kind == "correct_dating":
        _require_fields(act, ("evidence_id",), index)
        eid = snap.resolve_evidence(act["evidence_id"], index)
        ev = snap.evidence[eid]
        if ev.kind != "datum":
            raise ValidationError("wrong_kind", f"依据 {eid} 不是测年依据，无法更正测年", index)
        lo, hi = _closed_range(act, index)
        ev.lower_year, ev.upper_year, ev.note = lo, hi, note or ev.note
        ev.active = True
        return

    if kind == "correct_superposition":
        _require_fields(act, ("evidence_id", "earlier_layer_id", "later_layer_id"), index)
        eid = snap.resolve_evidence(act["evidence_id"], index)
        ev = snap.evidence[eid]
        if ev.kind != "superposition":
            raise ValidationError("wrong_kind", f"依据 {eid} 不是叠压依据", index)
        earlier = snap.resolve_layer(act["earlier_layer_id"], index)
        later = snap.resolve_layer(act["later_layer_id"], index)
        if earlier == later:
            raise ValidationError("self_relation", "严格早于关系的两端不能是同一层位", index)
        ev.layer_id, ev.layer2_id = earlier, later
        ev.note = note or ev.note
        ev.active = True
        return

    if kind == "withdraw_evidence":
        _require_fields(act, ("evidence_id",), index)
        eid = snap.resolve_evidence(act["evidence_id"], index)
        snap.evidence[eid].active = False
        return

    if kind == "add_registration":
        _require_fields(act, ("layer_id",), index)
        lid = snap.resolve_layer(act["layer_id"], index)
        lo, hi = _closed_range(act, index)
        tmp = snap.next_reg_tmp()
        snap.registrations[tmp] = RegistrationRec(tmp, lid, lo, hi, note)
        snap.reg_rid[tmp] = tmp
        act["_tmp_reg"] = tmp
        return

    if kind == "replace_registration":
        _require_fields(act, ("registration_id",), index)
        rid = snap.resolve_reg(act["registration_id"], index)
        old = snap.registrations[rid]
        if old.status != "active":
            raise ValidationError(
                "registration_not_active", f"登记 {rid} 已非有效状态，不能更换", index
            )
        lo, hi = _closed_range(act, index)
        new_tmp = snap.next_reg_tmp()
        snap.registrations[new_tmp] = RegistrationRec(
            new_tmp, old.layer_id, lo, hi, note or old.note
        )
        snap.reg_rid[new_tmp] = new_tmp
        old.status = "replaced"
        old.replaced_by = new_tmp
        act["_tmp_reg"] = new_tmp
        return

    if kind == "withdraw_registration":
        _require_fields(act, ("registration_id",), index)
        rid = snap.resolve_reg(act["registration_id"], index)
        reg = snap.registrations[rid]
        if reg.status != "active":
            raise ValidationError(
                "registration_not_active", f"登记 {rid} 已非有效状态，不能撤回", index
            )
        reg.status = "withdrawn"
        return

    if kind == "withdraw_with":
        # 更正测年/叠压记录时一并撤回失效登记：
        # evidence_id 为要更正或撤回的依据；registration_ids 为同时失效的登记。
        _require_fields(act, ("evidence_id",), index)
        eid = snap.resolve_evidence(act["evidence_id"], index)
        reg_refs = act.get("registration_ids", [])
        if not isinstance(reg_refs, list):
            raise ValidationError("bad_registration_list", "registration_ids 必须是数组", index)
        for ref in reg_refs:
            rid = snap.resolve_reg(ref, index)
            if snap.registrations[rid].status == "active":
                snap.registrations[rid].status = "withdrawn"
        if act.get("withdraw_evidence", False):
            snap.evidence[eid].active = False
        elif "lower_year" in act and "upper_year" in act:
            # 更正测年区间
            ev = snap.evidence[eid]
            if ev.kind != "datum":
                raise ValidationError("wrong_kind", f"依据 {eid} 不是测年依据", index)
            lo, hi = _closed_range(act, index)
            ev.lower_year, ev.upper_year = lo, hi
            ev.active = True
        elif "earlier_layer_id" in act and "later_layer_id" in act:
            ev = snap.evidence[eid]
            if ev.kind != "superposition":
                raise ValidationError("wrong_kind", f"依据 {eid} 不是叠压依据", index)
            earlier = snap.resolve_layer(act["earlier_layer_id"], index)
            later = snap.resolve_layer(act["later_layer_id"], index)
            if earlier == later:
                raise ValidationError("self_relation", "严格早于关系的两端不能是同一层位", index)
            ev.layer_id, ev.layer2_id = earlier, later
            ev.active = True
        return


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #
def persist(conn: sqlite3.Connection, snap: Snapshot, actions: List[dict]) -> None:
    """把快照相对当前库的变化写入。临时对象插入，被撤回/更换对象更新。"""
    # 已存在层位的 id 即真实 id，先放入映射，临时层位插入后再补。
    for lid, rec in snap.layers.items():
        if lid > 0:
            snap.layer_rid[lid] = lid

    def real_layer(ref: int) -> int:
        return snap.layer_rid.get(ref, ref)

    # 1. 层位
    for act in actions:
        if act.get("action") == "add_layer":
            tmp = act["_tmp_layer"]
            rec = snap.layers[tmp]
            real = dbmod.insert_layer(conn, rec.code, rec.name)
            snap.layer_rid[tmp] = real

    # 2. 依据
    for act in actions:
        tmp = act.get("_tmp_evidence")
        if tmp is not None:
            rec = snap.evidence[tmp]
            lid = real_layer(rec.layer_id)
            if rec.kind == "datum":
                real = dbmod.insert_dating(conn, lid, rec.lower_year, rec.upper_year, rec.note)
            else:
                l2 = rec.layer2_id
                if l2 is not None:
                    l2 = real_layer(l2)
                real = dbmod.insert_superposition(conn, lid, l2, rec.note)
            snap.evidence_rid[tmp] = real

    # 3. 登记
    for act in actions:
        tmp = act.get("_tmp_reg")
        if tmp is not None:
            rec = snap.registrations[tmp]
            lid = real_layer(rec.layer_id)
            real = dbmod.insert_registration(conn, lid, rec.lower_year, rec.upper_year, rec.note)
            snap.reg_rid[tmp] = real

    # 4. 对已存在对象的状态更新（撤回/更换/更正）
    for eid, rec in snap.evidence.items():
        if eid < 0:
            continue
        row = dbmod.get_evidence(conn, eid)
        if row is None:
            continue
        if bool(row["active"]) != rec.active:
            if not rec.active:
                dbmod.deactivate_evidence(conn, eid)
            else:
                conn.execute(
                    "UPDATE evidence SET active=1, version=version+1, "
                    "withdrawn_at=NULL, lower_year=?, upper_year=?, layer_id=?, "
                    "layer2_id=?, note=? WHERE id=?",
                    (rec.lower_year, rec.upper_year, rec.layer_id, rec.layer2_id, rec.note, eid),
                )
        elif (row["lower_year"], row["upper_year"], row["layer_id"],
              row["layer2_id"], row["note"]) != (
            rec.lower_year, rec.upper_year, rec.layer_id, rec.layer2_id, rec.note
        ):
            conn.execute(
                "UPDATE evidence SET lower_year=?, upper_year=?, layer_id=?, "
                "layer2_id=?, note=?, version=version+1 WHERE id=?",
                (rec.lower_year, rec.upper_year, rec.layer_id, rec.layer2_id, rec.note, eid),
            )

    for rid, rec in snap.registrations.items():
        if rid < 0:
            continue
        row = dbmod.get_registration(conn, rid)
        if row is None or row["status"] == rec.status:
            continue
        replaced_real = None
        if rec.replaced_by is not None and rec.replaced_by < 0:
            replaced_real = snap.reg_rid.get(rec.replaced_by)
        dbmod.mark_registration(conn, rid, rec.status, replaced_real)


# --------------------------------------------------------------------------- #
# CSP 装配与求解
# --------------------------------------------------------------------------- #
def build_and_solve(snap: Snapshot) -> dict:
    layer_ids = sorted(snap.layers)
    full_domain: solver.Domain = [(solver.YEAR_MIN, solver.YEAR_MAX)]
    # 测年依据：同一层位的多个候选闭区间是“分开的候选年代”，取并集（保留空档）。
    datum_pieces: Dict[int, List[Tuple[int, int]]] = {lid: [] for lid in layer_ids}
    # 登记：拟采用区间是额外约束，对候选域取交集。
    reg_pieces: Dict[int, List[Tuple[int, int]]] = {lid: [] for lid in layer_ids}
    constrained: set = set()
    edges: List[Tuple[int, int, int]] = []

    for eid, ev in snap.evidence.items():
        if not ev.active:
            continue
        if ev.kind == "datum":
            constrained.add(ev.layer_id)
            datum_pieces[ev.layer_id].append((ev.lower_year, ev.upper_year))
        else:
            edges.append((ev.layer_id, ev.layer2_id, eid))

    for rid, reg in snap.registrations.items():
        if reg.status != "active":
            continue
        constrained.add(reg.layer_id)
        reg_pieces[reg.layer_id].append((reg.lower_year, reg.upper_year))

    raw_domains: Dict[int, solver.Domain] = {}
    for lid in layer_ids:
        if datum_pieces[lid]:
            domain = solver.normalize(datum_pieces[lid])      # 候选区间并集
        else:
            domain = list(full_domain)                        # 无测年依据：有效年份全域
        for lo, hi in reg_pieces[lid]:                        # 登记逐条收窄
            domain = solver.intersect(domain, solver.normalize([(lo, hi)]))
        raw_domains[lid] = domain

    result = solver.solve(layer_ids, raw_domains, edges)
    result["constrained_layer_ids"] = sorted(constrained)
    return result


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #
def submit(conn: sqlite3.Connection, actions: List[dict]) -> dict:
    """事务化提交。返回结构化结果；校验/冲突错误抛 ValidationError 或返回冲突体。"""
    if not isinstance(actions, list) or not actions:
        raise ValidationError("empty_submission", "提交必须包含至少一个动作")

    conn.execute("BEGIN IMMEDIATE")
    try:
        snap = load_snapshot(conn)
        for index, act in enumerate(actions):
            apply_action(snap, act, index)

        result = build_and_solve(snap)
        if not result["satisfiable"]:
            conn.execute("ROLLBACK")
            decorated = _decorate_conflict(snap, result["conflict"])
            # 驳回同样写入流水（回滚后以新事务留痕，原编年表不变）。
            sub_id = dbmod.insert_submission(
                conn, False, "unsatisfiable",
                {"action_count": len(actions), "conflict": decorated},
            )
            return {
                "accepted": False,
                "submission_id": sub_id,
                "reason": "unsatisfiable",
                "conflict": decorated,
                "chronology": current_chronology(conn),
            }

        persist(conn, snap, actions)
        saved = build_and_solve(load_snapshot(conn))
        detail = {
            "action_count": len(actions),
            "conflict": None,
            "domains": {str(k): v for k, v in saved["domains"].items()},
        }
        sub_id = dbmod.insert_submission(conn, True, "ok", detail)
        conn.execute("COMMIT")
        return {
            "accepted": True,
            "submission_id": sub_id,
            "reason": "ok",
            "chronology": _chronology_from_solution(load_snapshot(conn), saved),
        }
    except ValidationError:
        conn.execute("ROLLBACK")
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _decorate_conflict(snap: Snapshot, conflict: dict) -> dict:
    out = dict(conflict)
    layer = snap.layers.get(conflict["layer_id"])
    out["layer_code"] = layer.code if layer else None
    evs = []
    for eid in conflict.get("evidence_ids", []):
        ev = snap.evidence.get(eid)
        if ev:
            evs.append({"id": eid, "kind": ev.kind, "note": ev.note, "active": ev.active})
    out["evidence"] = evs
    if "cycle_layers" in conflict:
        out["cycle_layer_codes"] = [
            snap.layers[i].code for i in conflict["cycle_layers"] if i in snap.layers
        ]
    return out


def _chronology_from_solution(snap: Snapshot, solution: dict) -> dict:
    rows = []
    for lid in sorted(snap.layers):
        rec = snap.layers[lid]
        feasible = [list(iv) for iv in solution["domains"].get(lid, [])]
        rows.append({
            "layer_id": lid,
            "code": rec.code,
            "name": rec.name,
            "feasible_intervals": feasible,
            "gap_count": max(0, len(feasible) - 1),
            "unconstrained": lid not in solution.get("constrained_layer_ids", set()),
        })
    return {
        "satisfiable": solution["satisfiable"],
        "layers": rows,
        "conflict": solution["conflict"],
    }


def current_chronology(conn: sqlite3.Connection) -> dict:
    """当前入库状态下的编年表（无解时返回冲突与原始候选域，表仍可查）。"""
    snap = load_snapshot(conn)
    solution = build_and_solve(snap)
    return _chronology_from_solution(snap, solution)


def full_state(conn: sqlite3.Connection) -> dict:
    snap = load_snapshot(conn)
    solution = build_and_solve(snap)
    return {
        "chronology": _chronology_from_solution(snap, solution),
        "layers": [
            {"id": l.id, "code": l.code, "name": l.name}
            for l in sorted(snap.layers.values(), key=lambda r: r.id)
        ],
        "evidence": [
            {
                "id": e.id, "kind": e.kind, "layer_id": e.layer_id,
                "layer2_id": e.layer2_id, "lower_year": e.lower_year,
                "upper_year": e.upper_year, "note": e.note, "active": e.active,
                "version": e.version,
            }
            for e in sorted(snap.evidence.values(), key=lambda r: r.id)
        ],
        "registrations": [
            {
                "id": r.id, "layer_id": r.layer_id,
                "lower_year": r.lower_year, "upper_year": r.upper_year,
                "note": r.note, "status": r.status, "replaced_by": r.replaced_by,
            }
            for r in sorted(snap.registrations.values(), key=lambda r: r.id)
        ],
        "submissions": [
            {
                "id": row["id"], "accepted": bool(row["accepted"]),
                "reason": row["reason"], "created_at": row["created_at"],
                "detail": _safe_json(row["detail"]),
            }
            for row in dbmod.list_submissions(conn)
        ],
    }


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


# --------------------------------------------------------------------------- #
# 外发校样（proof）
#
# 只取所选层位、所选有效依据，按与编年表完全相同的规则重算；登记不进入校样，
# 也不影响校样结果。校样记录在内部留档（proof 表），依据快照含版本号；
# 每次读取/下载都按当前库现算资格——依据发生任何更正或撤回（哪怕之后改回原值）
# 版本号都会前进，从而永久失去下载资格；登记及其它依据的改动不影响。
# --------------------------------------------------------------------------- #
def _proof_snapshot(conn: sqlite3.Connection) -> Snapshot:
    """一次性读取当前库的台账快照，保证生成时“同一份已入库台账”取数。"""
    return load_snapshot(conn)


def _active_evidence(snap: Snapshot, eid: int) -> EvidenceRec:
    ev = snap.evidence.get(eid)
    if ev is None:
        raise ProofError(422, "unknown_evidence", f"依据 {eid} 不存在")
    if not ev.active:
        raise ProofError(422, "evidence_inactive", f"依据 {eid} 已撤回，不可选为校样依据")
    return ev


def _solve_selected(snap: Snapshot, outbound_layer_id: int,
                    evidence_ids: List[int]) -> dict:
    """只用所选层位与所选依据，按原规则（测年并集、叠压严格早于）重算。"""
    outbound = snap.layers.get(outbound_layer_id)
    if outbound is None:
        raise ProofError(422, "unknown_layer", f"外发层位 {outbound_layer_id} 不存在")

    selected: List[EvidenceRec] = []
    seen = set()
    for eid in evidence_ids:
        if eid in seen:
            continue
        seen.add(eid)
        selected.append(_active_evidence(snap, eid))

    # “入选层位”= 外发层位 + 所选测年依据所属层位。
    layer_ids = {outbound_layer_id}
    for ev in selected:
        if ev.kind == "datum":
            layer_ids.add(ev.layer_id)

    # 叠压依据涉及的两个层位都须入选，否则该依据不能引用。
    for ev in selected:
        if ev.kind == "superposition":
            missing = [lid for lid in (ev.layer_id, ev.layer2_id) if lid not in layer_ids]
            if missing:
                raise ProofError(
                    422, "superposition_layer_not_selected",
                    f"叠压依据 {ev.id} 涉及的两个层位都须入选；"
                    f"层位 {', '.join(str(m) for m in missing)} 尚未入选",
                    {"evidence_id": ev.id, "missing_layer_ids": missing},
                )

    if not selected:
        raise ProofError(
            422, "no_evidence", "请至少选择一条有效测年或叠压依据；无依据的层位校样无意义"
        )

    datum_pieces: Dict[int, List[Tuple[int, int]]] = {lid: [] for lid in layer_ids}
    edges: List[Tuple[int, int, int]] = []
    for ev in selected:
        if ev.kind == "datum":
            datum_pieces[ev.layer_id].append((ev.lower_year, ev.upper_year))
        else:
            edges.append((ev.layer_id, ev.layer2_id, ev.id))

    # 与编年表一致：有测年依据取候选并集；无任何测年依据的层位取有效年份全域。
    # 校样不使用登记，因此不做登记交运算。
    raw_domains: Dict[int, solver.Domain] = {}
    full_domain: solver.Domain = [(solver.YEAR_MIN, solver.YEAR_MAX)]
    for lid in layer_ids:
        raw_domains[lid] = (
            solver.normalize(datum_pieces[lid]) if datum_pieces[lid] else list(full_domain)
        )

    result = solver.solve(sorted(layer_ids), raw_domains, edges)
    out = dict(result)
    out["outbound_layer"] = outbound
    out["selected_evidence"] = selected
    out["layer_ids"] = sorted(layer_ids)
    return out


def _evidence_refs(snap: Snapshot, selected: List[EvidenceRec]) -> List[dict]:
    """生成时所用依据的完整快照 + 版本号（内部留档，含所有依据详情）。"""
    refs = []
    for ev in selected:
        refs.append({
            "id": ev.id,
            "kind": ev.kind,
            "layer_id": ev.layer_id,
            "layer2_id": ev.layer2_id,
            "lower_year": ev.lower_year,
            "upper_year": ev.upper_year,
            "note": ev.note,
            "version": ev.version,
        })
    return refs


def create_proof(conn: sqlite3.Connection, outbound_layer_id: int,
                 evidence_ids: List[int]) -> dict:
    """生成校样并内部留档；无法同时成立时返回冲突、不落库。"""
    if not isinstance(evidence_ids, list):
        raise ProofError(422, "bad_evidence_list", "evidence_ids 必须是数组")
    try:
        ids = [int(x) for x in evidence_ids]
    except (TypeError, ValueError):
        raise ProofError(422, "bad_evidence_ref", "依据 id 必须是整数")
    try:
        outbound_id = int(outbound_layer_id)
    except (TypeError, ValueError):
        raise ProofError(422, "bad_layer_ref", "外发层位 id 必须是整数")

    # 读快照与写入留档放在同一事务内：生成时按同一份已入库台账取数与重算。
    conn.execute("BEGIN IMMEDIATE")
    try:
        snap = _proof_snapshot(conn)
        solved = _solve_selected(snap, outbound_id, ids)
        if not solved["satisfiable"]:
            raise ProofError(
                409, "unsatisfiable",
                "所选依据无法同时成立，本次停止出件",
                {"conflict": _decorate_conflict(snap, solved["conflict"])},
            )

        payload = {
            "outbound_layer_id": outbound_id,
            "evidence": _evidence_refs(snap, solved["selected_evidence"]),
            "layer_ids": solved["layer_ids"],
            "domains": {str(k): v for k, v in solved["domains"].items()},
        }
        proof_id = dbmod.insert_proof(conn, outbound_id, payload)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _proof_view(snap, payload, dbmod.get_proof(conn, proof_id))


def proof_rows(conn: sqlite3.Connection) -> List[dict]:
    snap = load_snapshot(conn)
    return [
        _proof_view(snap, _safe_json(row["payload"]) or {}, row)
        for row in dbmod.list_proofs(conn)
    ]


def _proof_eligibility(snap: Snapshot, payload: dict) -> dict:
    """按当前库核对校样是否仍可下载。返回 eligible + 失效依据明细。"""
    invalid = []
    for ref in payload.get("evidence", []):
        ev = snap.evidence.get(ref["id"])
        if ev is None:
            invalid.append({"id": ref["id"], "reason": "依据已不存在"})
        elif not ev.active:
            invalid.append({"id": ref["id"], "reason": "依据已撤回"})
        elif ev.version != ref["version"]:
            invalid.append({"id": ref["id"], "reason": "依据已更正（含更正后又改回原值）"})
    return {"eligible": not invalid, "invalid_evidence": invalid, "reason": None if not invalid else PROOF_INACTIVE_REASON}


def _proof_view(snap: Snapshot, payload: dict, row: sqlite3.Row) -> dict:
    elig = _proof_eligibility(snap, payload)
    outbound_id = payload["outbound_layer_id"]
    layer = snap.layers.get(outbound_id)
    return {
        "id": row["id"],
        "outbound_layer_id": outbound_id,
        "outbound_layer_code": layer.code if layer else None,
        "created_at": row["created_at"],
        "eligible": elig["eligible"],
        "reason": elig["reason"],
        "invalid_evidence": elig["invalid_evidence"],
        "selected_evidence": payload.get("evidence", []),
    }


def build_proof_csv(conn: sqlite3.Connection, proof_id: int) -> Tuple[str, bytes]:
    """下载前按最新入库状态核对资格；合格则只用生成时所选依据与区间出件。"""
    snap = load_snapshot(conn)
    row = dbmod.get_proof(conn, proof_id)
    if row is None:
        raise ProofError(404, "proof_not_found", f"校样 {proof_id} 不存在")
    payload = _safe_json(row["payload"]) or {}
    elig = _proof_eligibility(snap, payload)
    if not elig["eligible"]:
        raise ProofError(
            409, "proof_ineligible", PROOF_INACTIVE_REASON,
            {"invalid_evidence": elig["invalid_evidence"]},
        )

    outbound_id = payload["outbound_layer_id"]
    layer = snap.layers.get(outbound_id)
    domains = {int(k): v for k, v in (payload.get("domains") or {}).items()}
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["层位编号", "层位ID", "可行年份区间", "区间数", "空档数"])
    for lid in payload.get("layer_ids", [outbound_id]):
        lrec = snap.layers.get(lid)
        code = lrec.code if lrec else f"#{lid}"
        ivs = domains.get(lid, [])
        text = "；".join(
            (str(lo) if lo == hi else f"{lo} ~ {hi}") for lo, hi in ivs
        ) or "无可行年份"
        writer.writerow([code, lid, text, len(ivs), max(0, len(ivs) - 1)])
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # 开头 BOM 便于表格软件识别 UTF-8
    safe_code = (layer.code if layer else "layer").replace("/", "_").replace("\\", "_")
    filename = f"proof-{proof_id}-{safe_code}.csv"
    return filename, data
