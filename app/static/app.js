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
