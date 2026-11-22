/* 考古编年核对前端（原生 JS，无构建依赖）。 */

const ACTION_SCHEMAS = {
  add_layer: {
    label: "新增层位",
    fields: [
      { key: "code", label: "层位编号（唯一）", placeholder: "L5" },
      { key: "name", label: "名称", placeholder: "H5 灰土层" },
      { key: "note", label: "备注", optional: true },
    ],
  },
  add_dating: {
    label: "新增测年候选闭区间",
    fields: [
      { key: "layer_id", label: "层位 ID", type: "number" },
      { key: "lower_year", label: "下界年（整数，公元前为负）", type: "number" },
      { key: "upper_year", label: "上界年（整数）", type: "number" },
      { key: "note", label: "依据说明（如测年报告编号）", optional: true },
    ],
  },
  add_superposition: {
    label: "新增严格早于（叠压）",
    fields: [
      { key: "earlier_layer_id", label: "较早层位 ID", type: "number" },
      { key: "later_layer_id", label: "较晚层位 ID", type: "number" },
      { key: "note", label: "依据说明", optional: true },
    ],
  },
  correct_dating: {
    label: "更正测年区间",
    fields: [
      { key: "evidence_id", label: "测年依据 ID", type: "number" },
      { key: "lower_year", label: "新下界年", type: "number" },
      { key: "upper_year", label: "新上界年", type: "number" },
      { key: "note", label: "更正说明", optional: true },
    ],
  },
  correct_superposition: {
    label: "更正叠压关系",
    fields: [
      { key: "evidence_id", label: "叠压依据 ID", type: "number" },
      { key: "earlier_layer_id", label: "新较早层位 ID", type: "number" },
      { key: "later_layer_id", label: "新较晚层位 ID", type: "number" },
      { key: "note", label: "更正说明", optional: true },
    ],
  },
  withdraw_evidence: {
    label: "撤回依据",
    fields: [
      { key: "evidence_id", label: "依据 ID", type: "number" },
    ],
  },
  add_registration: {
    label: "登记拟采用区间",
    fields: [
      { key: "layer_id", label: "层位 ID", type: "number" },
      { key: "lower_year", label: "下界年", type: "number" },
      { key: "upper_year", label: "上界年", type: "number" },
      { key: "note", label: "登记说明", optional: true },
    ],
  },
  replace_registration: {
    label: "更换登记",
    fields: [
      { key: "registration_id", label: "原登记 ID", type: "number" },
      { key: "lower_year", label: "新下界年", type: "number" },
      { key: "upper_year", label: "新上界年", type: "number" },
      { key: "note", label: "更换说明", optional: true },
    ],
  },
  withdraw_registration: {
    label: "撤回登记",
    fields: [
      { key: "registration_id", label: "登记 ID", type: "number" },
    ],
  },
  withdraw_with: {
    label: "更正/撤回依据并联动撤回失效登记",
    fields: [
      { key: "evidence_id", label: "依据 ID", type: "number" },
      { key: "registration_ids", label: "联动撤回的登记 ID（逗号分隔）", placeholder: "3,5" },
      { key: "withdraw_evidence", label: "同时撤回依据（填 true/false，留空为否）",
        optional: true, placeholder: "false" },
      { key: "lower_year", label: "更正测年：新下界（留空则不改）", type: "number", optional: true },
      { key: "upper_year", label: "更正测年：新上界", type: "number", optional: true },
      { key: "earlier_layer_id", label: "更正叠压：新较早层位", type: "number", optional: true },
      { key: "later_layer_id", label: "更正叠压：新较晚层位", type: "number", optional: true },
      { key: "note", label: "说明", optional: true },
    ],
  },
};

let state = null;

const $ = (id) => document.getElementById(id);

function fmtYear(y) {
  return y < 0 ? `前${-y}` : `${y}`;
}

function fmtIntervals(ivs) {
  if (!ivs || ivs.length === 0) return '<span class="tag bad">无可选年份</span>';
  return ivs.map(([a, b]) =>
    a === b
      ? `<span class="iv">${fmtYear(a)}</span>`
      : `<span class="iv">${fmtYear(a)} ~ ${fmtYear(b)}</span>`
  ).join("");
}

function showMsg(kind, text) {
  const el = $("msg");
  el.className = kind;
  el.textContent = text;
}

function renderActionSelect() {
  const sel = $("f-action");
  sel.innerHTML = Object.entries(ACTION_SCHEMAS)
    .map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("");
  sel.onchange = renderFields;
  renderFields();
}

function renderFields() {
  const kind = $("f-action").value;
  const box = $("form-fields");
  box.innerHTML = ACTION_SCHEMAS[kind].fields.map((f) =>
    `<label>${f.label}${f.optional ? "（可选）" : ""}</label>
     <input data-k="${f.key}" data-type="${f.type || "text"}"
            placeholder="${f.placeholder || ""}">`
  ).join("");
}

function appendAction() {
  const kind = $("f-action").value;
  const act = { action: kind };
  $("form-fields").querySelectorAll("input").forEach((inp) => {
    const raw = inp.value.trim();
    if (raw === "") return;
    if (inp.dataset.type === "number") {
      act[inp.dataset.k] = Number(raw);
    } else if (inp.dataset.k === "registration_ids") {
      act[inp.dataset.k] = raw.split(",").map((s) => Number(s.trim())).filter((n) => !Number.isNaN(n));
    } else if (inp.dataset.k === "withdraw_evidence") {
      act[inp.dataset.k] = raw.toLowerCase() === "true";
    } else {
      act[inp.dataset.k] = raw;
    }
  });
  const batch = getBatch();
  batch.push(act);
  $("batch").value = JSON.stringify(batch, null, 2);
}

function getBatch() {
  const txt = $("batch").value.trim();
  if (!txt) return [];
  try {
    const parsed = JSON.parse(txt);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    showMsg("err", "批次 JSON 解析失败：" + e.message);
    throw e;
  }
}

function clearBatch() {
  $("batch").value = "";
}

async function submitBatch() {
  let actions;
  try {
    actions = getBatch();
  } catch (e) {
    return;
  }
  if (actions.length === 0) {
    showMsg("err", "提交必须包含至少一个动作。");
    return;
  }
  try {
    const resp = await fetch("/api/submissions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actions }),
    });
    const data = await resp.json();
    if (resp.ok && data.accepted) {
      showMsg("ok", `提交 #${data.submission_id} 已通过核对并入库。`);
      clearBatch();
    } else if (resp.status === 409) {
      const c = data.conflict || {};
      const ev = (c.evidence || []).map((e) => `#${e.id}(${e.kind})`).join("、");
      let txt = `提交被驳回（流水 #${data.submission_id}），原编年表未改动。\n`
        + `冲突层位：${c.layer_code || c.layer_id}\n`
        + `原因：${c.reason}\n`
        + `涉及依据：${ev || "（无向关联依据，多为登记与测年候选不相交）"}`;
      if (c.cycle_layer_codes) txt += `\n环路径：${c.cycle_layer_codes.join(" → ")}`;
      showMsg("err", txt);
    } else {
      const err = data.error || data.detail || data;
      showMsg("err", "输入校验失败（422）：" + JSON.stringify(err, null, 2));
    }
  } catch (e) {
    showMsg("err", "请求失败：" + e.message);
  }
  await refresh();
}

function layerCode(id) {
  const l = (state.layers || []).find((x) => x.id === id);
  return l ? `${l.code}(#${id})` : `#${id}`;
}

function render() {
  const ch = state.chronology;
  $("status-line").innerHTML = ch.satisfiable
    ? '<span class="tag ok">当前全部依据与登记可同时成立</span>'
    : '<span class="tag bad">当前状态无解（下列为原始候选，原编年表仍可查）</span>';

  $("chrono-table").querySelector("tbody").innerHTML = ch.layers.map((r) => `
    <tr>
      <td><b>${r.code}</b> <span class="muted">#${r.layer_id}</span></td>
      <td>${r.name || ""}</td>
      <td>${fmtIntervals(r.feasible_intervals)}</td>
      <td>${r.gap_count > 0 ? `<b>${r.gap_count}</b><span class="gap">含空档</span>` : "0"}
          ${r.unconstrained ? '<div class="muted">无测年/登记限定</div>' : ""}</td>
      <td>${r.feasible_intervals.length ? '<span class="tag ok">可行</span>'
                                        : '<span class="tag bad">冲突</span>'}</td>
    </tr>`).join("");

  $("layer-table").querySelector("tbody").innerHTML =
    (state.layers || []).map((l) => `<tr><td>${l.id}</td><td>${l.code}</td><td>${l.name || ""}</td></tr>`).join("");

  $("evidence-table").querySelector("tbody").innerHTML =
    (state.evidence || []).map((e) => {
      const isDatum = e.kind === "datum";
      const range = isDatum
        ? `${fmtYear(e.lower_year)} ~ ${fmtYear(e.upper_year)}`
        : "（严格早于）";
      const target = isDatum ? layerCode(e.layer_id) : layerCode(e.layer_id);
      const other = isDatum ? "—" : layerCode(e.layer2_id);
      return `<tr>
        <td>${e.id}</td>
        <td>${isDatum ? "测年" : "叠压"}</td>
        <td>${target}</td>
        <td>${other}</td>
        <td class="code">${range}</td>
        <td>${e.note || ""}</td>
        <td>${e.active ? '<span class="tag ok">有效</span>' : '<span class="tag gray">已撤回</span>'}</td>
      </tr>`;
    }).join("");

  $("reg-table").querySelector("tbody").innerHTML =
    (state.registrations || []).map((r) => {
      const st = r.status === "active" ? '<span class="tag ok">有效</span>'
        : r.status === "replaced" ? '<span class="tag gray">已更换</span>'
        : '<span class="tag gray">已撤回</span>';
      return `<tr>
        <td>${r.id}</td><td>${layerCode(r.layer_id)}</td>
        <td class="code">${fmtYear(r.lower_year)} ~ ${fmtYear(r.upper_year)}</td>
        <td>${r.note || ""}</td><td>${st}</td>
        <td>${r.replaced_by ?? ""}</td></tr>`;
    }).join("");

  $("sub-table").querySelector("tbody").innerHTML =
    (state.submissions || []).map((s) => `
      <tr>
        <td>${s.id}</td>
        <td>${s.accepted ? '<span class="tag ok">通过</span>' : '<span class="tag bad">驳回</span>'}</td>
        <td>${s.reason}</td><td class="code">${s.created_at}</td>
        <td class="code">${JSON.stringify(s.detail).slice(0, 220)}</td>
      </tr>`).join("");
}

async function refresh() {
  const resp = await fetch("/api/state");
  state = await resp.json();
  render();
  renderProofLayerSelect();
  await refreshProofs();
}

/* ------------------------------------------------------------------ */
/* 外发校样                                                            */
/* ------------------------------------------------------------------ */

function activeEvidence() {
  return (state.evidence || []).filter((e) => e.active);
}

function selectedOutboundLayer() {
  return Number($("proof-layer").value);
}

function checkedEvidenceIds() {
  return Array.from(
    document.querySelectorAll("#proof-evidence input[type=checkbox]:checked")
  ).map((el) => Number(el.value));
}

// “入选层位”= 外发层位 + 已勾选测年依据所属层位。
function selectedLayers() {
  const ids = new Set([selectedOutboundLayer()]);
  const byId = new Map(activeEvidence().map((e) => [e.id, e]));
  for (const eid of checkedEvidenceIds()) {
    const ev = byId.get(eid);
    if (ev && ev.kind === "datum") ids.add(ev.layer_id);
  }
  return ids;
}

function proofEvidenceLine(e) {
  const isDatum = e.kind === "datum";
  const range = isDatum ? `${fmtYear(e.lower_year)} ~ ${fmtYear(e.upper_year)}` : "严格早于";
  const rel = isDatum
    ? layerCode(e.layer_id)
    : `${layerCode(e.layer_id)} → ${layerCode(e.layer2_id)}`;
  return `<div class="meta">${isDatum ? "测年" : "叠压"} · ${rel} · ${range}${
    e.note ? " · " + e.note : ""}</div>`;
}

function reloadProofEvidence() {
  const box = $("proof-evidence");
  if (!state || !state.layers || state.layers.length === 0) {
    box.innerHTML = '<span class="muted">尚无层位，请先在②提交层位与依据。</span>';
    return;
  }
  const outbound = selectedOutboundLayer();
  if (!outbound) {
    box.innerHTML = '<span class="muted">请先选择外发层位。</span>';
    return;
  }
  const evs = activeEvidence();
  if (evs.length === 0) {
    box.innerHTML = '<span class="muted">当前没有有效依据可引用。</span>';
    return;
  }
  const layers = selectedLayers();
  const keep = new Set(checkedEvidenceIds());
  const chosen = evs.filter((e) => keep.has(e.id));
  // 重新计算“入选层位”时已勾选项也会扩展，迭代到稳定，保证联动勾选不会突兀失效。
  let grew = true;
  while (grew) {
    grew = false;
    for (const e of chosen) {
      if (e.kind === "datum" && !layers.has(e.layer_id)) {
        layers.add(e.layer_id);
        grew = true;
      }
    }
  }

  box.innerHTML = evs.map((e) => {
    const missing = [];
    if (e.kind === "superposition") {
      if (!layers.has(e.layer_id)) missing.push(e.layer_id);
      if (!layers.has(e.layer2_id)) missing.push(e.layer2_id);
    }
    const disabled = missing.length > 0;
    // 因层位不再入选而失效的勾选一并清掉，避免提交后被服务端拒绝。
    const checked = !disabled && keep.has(e.id);
    const note = disabled
      ? `<div class="meta" style="color:#cf222e">两端层位都须入选：尚缺 ${
          missing.map((id) => layerCode(id)).join("、")}</div>`
      : "";
    return `<label class="evpick ${disabled ? "disabled" : ""}">
      <input type="checkbox" value="${e.id}" ${checked ? "checked" : ""}
             ${disabled ? "disabled" : ""} onchange="reloadProofEvidence()">
      <div><b>#${e.id}</b> ${e.kind === "datum" ? "测年" : "叠压"}${
        e.note ? " · " + e.note : ""}${proofEvidenceLine(e)}${note}</div>
    </label>`;
  }).join("");
}

function onProofLayerChange() {
  // 换外发层位后“入选层位”集合变化，清空勾选从头来，避免残留不可引用项。
  $("proof-evidence").querySelectorAll("input[type=checkbox]").forEach((el) => {
    el.checked = false;
  });
  reloadProofEvidence();
}

function renderProofLayerSelect() {
  const sel = $("proof-layer");
  if (!state || !state.layers) return;
  const prev = sel.value;
  sel.innerHTML = state.layers
    .map((l) => `<option value="${l.id}">${l.code}（#${l.id}）${l.name ? " · " + l.name : ""}</option>`)
    .join("");
  if (prev && state.layers.some((l) => String(l.id) === prev)) sel.value = prev;
  sel.onchange = onProofLayerChange;
  reloadProofEvidence();
}

function renderProofMsg(kind, html) {
  const out = $("proof-out");
  out.className = "show";
  out.innerHTML = kind === "err"
    ? `<div class="conflict">${html}</div>`
    : `<div class="card">${html}</div>`;
}

async function generateProof() {
  const outbound = selectedOutboundLayer();
  const ids = checkedEvidenceIds();
  if (!outbound) {
    renderProofMsg("err", "请先选择外发层位。");
    return;
  }
  if (ids.length === 0) {
    renderProofMsg("err", "请至少勾选一条可引用的有效依据。");
    return;
  }
  try {
    const resp = await fetch("/api/proofs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outbound_layer_id: outbound, evidence_ids: ids }),
    });
    const data = await resp.json();
    if (resp.ok) {
      renderProofMsg("ok",
        `校样 #${data.id} 已生成（内部留档）。\n外发层位：${data.outbound_layer_code}\n`
        + `所用依据：${data.selected_evidence.map((e) => "#" + e.id).join("、")}\n`
        + `可在下方“校样留档与下载”下载 CSV。`);
    } else if (resp.status === 409 && data.error && data.error.code === "unsatisfiable") {
      const c = data.error.conflict || {};
      const ev = (c.evidence || []).map((e) => `#${e.id}(${e.kind})`).join("、");
      let txt = `所选依据无法同时成立，本次停止出件（未生成校样）。\n`
        + `冲突层位：${c.layer_code || c.layer_id}\n`
        + `原因：${c.reason}\n`
        + `涉及依据：${ev || "（无向关联依据，多为测年候选不相交）"}`;
      if (c.cycle_layer_codes) txt += `\n环路径：${c.cycle_layer_codes.join(" → ")}`;
      renderProofMsg("err", txt);
    } else {
      const err = (data.error || data);
      renderProofMsg("err", "校样请求失败：" + (err.message || JSON.stringify(err)));
    }
  } catch (e) {
    renderProofMsg("err", "请求失败：" + e.message);
  }
  await refreshProofs();
}

async function refreshProofs() {
  let rows = [];
  try {
    const resp = await fetch("/api/proofs");
    rows = await resp.json();
  } catch (e) {
    return;
  }
  const tbody = $("proof-table").querySelector("tbody");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">暂无校样。</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((p) => {
    const evs = (p.selected_evidence || []).map((e) => `#${e.id}`).join("、");
    const st = p.eligible
      ? '<span class="tag ok">可下载</span>'
      : `<span class="tag bad">已失格</span><div class="muted">${
          (p.invalid_evidence || []).map((x) => `#${x.id} ${x.reason}`).join("；")}</div>`;
    const dl = p.eligible
      ? `<a class="tag ok" href="/api/proofs/${p.id}/csv"
            onclick="downloadProof(event, ${p.id})">下载 CSV</a>`
      : '<span class="muted">不可下载</span>';
    return `<tr class="proof-item">
      <td>${p.id}</td>
      <td>${p.outbound_layer_code || "—"} <span class="muted">#${p.outbound_layer_id}</span></td>
      <td>${evs || "—"}</td>
      <td class="code">${p.created_at}</td>
      <td>${st}</td>
      <td>${dl}</td>
    </tr>`;
  }).join("");
}

async function downloadProof(ev, id) {
  // 用 fetch 下载以便把 409（依据已更正/撤回）在页面上直接说明，而不是打开一段 JSON。
  ev.preventDefault();
  try {
    const resp = await fetch(`/api/proofs/${id}/csv`);
    if (resp.ok) {
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `proof-${id}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      return;
    }
    const data = await resp.json().catch(() => ({}));
    const err = data.error || {};
    const bad = (err.invalid_evidence || [])
      .map((x) => `#${x.id} ${x.reason}`).join("；");
    renderProofMsg("err",
      `校样 #${id} 不可下载：${err.message || resp.statusText}`
      + (bad ? `\n失效依据：${bad}` : ""));
  } catch (e) {
    renderProofMsg("err", "下载失败：" + e.message);
  }
  await refreshProofs();
}

function loadDemo() {
  // 演示：新建层位 + 两条分离测年区间 + 叠压（同批临时 id -1）
  $("batch").value = JSON.stringify([
    { action: "add_layer", code: "L-DEMO", name: "演示层位" },
    { action: "add_dating", layer_id: -1, lower_year: -1200, upper_year: -1150, note: "碳样 ZK-1" },
    { action: "add_dating", layer_id: -1, lower_year: -900, upper_year: -850, note: "碳样 ZK-2" },
    { action: "add_registration", layer_id: -1, lower_year: -1000, upper_year: -800, note: "拟采用晚段" },
  ], null, 2);
}

renderActionSelect();
refresh();
