"""编年约束求解器（仅依赖标准库）。

问题模型
--------
每个层位是一个取整数年的变量 x_i；年份用整数表示，约定公元前 N 年记为 -N
（例如公元前 1200 年 = -1200，公元 800 年 = 800）。

两类依据都化为有限整数域上的约束：

* 测年依据（候选年代闭区间）：单变量域的并集
      D_i = [a1, b1] ∪ [a2, b2] ∪ ...
* 严格早于（地层叠压）依据 i 早于 j：
      x_i < x_j  ⇔  x_j >= x_i + 1

求解器在“区间并集”表示的域上做弧一致性（AC-3，有向弧队列）传播。
每条无向边 (i, j, eid) 对应两个有向弧：
  * 修订 i：i 的值 v 必须在 j 中存在 w >= v+1；
  * 修订 j：j 的值 w 必须在 i 中存在 v <= w-1。
区间并集上只需检查端点，空档天然保留。

对这种单调（max-closed / min-closed）约束语言，弧一致性后的非空域恰好等于
全部可行取值（tests 中用 4000 组随机实例与暴力枚举对照验证）；
某变量域被清空（或严格早于图出现有向环）即无可行解。
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

# 无限定层位（无任何测年依据）使用的有效年份边界。
YEAR_MIN = -10_000
YEAR_MAX = 10_000

Domain = List[Tuple[int, int]]  # 排序、不相交、已合并相邻段的闭区间并集


# --------------------------------------------------------------------------- #
# 区间并集基础运算
# --------------------------------------------------------------------------- #
def normalize(intervals: Sequence[Tuple[int, int]]) -> Domain:
    """合并重叠/相邻（相差 <=1，因为是整数闭区间）的区间，并排序。"""
    pts = sorted((int(a), int(b)) for a, b in intervals if int(a) <= int(b))
    out: Domain = []
    for a, b in pts:
        if out and a <= out[-1][1] + 1:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def intersect(a: Domain, b: Domain) -> Domain:
    """两个区间并集的交集。"""
    out: Domain = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi:
            out.append((lo, hi))
        # 右端点较小的区间前进（相等则同时前进）
        if a[i][1] < b[j][1]:
            i += 1
        elif b[j][1] < a[i][1]:
            j += 1
        else:
            i += 1
            j += 1
    return normalize(out)


def cut_above(domain: Domain, upper: int) -> Domain:
    """只保留 <= upper 的部分。"""
    out: Domain = []
    for a, b in domain:
        if a > upper:
            break
        out.append((a, min(b, upper)))
    return normalize(out)


def cut_below(domain: Domain, lower: int) -> Domain:
    """只保留 >= lower 的部分。"""
    out: Domain = []
    for a, b in domain:
        if b < lower:
            continue
        out.append((max(a, lower), b))
    return normalize(out)


def is_empty(d: Domain) -> bool:
    return not d


def domain_min(d: Domain) -> int:
    return d[0][0]


def domain_max(d: Domain) -> int:
    return d[-1][1]


# --------------------------------------------------------------------------- #
# 有向弧修订
# --------------------------------------------------------------------------- #
def revise_tail(tail: int, head: int, domains: Dict[int, Domain]) -> Domain:
    """约束 head >= tail + 1（tail 严格早于 head）。

    修订 tail：值 v 需在 head 域中存在 w >= v+1，即 v <= max(D_head)-1。
    """
    if not domains[tail] or not domains[head]:
        return domains[tail]
    upper = domain_max(domains[head]) - 1
    return cut_above(domains[tail], upper)


def revise_head(head: int, tail: int, domains: Dict[int, Domain]) -> Domain:
    """约束 head >= tail + 1。

    修订 head：值 w 需在 tail 域中存在 v <= w-1，即 w >= min(D_tail)+1。
    """
    if not domains[head] or not domains[tail]:
        return domains[head]
    lower = domain_min(domains[tail]) + 1
    return cut_below(domains[head], lower)


# --------------------------------------------------------------------------- #
# AC-3 主流程
# --------------------------------------------------------------------------- #
class Conflict:
    """无解诊断：哪个层位的域被清空、涉及哪些依据。"""

    def __init__(self, layer_id: int, evidence_ids: List[int], reason: str):
        self.layer_id = layer_id
        self.evidence_ids = sorted(set(evidence_ids))
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "layer_id": self.layer_id,
            "evidence_ids": self.evidence_ids,
            "reason": self.reason,
        }


def ac3(
    layer_ids: Sequence[int],
    initial_domains: Dict[int, Domain],
    # (早于方 i, 晚于方 j, 依据 id)；严格早于即 head=j >= tail=i+1
    edges: Sequence[Tuple[int, int, int]],
) -> Tuple[Dict[int, Domain], Optional[Conflict]]:
    """弧一致性传播。

    队列元素为有向弧 (被修订变量, 邻变量, i, j, eid)，标明被修订者是 tail 还是 head。
    返回 (最终域, 冲突)。冲突为 None 时最终域即各层位全部可行年份（保留空档）。
    """
    domains: Dict[int, Domain] = {lid: list(initial_domains.get(lid, [])) for lid in layer_ids}

    # 每个变量的邻接边，供域变化后重入相关有向弧。
    incident: Dict[int, List[Tuple[int, int, int]]] = {lid: [] for lid in layer_ids}
    for i, j, eid in edges:
        if i not in incident or j not in incident:
            raise ValueError(f"边引用了未知层位: {i} -> {j}")
        incident[i].append((i, j, eid))
        incident[j].append((i, j, eid))

    # 有向弧：(revise_var, neighbor, i, j, eid, role) role='tail'/'head'
    queue: deque = deque()
    in_queue: set = set()
    for i, j, eid in edges:
        queue.append((i, j, i, j, eid, "tail"))
        queue.append((j, i, i, j, eid, "head"))
        in_queue.update({(i, j, i, j, eid, "tail"), (j, i, i, j, eid, "head")})

    touched_evidence: Dict[int, set] = {lid: set() for lid in layer_ids}
    for i, j, eid in edges:
        touched_evidence[i].add(eid)
        touched_evidence[j].add(eid)

    while queue:
        revise_var, neighbor, i, j, eid, role = queue.popleft()
        in_queue.discard((revise_var, neighbor, i, j, eid, role))
        if role == "tail":
            new_domain = revise_tail(revise_var, neighbor, domains)
        else:
            new_domain = revise_head(revise_var, neighbor, domains)

        if new_domain == domains[revise_var]:
            continue
        domains[revise_var] = new_domain
        if is_empty(new_domain):
            return domains, Conflict(
                revise_var,
                list(touched_evidence[revise_var]),
                f"层位 {revise_var} 的候选年份在相关依据约束下被全部排除",
            )
        # revise_var 的域变化：重入所有“修订其邻变量”的有向弧。
        for (ei, ej, eeid) in incident[revise_var]:
            if revise_var == ei:
                arc = (ej, ei, ei, ej, eeid, "head")   # 修订 head=ej
            else:
                arc = (ei, ej, ei, ej, eeid, "tail")   # 修订 tail=ei
            if arc not in in_queue:
                in_queue.add(arc)
                queue.append(arc)

    return domains, None


def detect_cycles(
    layer_ids: Sequence[int],
    edges: Sequence[Tuple[int, int, int]],
) -> Optional[Tuple[List[int], List[int]]]:
    """在严格早于图上检测有向环（环本身即矛盾，如 A<B<A，含自环）。

    返回 (环上的层位 id 列表, 环涉及的依据 id 列表)；无环返回 None。
    """
    adj: Dict[int, List[Tuple[int, int]]] = {lid: [] for lid in layer_ids}
    for i, j, eid in edges:
        adj[i].append((j, eid))

    color: Dict[int, int] = {lid: 0 for lid in layer_ids}  # 0 白 1 灰 2 黑
    stack: List[int] = []
    edge_stack: List[int] = []
    found: Dict[str, list] = {}

    def dfs(u: int) -> bool:
        color[u] = 1
        stack.append(u)
        for v, eid in adj[u]:
            edge_stack.append(eid)
            if color[v] == 1:  # 回边 -> 环
                idx = stack.index(v)
                found["layers"] = stack[idx:] + [v]
                found["evidence"] = edge_stack[idx:]
                return True
            if color[v] == 0 and dfs(v):
                return True
            edge_stack.pop()
        stack.pop()
        color[u] = 2
        return False

    for lid in layer_ids:
        if color[lid] == 0 and dfs(lid):
            return found["layers"], sorted(set(found["evidence"]))
    return None


def solve(
    layer_ids: Sequence[int],
    raw_domains: Dict[int, Domain],
    edges: Sequence[Tuple[int, int, int]],
) -> dict:
    """完整求解：先查环，再做弧一致性。

    返回::
        {"satisfiable": bool,
         "domains": {layer_id: [[lo, hi], ...]},   # 可行年份区间（保留空档）
         "conflict": null | {"layer_id", "evidence_ids", "reason",
                             "cycle_layers", "cycle_evidence"}}
    """
    cycle = detect_cycles(layer_ids, edges)
    if cycle is not None:
        cyc_layers, cyc_evidence = cycle
        return {
            "satisfiable": False,
            "domains": {lid: raw_domains.get(lid, []) for lid in layer_ids},
            "conflict": {
                "layer_id": cyc_layers[0],
                "evidence_ids": cyc_evidence,
                "reason": "严格早于关系出现有向环（层位不可能同时早于彼此）",
                "cycle_layers": cyc_layers,
                "cycle_evidence": cyc_evidence,
            },
        }

    # 初始域即空（如登记区间与全部测年候选不相交）：直接给出冲突诊断。
    incident_evidence: Dict[int, List[int]] = {lid: [] for lid in layer_ids}
    for i, j, eid in edges:
        incident_evidence[i].append(eid)
        incident_evidence[j].append(eid)
    for lid in layer_ids:
        if is_empty(raw_domains.get(lid, [])):
            return {
                "satisfiable": False,
                "domains": {lid2: raw_domains.get(lid2, []) for lid2 in layer_ids},
                "conflict": Conflict(
                    lid,
                    incident_evidence[lid],
                    f"层位 {lid} 的登记区间与全部有效测年候选不相交，没有可选年份",
                ).to_dict(),
            }

    domains, conflict = ac3(layer_ids, raw_domains, edges)
    if conflict is not None:
        return {
            "satisfiable": False,
            "domains": {lid: raw_domains.get(lid, []) for lid in layer_ids},
            "conflict": conflict.to_dict(),
        }
    return {
        "satisfiable": True,
        "domains": {lid: domains[lid] for lid in layer_ids},
        "conflict": None,
    }
