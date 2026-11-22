"""求解器穷举对照测试（仅标准库）：随机小实例上用暴力枚举核对可行域。"""
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import solver  # noqa: E402


def brute_force(layer_ids, raw_domains, edges):
    """枚举每个变量的候选值，返回 {layer: set(可行值)}。"""
    value_sets = {lid: set() for lid in layer_ids}
    pools = []
    for lid in layer_ids:
        vals = set()
        for a, b in raw_domains[lid]:
            vals.update(range(a, b + 1))
        pools.append(sorted(vals))
    for assignment in itertools.product(*pools):
        x = dict(zip(layer_ids, assignment))
        ok = True
        for i, j, _eid in edges:
            if not x[i] < x[j]:
                ok = False
                break
        if ok:
            for lid in layer_ids:
                value_sets[lid].add(x[lid])
    return value_sets


def to_set(domain):
    out = set()
    for a, b in domain:
        out.update(range(a, b + 1))
    return out


def random_instance(rng):
    n = rng.randint(2, 4)
    layer_ids = list(range(1, n + 1))
    raw = {}
    for lid in layer_ids:
        # 随机造 1~2 个小区间，刻意制造空档
        starts = sorted(rng.sample(range(1, 12), rng.randint(1, 2)))
        ivs = []
        for s in starts:
            ivs.append((s, min(12, s + rng.randint(0, 3))))
        raw[lid] = solver.normalize(ivs)
    # 随机严格早于边（只取 i<j 的边，避免无谓自环；另有专门环测试）
    possible = [(i, j) for i in layer_ids for j in layer_ids if i < j]
    rng.shuffle(possible)
    k = rng.randint(0, len(possible))
    edges = [(i, j, eid + 1) for eid, (i, j) in enumerate(possible[:k])]
    return layer_ids, raw, edges


def main():
    rng = random.Random(20261122)
    failures = 0
    for trial in range(4000):
        layer_ids, raw, edges = random_instance(rng)
        result = solver.solve(layer_ids, raw, edges)
        bf = brute_force(layer_ids, raw, edges)
        bf_sat = all(len(bf[lid]) > 0 for lid in layer_ids)
        if result["satisfiable"] != bf_sat:
            failures += 1
            print("SAT MISMATCH", layer_ids, raw, edges, result["satisfiable"], bf_sat)
            continue
        if bf_sat:
            for lid in layer_ids:
                got = to_set(result["domains"][lid])
                if got != bf[lid]:
                    failures += 1
                    print("DOMAIN MISMATCH", lid, got, bf[lid], raw, edges)
        else:
            assert result["conflict"] is not None

    # 区间并集交集的空档保持（旧实现曾在第二段误并）
    d = solver.normalize([(100, 110), (200, 210)])
    full = solver.normalize([(solver.YEAR_MIN, solver.YEAR_MAX)])
    assert solver.intersect(d, full) == [(100, 110), (200, 210)]
    assert solver.intersect(full, d) == [(100, 110), (200, 210)]
    d2 = solver.normalize([(105, 205)])
    assert solver.intersect(d, d2) == [(105, 110), (200, 205)], solver.intersect(d, d2)
    assert solver.intersect(d2, d) == [(105, 110), (200, 205)]
    assert solver.intersect(solver.normalize([(1, 5), (10, 15)]),
                            solver.normalize([(3, 12)])) == [(3, 5), (10, 12)]

    # 含空档的定向用例：D1={3,5,7}, D2={4,6,8}，约束 1<2
    raw = {
        1: solver.normalize([(3, 3), (5, 5), (7, 7)]),
        2: solver.normalize([(4, 4), (6, 6), (8, 8)]),
    }
    res = solver.solve([1, 2], raw, [(1, 2, 1)])
    assert res["satisfiable"]
    assert res["domains"][1] == [(3, 3), (5, 5), (7, 7)], res["domains"][1]
    assert res["domains"][2] == [(4, 4), (6, 6), (8, 8)], res["domains"][2]

    # 含空档且需要剪枝：D1={3,5,7}, D2={6}，1<2 -> 7 失去支撑
    raw1b = {
        1: solver.normalize([(3, 3), (5, 5), (7, 7)]),
        2: solver.normalize([(6, 6)]),
    }
    res1b = solver.solve([1, 2], raw1b, [(1, 2, 1)])
    assert res1b["satisfiable"]
    assert res1b["domains"][1] == [(3, 3), (5, 5)], res1b["domains"][1]
    assert res1b["domains"][2] == [(6, 6)]

    # 空档保留检查：可行域仍为两段
    raw2 = {1: solver.normalize([(1, 4), (10, 14)]), 2: solver.normalize([(5, 20)])}
    res2 = solver.solve([1, 2], raw2, [(1, 2, 1)])
    assert res2["domains"][1] == [(1, 4), (10, 14)], res2["domains"][1]
    assert res2["domains"][2] == [(5, 20)], res2["domains"][2]

    # 环检测
    cyc = solver.detect_cycles([1, 2, 3], [(1, 2, 1), (2, 3, 2), (3, 1, 3)])
    assert cyc is not None and cyc[1] == [1, 2, 3]
    res3 = solver.solve([1, 2, 3],
                        {1: [(1, 5)], 2: [(1, 5)], 3: [(1, 5)]},
                        [(1, 2, 1), (2, 3, 2), (3, 1, 3)])
    assert not res3["satisfiable"] and res3["conflict"]["cycle_layers"]

    # 区间端点矛盾：1<2 但 D1 全大于 D2
    res4 = solver.solve([1, 2], {1: [(10, 12)], 2: [(1, 5)]}, [(1, 2, 1)])
    assert not res4["satisfiable"]
    assert 1 in res4["conflict"]["evidence_ids"]

    # 自环
    res5 = solver.solve([1], {1: [(1, 5)]}, [(1, 1, 9)])
    assert not res5["satisfiable"] and res5["conflict"]

    # 初始域即空（登记与测年候选不相交）
    res6 = solver.solve([1, 2], {1: [], 2: [(1, 5)]}, [(1, 2, 1)])
    assert not res6["satisfiable"]
    assert res6["conflict"]["layer_id"] == 1
    assert 1 in res6["conflict"]["evidence_ids"]
    res7 = solver.solve([1], {1: []}, [])
    assert not res7["satisfiable"] and res7["conflict"]["layer_id"] == 1

    print(f"random trials=4000 targeted=5 failures={failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
