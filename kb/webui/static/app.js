"use strict";

const REFRESH_MS = 15000;
let timer = null;
let actionCatalog = { items: [], groups: [], agents: [] };

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function cpu(value) {
  if (value === null || value === undefined || value === "") return "-";
  const cores = Number(value);
  if (!Number.isFinite(cores)) return "-";
  if (cores === 0) return "0";
  if (cores < 1) return Math.round(cores * 1000) + "m";
  return String(Math.round(cores * 100) / 100);
}

function mem(bytes) {
  if (bytes === null || bytes === undefined || bytes === "") return "-";
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return value === 0 ? "0" : "-";
  const units = [["Gi", 1024 ** 3], ["Mi", 1024 ** 2], ["Ki", 1024]];
  for (const [unit, factor] of units) {
    if (value >= factor) {
      const text = (value / factor).toFixed(1).replace(/\.0$/, "");
      return text + unit;
    }
  }
  return value + "B";
}

function ratioClass(ratio) {
  if (ratio >= 0.9) return "fail";
  if (ratio >= 0.7) return "warn";
  return "";
}

function badge(text, kind) {
  return '<span class="badge badge-' + kind + '">' + esc(text) + "</span>";
}

function renderChecks(checks) {
  const box = document.getElementById("checks");
  if (!checks || checks.length === 0) {
    box.innerHTML = '<div class="empty">暂无体检结果</div>';
    return;
  }
  box.innerHTML = checks.map(function (check) {
    return (
      '<div class="check">' +
      '<span class="dot dot-' + esc(check.status) + '"></span>' +
      "<div>" +
      '<div class="check-title">' + esc(check.title) + "</div>" +
      '<div class="check-detail">' + esc(check.detail) + "</div>" +
      "</div></div>"
    );
  }).join("");
}

function renderNodes(nodes) {
  const box = document.getElementById("nodes");
  if (!nodes || nodes.length === 0) {
    box.innerHTML = '<div class="empty">没有发现节点</div>';
    return;
  }
  box.innerHTML = nodes.map(function (node) {
    const alloc = Number(node.cpu_allocatable_cores) || 0;
    const requested = Number(node.cpu_requested_cores) || 0;
    const used = node.cpu_used_cores === null || node.cpu_used_cores === undefined
      ? null
      : Number(node.cpu_used_cores);
    const requestRatio = alloc > 0 ? Math.min(requested / alloc, 1) : 0;
    const usedRatio = alloc > 0 && used !== null ? Math.min(used / alloc, 1) : 0;

    const memAlloc = Number(node.mem_allocatable_bytes) || 0;
    const memRequested = Number(node.mem_requested_bytes) || 0;
    const memRatio = memAlloc > 0 ? Math.min(memRequested / memAlloc, 1) : 0;

    const roleTags = (node.roles || []).map(function (role) {
      return '<span class="tag">' + esc(role) + "</span>";
    }).join("");
    const ready = node.ready
      ? '<span class="status"><span class="dot dot-ok"></span>Ready</span>'
      : '<span class="status"><span class="dot dot-fail"></span>NotReady</span>';
    const taints = (node.taints || []).length
      ? '<div class="node-extra">taint: ' + esc(node.taints.join(", ")) + "</div>"
      : "";

    return (
      '<div class="node-card">' +
      '<div class="node-head"><div class="node-name">' + esc(node.display_name || node.name) +
      roleTags + "</div>" + ready + "</div>" +
      '<div class="metric"><span class="metric-label">CPU 申请</span>' +
      '<div class="bar"><div class="bar-fill ' + ratioClass(requestRatio) + '" style="width:' +
      (requestRatio * 100).toFixed(1) + '%"></div></div>' +
      '<span class="metric-value">' + cpu(requested) + " / " + cpu(alloc) + "</span></div>" +
      (used === null ? "" :
        '<div class="metric"><span class="metric-label">CPU 使用</span>' +
        '<div class="bar"><div class="bar-fill ' + ratioClass(usedRatio) + '" style="width:' +
        (usedRatio * 100).toFixed(1) + '%"></div></div>' +
        '<span class="metric-value">' + cpu(used) + "</span></div>") +
      '<div class="metric"><span class="metric-label">内存申请</span>' +
      '<div class="bar"><div class="bar-fill ' + ratioClass(memRatio) + '" style="width:' +
      (memRatio * 100).toFixed(1) + '%"></div></div>' +
      '<span class="metric-value">' + mem(memRequested) + " / " + mem(memAlloc) + "</span></div>" +
      '<div class="node-extra">Pod: ' + esc(node.pods) + " 个</div>" +
      taints +
      "</div>"
    );
  }).join("");
}

function renderDeployments(items) {
  const tbody = document.querySelector("#deployments tbody");
  if (!items || items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">没有发现工作负载</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(function (item) {
    return (
      "<tr>" +
      "<td>" + esc(item.name) + "</td>" +
      "<td>" + esc(item.namespace) + "</td>" +
      '<td class="num">' + esc(item.ready) + " / " + esc(item.replicas) + "</td>" +
      '<td class="num">' + cpu(item.cpu_request_per_pod_cores) + "</td>" +
      '<td class="num">' + cpu(item.cpu_limit_per_pod_cores) + "</td>" +
      '<td class="num">' + cpu(item.cpu_request_total_cores) + "</td>" +
      "<td>" + esc((item.nodes || []).join(", ") || "-") + "</td>" +
      "<td>" + esc((item.images || []).join(", ") || "-") + "</td>" +
      "</tr>"
    );
  }).join("");
}

function renderPods(items) {
  const tbody = document.querySelector("#pods tbody");
  if (!items || items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">没有发现 Pod</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(function (item) {
    const running = item.status === "Running";
    const dot = running ? "dot-ok" : "dot-warn";
    return (
      "<tr>" +
      "<td>" + esc(item.name) + "</td>" +
      "<td>" + esc(item.namespace) + "</td>" +
      "<td>" + esc(item.display_node || item.node) + "</td>" +
      '<td><span class="status"><span class="dot ' + dot + '"></span>' + esc(item.status) + "</span></td>" +
      '<td class="num">' + esc(item.restarts) + "</td>" +
      '<td class="num">' + cpu(item.cpu_request_cores) + "</td>" +
      '<td class="num">' + cpu(item.cpu_limit_cores) + "</td>" +
      '<td class="num">' + cpu(item.cpu_used_cores) + "</td>" +
      '<td class="num">' + mem(item.mem_request_bytes) + "</td>" +
      "<td>" + esc(item.qos) + "</td>" +
      "</tr>"
    );
  }).join("");
}

function renderEdge(data) {
  const agent = data.agent || {};
  const services = agent.services || {};
  const commands = agent.commands || {};
  const devices = agent.devices || {};

  document.getElementById("nodes").innerHTML = "";
  document.getElementById("nodes-panel").classList.add("hidden");

  const info = [
    { label: "主机名", value: agent.hostname || "-" },
    { label: "Tailscale IP", value: agent.tailscale_ip || "-" },
    { label: "UE 上电状态", value: agent.ue_power || "-" },
    { label: "Amarisoft 模式", value: agent.mode || "-" },
    { label: "网络模式", value: agent.network_mode || "-" },
    { label: "SDR 设备", value: devices.sdr0 ? "/dev/sdr0 存在" : "未发现" },
  ];

  const servicesText = Object.keys(services).map(function (key) {
    return key + ": " + services[key];
  }).join("，") || "-";
  const commandsText = Object.keys(commands).map(function (key) {
    return key + ": " + (commands[key] ? "可用" : "缺失");
  }).join("，") || "-";

  document.querySelector("#deployments tbody").innerHTML =
    '<tr><td class="empty" colspan="8">边缘模式不显示工作负载，请在主机控制台查看</td></tr>';
  document.querySelector("#pods tbody").innerHTML =
    info.map(function (item) {
      return "<tr><td>" + esc(item.label) + '</td><td colspan="9">' + esc(item.value) + "</td></tr>";
    }).join("") +
    '<tr><td>服务</td><td colspan="9">' + esc(servicesText) + "</td></tr>" +
    '<tr><td>命令</td><td colspan="9">' + esc(commandsText) + "</td></tr>";

  document.querySelector("#pods thead").innerHTML =
    '<tr><th>项目</th><th colspan="9">状态</th></tr>';
  document.querySelector("#deployments thead").innerHTML = "";
}

function paramControl(action, param) {
  const id = "p-" + action.id + "-" + param.name;
  const attrs = ' data-param="' + esc(param.name) + '" id="' + esc(id) + '"';
  if (param.kind === "int") {
    return '<label class="param"><span>' + esc(param.label) + "</span>" +
      '<input type="number"' + attrs + ' min="' + esc(param.minimum) + '" max="' + esc(param.maximum) +
      '" value="' + esc(param.default) + '"></label>';
  }
  const options = (param.choices || []).map(function (choice) {
    return '<option value="' + esc(choice.value) + '"' +
      (String(choice.value) === String(param.default) ? " selected" : "") + ">" +
      esc(choice.label) + "</option>";
  }).join("");
  return '<label class="param"><span>' + esc(param.label) + "</span>" +
    "<select" + attrs + ">" + options + "</select></label>";
}

function selectedAgent() {
  const select = document.getElementById("agent-select");
  if (!select || select.value === "local") return null;
  return select.value;
}

function actionGuard(item) {
  if (item.blocked) return item.blocked;
  const forward = selectedAgent();
  const roles = item.roles || [];
  if (forward && roles.indexOf("edge") < 0) {
    return "主机侧操作只能在云主机上执行";
  }
  if (!forward && (actionCatalog.role === "host") && roles.length === 1 && roles[0] === "edge") {
    return "这是边缘节点操作，请在「执行位置」里选择边缘节点转发，或到边缘控制台执行";
  }
  return null;
}

function renderActions() {
  const box = document.getElementById("actions");
  const items = actionCatalog.items || [];
  const groups = actionCatalog.groups || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">当前角色没有可用操作</div>';
    return;
  }
  const parts = [];
  groups.forEach(function (group) {
    const groupItems = items.filter(function (item) { return item.group === group.id; });
    if (!groupItems.length) return;
    parts.push('<div class="action-group-title">' + esc(group.title) + "</div>");
    groupItems.forEach(function (item) {
      const blocked = actionGuard(item);
      const params = (item.params || []).map(function (param) {
        return paramControl(item, param);
      }).join("");
      const tags =
        (item.confirm ? '<span class="tag tag-confirm">需确认</span>' : "") +
        (item.requires_hardware ? '<span class="tag tag-hw">设备</span>' : "");
      parts.push(
        '<div class="action" data-id="' + esc(item.id) + '">' +
        '<div class="action-text">' +
        '<div class="action-title">' + esc(item.title) + tags + "</div>" +
        '<div class="action-desc">' + esc(item.description) + "</div>" +
        (blocked ? '<div class="action-blocked">' + esc(blocked) + "</div>" : "") +
        "</div>" +
        '<div class="action-form">' + params +
        '<button class="btn btn-primary" type="button" data-run="' + esc(item.id) + '"' +
        (blocked ? ' disabled data-blocked="1"' : "") + ">执行</button>" +
        "</div></div>"
      );
    });
  });
  box.innerHTML = parts.join("");
  const meta = document.getElementById("actions-meta");
  meta.textContent = actionCatalog.dry_run ? "演示 / dry-run：只打印命令" : "";
}

function renderTargetSelector() {
  const wrap = document.getElementById("agent-target");
  const select = document.getElementById("agent-select");
  const agents = actionCatalog.agents || [];
  if (!agents.length) {
    wrap.classList.add("hidden");
    return;
  }
  const options = ['<option value="local">本机</option>'].concat(
    agents.map(function (agent) {
      return '<option value="' + esc(agent.name) + '">' + esc(agent.name) + "（" + esc(agent.url) + "）</option>";
    })
  );
  select.innerHTML = options.join("");
  wrap.classList.remove("hidden");
}

function renderResult(data) {
  const box = document.getElementById("action-result");
  box.classList.remove("hidden");
  const head =
    '<div class="result-head">' +
    '<span class="dot ' + (data.ok ? "dot-ok" : "dot-fail") + '"></span>' +
    "<strong>" + esc(data.title || data.id || "操作") + "</strong>" +
    (data.forwarded_to ? '<span class="tag">转发到 ' + esc(data.forwarded_to) + "</span>"
      : '<span class="tag">本机执行</span>') +
    (data.dry_run ? '<span class="tag">dry-run</span>' : "") +
    (data.aborted ? '<span class="tag tag-confirm">已中断</span>' : "") +
    "</div>";
  const steps = (data.steps || []).map(function (step) {
    return '<div class="step">' +
      '<span class="dot ' + (step.ok ? "dot-ok" : "dot-fail") + '"></span>' +
      '<div class="step-body">' +
      '<div class="step-note">' + esc(step.note || "") +
      (step.optional ? ' <span class="tag">可选</span>' : "") + "</div>" +
      '<code class="step-argv">' + esc((step.argv || []).join(" ")) + "</code>" +
      (step.stdout && step.stdout.trim() ? '<pre class="step-out">' + esc(step.stdout.trim()) + "</pre>" : "") +
      (step.stderr && step.stderr.trim() ? '<pre class="step-out step-err">' + esc(step.stderr.trim()) + "</pre>" : "") +
      "</div></div>";
  }).join("");
  box.innerHTML = head + (steps || '<div class="empty">没有执行任何命令</div>');
}

function renderFailure(title, message) {
  const box = document.getElementById("action-result");
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="result-head"><span class="dot dot-fail"></span><strong>' + esc(title) + "</strong></div>" +
    '<div class="step"><div class="step-body"><pre class="step-out step-err">' + esc(message) + "</pre></div></div>";
}

async function runAction(id) {
  const item = (actionCatalog.items || []).filter(function (entry) { return entry.id === id; })[0];
  if (!item) return;
  const row = document.querySelector('.action[data-id="' + id + '"]');
  const params = {};
  if (row) {
    Array.prototype.forEach.call(row.querySelectorAll("[data-param]"), function (input) {
      params[input.getAttribute("data-param")] = input.value;
    });
  }
  if (item.confirm && !window.confirm("确定执行「" + item.title + "」？\n\n" + (item.description || ""))) {
    return;
  }
  const buttons = document.querySelectorAll("[data-run]");
  Array.prototype.forEach.call(buttons, function (button) { button.disabled = true; });
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: id,
        params: params,
        confirm: item.confirm ? true : undefined,
        agent: selectedAgent() || undefined,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    renderResult(data);
    await loadLog();
    setTimeout(load, 900);
  } catch (err) {
    renderFailure(item.title, err.message);
  } finally {
    Array.prototype.forEach.call(buttons, function (button) {
      if (button.getAttribute("data-blocked") !== "1") button.disabled = false;
    });
  }
}

async function loadActions() {
  try {
    const response = await fetch("/api/actions", { cache: "no-store" });
    if (!response.ok) return;
    actionCatalog = await response.json();
    renderTargetSelector();
    renderActions();
  } catch (err) {
    document.getElementById("actions").innerHTML =
      '<div class="empty">无法读取操作目录：' + esc(err.message) + "</div>";
  }
}

async function loadLog() {
  try {
    const response = await fetch("/api/log?limit=40", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const items = (data.items || []).slice().reverse();
    const tbody = document.querySelector("#log tbody");
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">还没有执行过命令</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(function (item) {
      const ok = item.returncode === 0;
      const time = item.time
        ? new Date(item.time).toLocaleTimeString("zh-CN", { hour12: false })
        : "-";
      return "<tr>" +
        "<td>" + esc(time) + "</td>" +
        '<td class="num"><span class="status"><span class="dot ' + (ok ? "dot-ok" : "dot-fail") +
        '"></span>' + esc(item.returncode) + "</span></td>" +
        "<td><code>" + esc((item.argv || []).join(" ")) + "</code></td>" +
        "<td>" + esc(item.error || item.stderr || item.stdout || "") + "</td>" +
        "</tr>";
    }).join("");
  } catch (err) {
    /* 记录面板失败不影响主视图 */
  }
}

async function load() {
  const errorBox = document.getElementById("error");
  try {
    const response = await fetch("/api/discovery", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();

    errorBox.classList.toggle("hidden", !data.error);
    if (data.error) errorBox.textContent = data.error;

    const modeBadge = document.getElementById("mode-badge");
    if (data.kind === "edge") {
      modeBadge.textContent = data.mode === "sim" ? "边缘模式（模拟）" : "边缘模式";
      modeBadge.className = "badge " + (data.mode === "sim" ? "badge-accent" : "badge-ok");
      renderChecks(data.checks || []);
      renderEdge(data);
    } else {
      modeBadge.textContent = data.mode === "demo" ? "主机模式（演示数据）" : "主机模式";
      modeBadge.className = "badge badge-accent";
      const metrics = (data.kubernetes || {}).metrics_available;
      const metricsBadge = document.getElementById("metrics-badge");
      metricsBadge.textContent = metrics ? "实时监控可用" : "实时监控未启用";
      metricsBadge.className = "badge " + (metrics ? "badge-ok" : "badge-warn");

      renderChecks(data.checks || []);
      renderNodes(data.nodes || []);
      renderDeployments(data.deployments || []);
      renderPods(data.pods || []);

      const summary = data.summary || {};
      document.getElementById("summary").textContent =
        "节点 " + (summary.nodes_ready || 0) + "/" + (summary.nodes_total || 0) +
        "  Ready    Pod " + (summary.pods_running || 0) + "/" + (summary.pods_total || 0) +
        "  Running    CPU 申请 " + cpu(summary.cpu_requested_cores) + " / " + cpu(summary.cpu_allocatable_cores);
      document.getElementById("nodes-meta").textContent = (data.kubernetes || {}).context || "";
      document.getElementById("nodes-panel").classList.remove("hidden");
    }

    const time = new Date(data.generated_at || Date.now());
    document.getElementById("updated").textContent =
      "更新于 " + time.toLocaleTimeString("zh-CN", { hour12: false });
  } catch (err) {
    errorBox.classList.remove("hidden");
    errorBox.textContent = "无法读取环境数据：" + err.message;
  }
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = setInterval(function () {
    if (!document.hidden) load();
  }, REFRESH_MS);
}

document.getElementById("refresh").addEventListener("click", function () {
  load();
  loadLog();
});

document.getElementById("actions").addEventListener("click", function (event) {
  const button = event.target.closest("button[data-run]");
  if (!button || button.disabled) return;
  runAction(button.getAttribute("data-run"));
});

document.getElementById("agent-select").addEventListener("change", renderActions);

document.addEventListener("visibilitychange", function () {
  if (!document.hidden) {
    load();
    loadActions();
  }
});

load();
loadActions();
loadLog();
schedule();
