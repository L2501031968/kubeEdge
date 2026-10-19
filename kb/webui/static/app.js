"use strict";

// 前端版本标记：用于确认浏览器加载的是不是最新界面（旧缓存会显示旧值）。
const UI_BUILD = "2026-10-19.4";
const REFRESH_MS = 15000;
const PAGE_SIZE_OPTIONS = [5, 10, 20, 50];
const STARTUP_COMPONENTS = [
  { name: "amari-core", label: "核心网" },
  { name: "amari-ims", label: "IMS服务" },
  { name: "amari-ran", label: "基站" },
  { name: "amari-sat", label: "卫星" },
  { name: "amari-ue", label: "UE" },
];
const SHUTDOWN_COMPONENTS = [
  ...STARTUP_COMPONENTS,
];
const STARTUP_ACTION_COMPONENTS = {
  startup_stack: ["amari-core", "amari-ims", "amari-ran", "amari-sat", "amari-ue"],
  startup_u3: ["amari-core", "amari-ims", "amari-ran", "amari-sat"],
  start_core: ["amari-core"],
  start_ims: ["amari-ims"],
  start_ran: ["amari-ran"],
  start_sat: ["amari-sat"],
};
const PAGE_ORDER = [
  "overview", "users", "modules", "resources", "instances", "deploy", "log", "settings",
];
const MODULE_TONE = {
  running: "ok",
  degraded: "warn",
  stopped: "muted",
  "not-deployed": "muted",
};
const USER_TONE = {
  connected: "dot-ok",
  active: "dot-ok",
  idle: "",
  handover: "dot-warn",
  offline: "dot-fail",
};
let timer = null;
let actionCatalog = { items: [], groups: [], agents: [] };
let deploymentItems = [];
let podItems = [];
let logItems = [];
let domainData = null;
let userItems = [];
let moduleItems = [];
let healthData = null;
let deploymentReplicas = {};
let actionPollToken = 0;
let targetSelection = "local";
let nodeAliases = {};
let returnConsoleUrl = safeConsoleUrl(new URL(window.location.href).searchParams.get("from"), true);

const listState = {
  deployments: { page: 1, size: 10, query: "", node: "", namespace: "", state: "", includeSystem: false },
  pods: { page: 1, size: 10, query: "", node: "", namespace: "", status: "", includeSystem: false },
  logs: { page: 1, size: 10, query: "", result: "", type: "" },
  users: { page: 1, size: 10, query: "", state: "", cell: "" },
  modules: { query: "", state: "" },
};

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
  if (cores === 0) return "0 核";
  const digits = Math.abs(cores) < 0.1 ? 3 : 2;
  let text = cores.toFixed(digits).replace(/\.?0+$/, "");
  if (!text || Number(text) === 0) text = String(cores);
  return text + " 核";
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

function unitValue(value, unit) {
  if (value === null || value === undefined || value === "") return "N/A";
  if (unit === "cores") return cpu(value);
  if (unit === "bytes") return mem(value);
  const number = Number(value);
  if (!Number.isFinite(number)) return "N/A";
  if (unit === "mbps") {
    return number > 0 ? String(Math.round(number * 100) / 100) + " Mbps" : "N/A";
  }
  return String(Math.round(number * 100) / 100);
}

function percentText(value) {
  if (value === null || value === undefined) return "N/A";
  const number = Number(value);
  if (!Number.isFinite(number)) return "N/A";
  return (number * 100).toFixed(number < 0.1 ? 1 : 0) + "%";
}

function metricCard(label, value, sub, tone) {
  return '<div class="metric-card' + (tone ? " metric-" + esc(tone) : "") + '">' +
    '<div class="metric-card-label">' + esc(label) + "</div>" +
    '<div class="metric-card-value">' + esc(value) + "</div>" +
    (sub ? '<div class="metric-card-sub">' + esc(sub) + "</div>" : "") +
    "</div>";
}

function moduleBadge(item) {
  return '<span class="badge badge-' + esc(MODULE_TONE[item.status] || "muted") + '">' +
    esc(item.status_label || item.status) + "</span>";
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

const SYSTEM_ROLES = ["control-plane", "master"];
const SYSTEM_NAMESPACES = ["kube-system", "kube-public", "kube-node-lease", "kubeedge"];
const BUSINESS_NAMESPACE_PREFIXES = ["amari-"];
let systemNodeNames = new Set();

function isSystemNode(node) {
  return (node.roles || []).some(function (role) {
    return SYSTEM_ROLES.indexOf(String(role).toLowerCase()) >= 0;
  });
}

function isSystemWorkload(item) {
  const namespace = String(item.namespace || "");
  if (SYSTEM_NAMESPACES.indexOf(namespace) >= 0 || namespace.indexOf("kube-") === 0) {
    return true;
  }
  // 业务命名空间的 Pod 被手工调度到控制面节点时，仍按业务条目归类。
  if (BUSINESS_NAMESPACE_PREFIXES.some(function (prefix) {
    return namespace.indexOf(prefix) === 0;
  })) {
    return false;
  }
  const names = item.nodes && item.nodes.length
    ? item.nodes
    : [item.node, item.node_alias, item.display_node];
  return names.filter(Boolean).some(function (name) {
    return systemNodeNames.has(String(name));
  });
}

function businessNodesFirst(items) {
  return (items || []).slice().sort(function (left, right) {
    return Number(isSystemWorkload(left)) - Number(isSystemWorkload(right));
  });
}

function groupDivider(items, index, predicate, columns, title) {
  if (index <= 0 || index >= items.length) return "";
  if (!predicate(items[index]) || predicate(items[index - 1])) return "";
  return '<tr class="group-row"><td colspan="' + columns + '">' + esc(title) + "</td></tr>";
}

function emptyListHint(message, hiddenSystemRows, toggleLabel) {
  if (!hiddenSystemRows) return esc(message);
  return esc(message + "；当前只剩系统条目，勾选“" + toggleLabel + "”可查看");
}

function nodeCard(node) {
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
    '<div class="node-head"><div class="node-name">' + esc(node.alias || node.display_name || node.name) +
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
}

function renderNodes(nodes) {
  const box = document.getElementById("nodes");
  systemNodeNames = new Set();
  if (!nodes || nodes.length === 0) {
    box.innerHTML = '<div class="empty">没有发现节点</div>';
    return;
  }
  nodeAliases = {};
  nodes.forEach(function (node) {
    const label = node.alias || node.display_name || node.name;
    [node.name, node.display_name].filter(Boolean).forEach(function (key) {
      nodeAliases[key] = label;
    });
    if (isSystemNode(node)) {
      [node.name, node.display_name, node.alias, label].filter(Boolean).forEach(function (key) {
        systemNodeNames.add(String(key));
      });
    }
  });
  const business = nodes.filter(function (node) { return !isSystemNode(node); });
  const system = nodes.filter(isSystemNode);
  if (!business.length || !system.length) {
    box.innerHTML = nodes.map(nodeCard).join("");
    return;
  }
  box.innerHTML =
    '<div class="node-group-title">业务节点 · ' + esc(business.length) + "</div>" +
    business.map(nodeCard).join("") +
    '<div class="node-group-title">系统节点 · ' + esc(system.length) + "</div>" +
    system.map(nodeCard).join("");
}

function displayNodeName(name) {
  if (!name) return "";
  return nodeAliases[name] || name;
}

function includesQuery(value, query) {
  if (!query) return true;
  return String(value === null || value === undefined ? "" : value)
    .toLowerCase()
    .indexOf(query.toLowerCase()) >= 0;
}

function deploymentState(item) {
  const replicas = Number(item.replicas) || 0;
  const ready = Number(item.ready) || 0;
  if (replicas === 0) return "released";
  if (ready >= replicas) return "healthy";
  return "degraded";
}

function syncSelectOptions(id, values, selected, allLabel) {
  const select = document.getElementById(id);
  if (!select) return "";
  const previous = selected === undefined || selected === null ? select.value : selected;
  select.innerHTML = '<option value="">' + esc(allLabel) + "</option>" + values.map(function (value) {
    return '<option value="' + esc(value) + '">' + esc(value) + "</option>";
  }).join("");
  const next = values.indexOf(previous) >= 0 ? previous : "";
  select.value = next;
  return next;
}

function syncOptionList(id, options, selected, allLabel) {
  const select = document.getElementById(id);
  if (!select) return "";
  const previous = selected === undefined || selected === null ? select.value : selected;
  const values = options.map(function (option) { return option.value; });
  select.innerHTML = '<option value="">' + esc(allLabel) + "</option>" + options.map(function (option) {
    return '<option value="' + esc(option.value) + '">' + esc(option.label) + "</option>";
  }).join("");
  const next = values.indexOf(previous) >= 0 ? previous : "";
  select.value = next;
  return next;
}

function syncPageSize(id, state) {
  const select = document.getElementById(id);
  if (!select) return;
  if (!select.options.length) {
    select.innerHTML = PAGE_SIZE_OPTIONS.map(function (size) {
      return '<option value="' + size + '">' + size + "</option>";
    }).join("");
  }
  select.value = String(state.size);
}

function paginate(items, state) {
  const pages = Math.max(1, Math.ceil(items.length / state.size));
  state.page = Math.min(Math.max(1, state.page), pages);
  const start = (state.page - 1) * state.size;
  return items.slice(start, start + state.size);
}

function renderPager(containerId, kind, total) {
  const box = document.getElementById(containerId);
  if (!box) return;
  const state = listState[kind];
  const pages = Math.max(1, Math.ceil(total / state.size));
  state.page = Math.min(Math.max(1, state.page), pages);
  const start = total ? (state.page - 1) * state.size + 1 : 0;
  const end = Math.min(total, state.page * state.size);
  let first = Math.max(1, state.page - 2);
  let last = Math.min(pages, first + 4);
  first = Math.max(1, last - 4);
  const pageButtons = [];
  for (let page = first; page <= last; page += 1) {
    pageButtons.push(
      '<button type="button" class="page-btn' + (page === state.page ? " active" : "") +
      '" data-page-kind="' + esc(kind) + '" data-page="' + page + '">' + page + "</button>"
    );
  }
  box.innerHTML =
    '<span class="muted">显示 ' + start + "-" + end + " / 共 " + total + " 条</span>" +
    '<span class="pager-actions">' +
    '<button type="button" class="page-btn" data-page-kind="' + esc(kind) + '" data-page="' +
    Math.max(1, state.page - 1) + '"' + (state.page <= 1 ? " disabled" : "") + ">上一页</button>" +
    pageButtons.join("") +
    '<button type="button" class="page-btn" data-page-kind="' + esc(kind) + '" data-page="' +
    Math.min(pages, state.page + 1) + '"' + (state.page >= pages ? " disabled" : "") + ">下一页</button>" +
    "</span>";
}

function renderDeployments(items) {
  deploymentItems = businessNodesFirst(items);
  const state = listState.deployments;
  const namespaces = Array.from(new Set(deploymentItems.map(function (item) {
    return item.namespace;
  }).filter(Boolean))).sort();
  const nodes = Array.from(new Set(deploymentItems.reduce(function (all, item) {
    return all.concat(item.nodes || []);
  }, []).filter(Boolean).map(displayNodeName))).sort();
  state.namespace = syncSelectOptions("deploy-namespace", namespaces, state.namespace, "全部");
  state.node = syncSelectOptions("deploy-node", nodes, state.node, "全部");
  syncPageSize("deploy-page-size", state);

  const filtered = deploymentItems.filter(function (item) {
    const itemNodes = (item.nodes || []).map(displayNodeName);
    if (!state.includeSystem && isSystemWorkload(item)) return false;
    if (state.namespace && item.namespace !== state.namespace) return false;
    if (state.node && itemNodes.indexOf(state.node) < 0) return false;
    if (state.state && deploymentState(item) !== state.state) return false;
    return includesQuery([
      item.name,
      item.namespace,
      itemNodes.join(" "),
      (item.images || []).join(" "),
    ].join(" "), state.query);
  });
  const visible = paginate(filtered, state);
  const tbody = document.querySelector("#deployments tbody");
  if (!visible.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">' + emptyListHint(
      "没有匹配的工作负载",
      !state.includeSystem && deploymentItems.some(isSystemWorkload),
      "显示系统工作负载",
    ) + "</td></tr>";
    renderPager("deployments-pager", "deployments", filtered.length);
    return;
  }
  tbody.innerHTML = visible.map(function (item, index) {
    const itemNodes = (item.nodes || []).map(displayNodeName);
    const divider = groupDivider(visible, index, isSystemWorkload, 8, "系统工作负载");
    return (
      divider + "<tr>" +
      "<td>" + esc(item.name) + "</td>" +
      "<td>" + esc(item.namespace) + "</td>" +
      '<td class="num">' + esc(item.ready) + " / " + esc(item.replicas) + "</td>" +
      '<td class="num">' + cpu(item.cpu_request_per_pod_cores) + "</td>" +
      '<td class="num">' + cpu(item.cpu_limit_per_pod_cores) + "</td>" +
      '<td class="num">' + cpu(item.cpu_request_total_cores) + "</td>" +
      "<td>" + esc(itemNodes.join(", ") || "-") + "</td>" +
      "<td>" + esc((item.images || []).join(", ") || "-") + "</td>" +
      "</tr>"
    );
  }).join("");
  renderPager("deployments-pager", "deployments", filtered.length);
}

function renderPods(items) {
  podItems = businessNodesFirst(items);
  const state = listState.pods;
  const namespaces = Array.from(new Set(podItems.map(function (item) {
    return item.namespace;
  }).filter(Boolean))).sort();
  const nodes = Array.from(new Set(podItems.map(function (item) {
    return item.node_alias || displayNodeName(item.display_node || item.node);
  }).filter(Boolean))).sort();
  const statuses = Array.from(new Set(podItems.map(function (item) {
    return item.status;
  }).filter(Boolean))).sort();
  state.namespace = syncSelectOptions("pod-namespace", namespaces, state.namespace, "全部");
  state.node = syncSelectOptions("pod-node", nodes, state.node, "全部");
  state.status = syncSelectOptions("pod-status", statuses, state.status, "全部");
  syncPageSize("pod-page-size", state);

  const filtered = podItems.filter(function (item) {
    const itemNode = item.node_alias || displayNodeName(item.display_node || item.node);
    if (!state.includeSystem && isSystemWorkload(item)) return false;
    if (state.namespace && item.namespace !== state.namespace) return false;
    if (state.node && itemNode !== state.node) return false;
    if (state.status && item.status !== state.status) return false;
    return includesQuery([
      item.name,
      item.namespace,
      itemNode,
      item.node,
      item.deployment,
    ].join(" "), state.query);
  });
  const visible = paginate(filtered, state);
  const tbody = document.querySelector("#pods tbody");
  if (!visible.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">' + emptyListHint(
      "没有匹配的 Pod",
      !state.includeSystem && podItems.some(isSystemWorkload),
      "显示系统 Pod",
    ) + "</td></tr>";
    renderPager("pods-pager", "pods", filtered.length);
    return;
  }
  tbody.innerHTML = visible.map(function (item, index) {
    const running = item.status === "Running";
    const dot = running ? "dot-ok" : "dot-warn";
    const itemNode = item.node_alias || displayNodeName(item.display_node || item.node);
    const divider = groupDivider(visible, index, isSystemWorkload, 10, "系统 Pod");
    return (
      divider + "<tr>" +
      "<td>" + esc(item.name) + "</td>" +
      "<td>" + esc(item.namespace) + "</td>" +
      "<td>" + esc(itemNode) + "</td>" +
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
  renderPager("pods-pager", "pods", filtered.length);
}

function clearDomainPanels() {
  [
    "overview-metrics",
    "overview-modules",
    "users-summary",
    "users-ue-pods",
    "modules",
    "module-chart",
    "resource-summary",
  ].forEach(function (id) {
    const box = document.getElementById(id);
    if (box) box.innerHTML = "";
  });
  ["#users tbody", "#resource-nodes tbody"].forEach(function (selector) {
    const tbody = document.querySelector(selector);
    if (tbody) tbody.innerHTML = "";
  });
  ["users-meta", "users-note", "modules-meta", "resources-meta", "resource-notes"].forEach(function (id) {
    const node = document.getElementById(id);
    if (node) node.textContent = "";
  });
  const pager = document.getElementById("users-pager");
  if (pager) pager.innerHTML = "";
}

function moduleResourceRow(row) {
  const known = row.ratio !== null && row.ratio !== undefined;
  const ratio = known ? Math.max(0, Math.min(Number(row.ratio), 1)) : 0;
  let value = unitValue(row.request, row.unit) + " / " + unitValue(row.pool, row.unit);
  if (row.used !== null && row.used !== undefined) {
    value += "（实时 " + unitValue(row.used, row.unit) + "）";
  }
  return (
    '<div class="module-resource">' +
    '<span class="metric-label">' + esc(row.label) + "</span>" +
    '<div class="bar"><div class="bar-fill ' + (known ? ratioClass(ratio) : "") +
    '" style="width:' + (known ? (ratio * 100).toFixed(1) : "0") + '%"></div></div>' +
    '<span class="metric-value">' + esc(value) + "</span>" +
    "</div>"
  );
}

function moduleCard(item) {
  const components = (item.components || []).map(function (component) {
    const label = component.missing
      ? component.label + "（未接入）"
      : component.label + " " + component.ready + "/" + component.replicas;
    return '<span class="component-chip' + (component.missing ? " missing" : "") + '">' +
      esc(label) + "</span>";
  }).join("");
  const resources = (item.resources || []).map(moduleResourceRow).join("");
  const pods = item.pods_total
    ? "Pod " + item.pods_running + "/" + item.pods_total + " Running"
    : "暂无 Pod";
  const nodes = (item.nodes || []).length
    ? "节点 " + item.nodes.join("、")
    : "未调度到节点";
  return (
    '<div class="module-card">' +
    '<div class="module-head">' +
    "<div>" +
    '<div class="module-title">' + esc(item.title) + "</div>" +
    '<div class="module-subtitle">' + esc(item.subtitle) + "</div>" +
    "</div>" +
    '<span class="module-status">' + moduleBadge(item) + "</span>" +
    "</div>" +
    '<div class="module-desc">' + esc(item.description) + "</div>" +
    (components ? '<div class="module-components">' + components + "</div>" : "") +
    resources +
    '<div class="module-pods">' + esc(pods) + " · " + esc(nodes) + "</div>" +
    '<div class="module-foot">' +
    "<span>" + esc(item.metric_hint || "") + "</span>" +
    "<span>副本 " + esc(item.ready) + "/" + esc(item.replicas) + "</span>" +
    "</div></div>"
  );
}

function moduleMatches(item, state) {
  if (state.state && item.status !== state.state) return false;
  const haystack = [item.title, item.subtitle, item.description]
    .concat((item.components || []).map(function (component) {
      return component.name + " " + component.label;
    }))
    .concat(item.nodes || [])
    .join(" ");
  return includesQuery(haystack, state.query);
}

function renderModuleChart() {
  const box = document.getElementById("module-chart");
  if (!box) return;
  const summary = (domainData && domainData.extra_summary) || {};
  const total = Number(summary.cpu_allocatable_cores) || 0;
  const items = ((domainData && domainData.modules) || []).filter(function (item) {
    return Number(item.cpu_request_cores) > 0;
  });
  if (!total || !items.length) {
    box.innerHTML = '<div class="empty">暂无可对比的 CPU 申请量</div>';
    return;
  }
  box.innerHTML = items.map(function (item) {
    const request = Number(item.cpu_request_cores) || 0;
    const ratio = Math.min(request / total, 1);
    return (
      '<div class="chart-row">' +
      '<div class="chart-label" title="' + esc(item.title) + '">' + esc(item.title) + "</div>" +
      '<div class="chart-track"><div class="chart-fill ' + ratioClass(ratio) +
      '" style="width:' + (ratio * 100).toFixed(1) + '%"></div></div>' +
      '<div class="chart-value">' + esc(cpu(request) + " / " + cpu(total) + "（" +
        percentText(ratio) + "）") + "</div>" +
      "</div>"
    );
  }).join("");
}

function renderModules() {
  const state = listState.modules;
  const items = (domainData && domainData.modules) || [];
  const filtered = items.filter(function (item) { return moduleMatches(item, state); });
  const running = items.filter(function (item) { return item.status === "running"; }).length;
  const meta = document.getElementById("modules-meta");
  if (meta) {
    meta.textContent = "共 " + items.length + " 个模块，运行中 " + running +
      "，当前显示 " + filtered.length + " 个";
  }
  const box = document.getElementById("modules");
  if (box) {
    box.innerHTML = filtered.length
      ? filtered.map(moduleCard).join("")
      : '<div class="empty">没有匹配的业务模块</div>';
  }
  renderModuleChart();
}

function renderModuleOverview() {
  const box = document.getElementById("overview-modules");
  if (!box) return;
  const items = (domainData && domainData.modules) || [];
  box.innerHTML = items.map(function (item) {
    return '<span class="module-chip">' +
      '<span class="dot ' + (MODULE_TONE[item.status] === "ok" ? "dot-ok" : "dot-warn") + '"></span>' +
      "<strong>" + esc(item.title) + "</strong>" +
      "<span>" + esc(item.status_label) + "</span>" +
      "</span>";
  }).join("");
}

function userStateOptions(users) {
  const seen = {};
  const options = [];
  users.forEach(function (user) {
    // connected / active 都显示为「在线」，按展示名归并，避免下拉里出现重复项。
    const label = user.state_label || user.state;
    if (!label || seen[label]) return;
    seen[label] = true;
    options.push({ value: label, label: label });
  });
  return options.sort(function (left, right) {
    return left.label.localeCompare(right.label, "zh-CN");
  });
}

function userPods(items) {
  if (!items || !items.length) {
    return '<div class="empty">未发现 UE 模块 Pod</div>';
  }
  return items.map(function (pod) {
    return '<span class="module-chip">' +
      "<strong>" + esc(pod.name) + "</strong>" +
      "<span>" + esc(pod.status) + "</span>" +
      "<span>" + esc(pod.node || "-") + "</span>" +
      "<span>CPU 申请 " + esc(unitValue(pod.cpu_request_cores, "cores")) + "</span>" +
      "</span>";
  }).join("");
}

function numberText(value, suffix, digits) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(digits === undefined ? 1 : digits).replace(/\.0$/, "") +
    (suffix || "");
}

function renderUsers() {
  const users = (domainData && domainData.users) || null;
  const summary = document.getElementById("users-summary");
  const note = document.getElementById("users-note");
  const unavailable = document.getElementById("users-unavailable");
  const listPanel = document.getElementById("users-list-panel");
  const meta = document.getElementById("users-meta");
  const pager = document.getElementById("users-pager");
  if (!users) {
    if (summary) summary.innerHTML = "";
    if (note) note.textContent = "等待业务视图数据";
    return;
  }

  const cards = [];
  if (!users.available) {
    cards.push(metricCard("在线用户", "未接入", "平台不伪造用户数", ""));
    cards.push(metricCard(
      "接入容量",
      users.capacity === null || users.capacity === undefined ? "N/A" : users.capacity + " 用户",
      "来自 kb.json 的 ue_capacity",
      ""
    ));
    cards.push(metricCard(
      "UE Pod",
      String(users.ue_pod_count || 0),
      "UE 模块运行中的 Pod",
      users.ue_pod_count ? "ok" : ""
    ));
  } else {
    const online = users.online === null || users.online === undefined ? "N/A" : users.online;
    const capacity = users.capacity ? " / " + users.capacity : "";
    cards.push(metricCard("在线用户", String(online) + capacity, users.detail || "", "ok"));
    cards.push(metricCard("会话总数", users.sessions === null ? "N/A" : String(users.sessions), "来自用户数据源", ""));
    cards.push(metricCard(
      "总带宽",
      users.bandwidth_mbps === null ? "N/A" : numberText(users.bandwidth_mbps, " Mbps"),
      "所有用户合计",
      ""
    ));
    cards.push(metricCard(
      "平均 PRB",
      users.prb_percent === null ? "N/A" : numberText(users.prb_percent, "%"),
      "按已有 PRB 记录平均",
      ""
    ));
    cards.push(metricCard(
      "单用户 CPU 折算",
      users.per_user_cpu_request_cores === null
        ? "N/A"
        : cpu(users.per_user_cpu_request_cores),
      "按 UE Pod 申请量除以在线用户数",
      ""
    ));
    cards.push(metricCard(
      "单用户内存折算",
      users.per_user_mem_request_bytes === null
        ? "N/A"
        : mem(users.per_user_mem_request_bytes),
      "按 UE Pod 申请量除以在线用户数",
      ""
    ));
    cards.push(metricCard("UE Pod", String(users.ue_pod_count || 0), "承载用户接入的 Pod", ""));
  }
  if (summary) summary.innerHTML = cards.join("");

  const generated = users.generated_at
    ? "数据时间 " + new Date(users.generated_at).toLocaleString("zh-CN", { hour12: false })
    : "";
  if (note) {
    note.textContent = [users.share_note, generated].filter(Boolean).join(" · ");
  }
  if (meta) {
    meta.textContent = users.available ? (users.detail || "") : "";
  }

  if (!users.available) {
    if (unavailable) unavailable.classList.remove("hidden");
    if (listPanel) listPanel.classList.add("hidden");
    const pods = document.getElementById("users-ue-pods");
    if (pods) pods.innerHTML = userPods(users.ue_pods || []);
    if (pager) pager.innerHTML = "";
    return;
  }
  if (unavailable) unavailable.classList.add("hidden");
  if (listPanel) listPanel.classList.remove("hidden");

  const state = listState.users;
  const items = users.users || [];
  userItems = items.slice();
  state.state = syncOptionList("user-state", userStateOptions(items), state.state, "全部");
  state.cell = syncOptionList(
    "user-cell",
    (users.cells || []).map(function (cell) { return { value: cell, label: cell }; }),
    state.cell,
    "全部"
  );
  syncPageSize("user-page-size", state);

  const filtered = items.filter(function (user) {
    if (state.state && (user.state_label || user.state) !== state.state) return false;
    if (state.cell && user.cell !== state.cell) return false;
    return includesQuery([
      user.id,
      user.state_label,
      user.cell,
      user.pod,
      user.node_alias,
      user.node,
      user.ip,
      user.note,
    ].join(" "), state.query);
  });
  const visible = paginate(filtered, state);
  const tbody = document.querySelector("#users tbody");
  if (!tbody) return;
  if (!visible.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">没有匹配的用户</td></tr>';
    renderPager("users-pager", "users", filtered.length);
    return;
  }
  tbody.innerHTML = visible.map(function (user) {
    const podCell = user.pod
      ? esc(user.pod) + (user.node_alias || user.node
        ? '<div class="muted">' + esc(user.node_alias || user.node) + "</div>"
        : "")
      : "-";
    return (
      "<tr>" +
      "<td>" + esc(user.id) + "</td>" +
      '<td><span class="status"><span class="dot ' + esc(USER_TONE[user.state] || "") + '"></span>' +
      esc(user.state_label) + "</span></td>" +
      '<td class="num">' + esc(user.sessions === null || user.sessions === undefined ? "-" : user.sessions) + "</td>" +
      '<td class="num">' + esc(user.bandwidth_mbps === null ? "-" : numberText(user.bandwidth_mbps, " Mbps")) + "</td>" +
      '<td class="num">' + esc(user.prb_percent === null ? "-" : numberText(user.prb_percent, "%")) + "</td>" +
      '<td class="num">' + esc(user.rsrp_dbm === null || user.rsrp_dbm === undefined ? "-" : user.rsrp_dbm + " dBm") + "</td>" +
      "<td>" + esc(user.cell || "-") + "</td>" +
      "<td>" + podCell + "</td>" +
      "<td>" + esc(user.ip || "-") + "</td>" +
      "<td>" + esc(user.note || "-") + "</td>" +
      "</tr>"
    );
  }).join("");
  renderPager("users-pager", "users", filtered.length);
}

function poolCard(pool) {
  const hasRequest = pool.requested !== null && pool.requested !== undefined;
  const value = hasRequest
    ? unitValue(pool.requested, pool.unit) + " / " + unitValue(pool.allocatable, pool.unit)
    : unitValue(pool.allocatable, pool.unit);
  const sub = [];
  if (hasRequest) {
    sub.push("可调度剩余 " + unitValue(pool.free_schedulable, pool.unit));
  }
  if (pool.used !== null && pool.used !== undefined) {
    sub.push("实际使用 " + unitValue(pool.used, pool.unit));
  }
  if (pool.free_actual !== null && pool.free_actual !== undefined) {
    sub.push("实际空闲 " + unitValue(pool.free_actual, pool.unit));
  }
  if (!sub.length) sub.push(pool.source || "");
  const tone = hasRequest && Number(pool.allocatable) > 0
    ? ratioClass(Number(pool.requested) / Number(pool.allocatable))
    : "";
  return metricCard(
    pool.label + "（申请 / 可分配）",
    value,
    sub.join(" · "),
    tone
  );
}

function acceleratorCell(node) {
  const accelerators = node.accelerators || [];
  if (!accelerators.length) {
    return '<span class="muted">未上报</span>';
  }
  return accelerators.map(function (item) {
    return '<span class="component-chip">' + esc(item.label) + " " +
      esc(numberText(item.requested, "")) + "/" + esc(numberText(item.allocatable, "")) + "</span>";
  }).join(" ");
}

function renderResources() {
  const resources = (domainData && domainData.resources) || null;
  const summary = document.getElementById("resource-summary");
  const notes = document.getElementById("resource-notes");
  const meta = document.getElementById("resources-meta");
  if (!resources) {
    if (summary) summary.innerHTML = "";
    if (notes) notes.textContent = "等待业务视图数据";
    return;
  }
  if (summary) summary.innerHTML = (resources.pools || []).map(poolCard).join("");
  if (meta) {
    meta.textContent = "Pod " + esc(resources.pod_count || 0) + " 个 · 节点 " +
      esc((resources.nodes || []).length) + " 个";
  }
  if (notes) {
    const parts = (resources.sources || []).slice();
    if ((resources.missing_accelerators || []).length) {
      parts.push("未上报加速资源：" + resources.missing_accelerators.join("、"));
    }
    if ((resources.arch_override_unmatched || []).length) {
      parts.push(
        "架构覆盖未匹配到节点：" + resources.arch_override_unmatched.join("、") +
        "（请检查 kb.json 的 node_arch_overrides，key 用节点名或 kb.local/name 标签值）"
      );
    }
    notes.textContent = parts.join("；");
  }

  const tbody = document.querySelector("#resource-nodes tbody");
  if (!tbody) return;
  const nodes = resources.nodes || [];
  if (!nodes.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty">没有发现节点</td></tr>';
    return;
  }
  const ordered = nodes.slice().sort(function (left, right) {
    return Number(isSystemNode(left)) - Number(isSystemNode(right));
  });
  tbody.innerHTML = ordered.map(function (node, index) {
    const roles = (node.roles || []).join("、");
    const addresses = ((node.network || {}).addresses || []).map(function (address) {
      return address.address;
    }).filter(Boolean).join(", ");
    const divider = groupDivider(ordered, index, isSystemNode, 11, "系统节点");
    return (
      divider + "<tr>" +
      "<td>" + esc(node.alias || node.name) +
      '<div class="muted">' + esc(roles || "无角色") + " · Pod " + esc(node.pods) + " 个</div></td>" +
      '<td><span class="status"><span class="dot ' + (node.ready ? "dot-ok" : "dot-fail") +
      '"></span>' + (node.ready ? "Ready" : "NotReady") + "</span></td>" +
      '<td class="num">' + cpu((node.cpu || {}).allocatable) + "</td>" +
      '<td class="num">' + cpu((node.cpu || {}).requested) + "</td>" +
      '<td class="num">' + cpu((node.cpu || {}).used) + "</td>" +
      '<td class="num">' + mem((node.memory || {}).allocatable) + "</td>" +
      '<td class="num">' + mem((node.memory || {}).requested) + "</td>" +
      '<td class="num">' + mem((node.memory || {}).used) + "</td>" +
      "<td>" + acceleratorCell(node) + "</td>" +
      "<td>" + esc(unitValue((node.network || {}).bandwidth_mbps, "mbps")) +
      (addresses ? '<div class="muted">' + esc(addresses) + "</div>" : "") + "</td>" +
      "<td>" + esc([node.arch, node.os].filter(Boolean).join(" / ") || "-") + "</td>" +
      "</tr>"
    );
  }).join("");
}

function renderSettings() {
  const box = document.getElementById("settings-runtime");
  if (!box) return;
  const health = healthData || {};
  const items = [];
  function add(label, value) {
    items.push(
      '<div class="settings-item"><div class="settings-label">' + esc(label) + "</div>" +
      '<div class="settings-value">' + esc(value) + "</div></div>"
    );
  }
  add("控制台角色", health.role === "edge" ? "边缘端（edge）" : "云主机（host）");
  add("运行模式", health.mode || "-");
  add("命令模式", health.dry_run ? "dry-run（只打印命令）" : "真实执行");
  add("允许真实硬件操作", health.allow_real_hardware ? "是" : "否");
  add("界面版本", UI_BUILD);
  if (actionCatalog.role === "edge") {
    add("云主机控制台", actionCatalog.host_console || "未配置 host_console");
  } else {
    const agents = actionCatalog.agents || [];
    add(
      "已登记边缘控制台",
      agents.length
        ? agents.map(function (agent) { return agent.name + "（" + agent.url + "）"; }).join("，")
        : "未配置 agents"
    );
  }
  add("用户数据源", actionCatalog.user_source || "未配置（用户接入页显示未接入）");
  add("用户接入容量", (actionCatalog.ue_capacity === null || actionCatalog.ue_capacity === undefined
    ? "-" : actionCatalog.ue_capacity + " 用户"));
  box.innerHTML = items.join("");
}

function renderOverviewDomain(data) {
  const summary = (domainData && domainData.extra_summary) || {};
  const cluster = data.summary || {};
  const cards = [];
  const modulesTotal = Number(summary.modules_total) || 0;
  const modulesRunning = Number(summary.modules_running) || 0;
  const modulesDegraded = Number(summary.modules_degraded) || 0;
  const modulesAbsent = Number(summary.modules_absent) || 0;
  const modulesSub = [];
  if (modulesDegraded) modulesSub.push(modulesDegraded + " 个异常");
  if (modulesAbsent) modulesSub.push(modulesAbsent + " 个未接入");
  cards.push(metricCard(
    "业务模块",
    modulesRunning + " / " + modulesTotal,
    modulesSub.length ? modulesSub.join(" · ") : "全部运行中",
    modulesTotal && modulesRunning === modulesTotal ? "ok" : "warn"
  ));
  if (summary.users_available) {
    cards.push(metricCard(
      "在线用户",
      (summary.users_online === null ? "N/A" : summary.users_online) +
        (summary.users_capacity ? " / " + summary.users_capacity : ""),
      "来自用户数据源",
      "ok"
    ));
  } else {
    cards.push(metricCard("在线用户", "未接入", "配置 kb.json 的 user_source 后显示", ""));
  }
  const cpuAlloc = Number(summary.cpu_allocatable_cores) || 0;
  const cpuRequest = Number(summary.cpu_requested_cores) || 0;
  cards.push(metricCard(
    "CPU 申请 / 可分配",
    cpu(cpuRequest) + " / " + cpu(cpuAlloc),
    "可调度剩余 " + cpu(summary.cpu_free_schedulable_cores) +
      (summary.cpu_used_cores === null || summary.cpu_used_cores === undefined
        ? "" : " · 实时使用 " + cpu(summary.cpu_used_cores)),
    cpuAlloc > 0 ? ratioClass(cpuRequest / cpuAlloc) : ""
  ));
  const memAlloc = Number(summary.mem_allocatable_bytes) || 0;
  const memRequest = Number(summary.mem_requested_bytes) || 0;
  cards.push(metricCard(
    "内存申请 / 可分配",
    mem(memRequest) + " / " + mem(memAlloc),
    "可调度剩余 " + mem(summary.mem_free_schedulable_bytes),
    memAlloc > 0 ? ratioClass(memRequest / memAlloc) : ""
  ));
  const acceleratorLabels = summary.accelerator_labels || [];
  cards.push(metricCard(
    "加速资源",
    acceleratorLabels.length ? String(summary.accelerator_total || 0) + " 张" : "未上报",
    acceleratorLabels.length ? acceleratorLabels.join("、") : "集群节点未上报 GPU / NPU / FPGA 等扩展资源",
    ""
  ));
  const metrics = document.getElementById("overview-metrics");
  if (metrics) metrics.innerHTML = cards.join("");

  const meta = document.getElementById("overview-meta");
  if (meta) {
    meta.textContent = "节点 " + (cluster.nodes_ready || 0) + "/" + (cluster.nodes_total || 0) +
      " Ready · Pod " + (cluster.pods_running || 0) + "/" + (cluster.pods_total || 0) + " Running";
  }
  renderModuleOverview();

  const note = document.getElementById("overview-domain-note");
  if (note) {
    const parts = [];
    if (domainData.users && domainData.users.available) {
      parts.push("用户数据源已接入（" + domainData.users.detail + "）");
    } else {
      parts.push("用户数据源未接入，用户接入页不显示在线人数");
    }
    if (domainData.user_source_error) parts.push(domainData.user_source_error);
    note.textContent = parts.join(" · ");
  }
}

function renderDomain(data) {
  const domain = data && data.domain ? data.domain : null;
  if (!domain) {
    clearDomainPanels();
    const metrics = document.getElementById("overview-metrics");
    if (metrics) {
      metrics.innerHTML = metricCard(
        "业务视图",
        "不可用",
        data && data.domain_error ? data.domain_error : "等待集群数据",
        "warn"
      );
    }
    const note = document.getElementById("overview-domain-note");
    if (note) note.textContent = data && data.domain_error ? data.domain_error : "";
    return;
  }
  domainData = domain;
  renderOverviewDomain(data);
  renderUsers();
  renderModules();
  renderResources();
  renderSettings();
}

function renderEdge(data) {
  const agent = data.agent || {};
  const services = agent.services || {};
  const commands = agent.commands || {};
  const devices = agent.devices || {};

  const edgeNote = "边缘端只负责设备与状态类命令，业务视图请在云主机控制台查看。";
  ["users-note", "modules-meta", "resources-meta", "overview-domain-note"].forEach(function (id) {
    const node = document.getElementById(id);
    if (node) node.textContent = edgeNote;
  });
  ["overview-metrics", "users-summary", "modules", "resource-summary"].forEach(function (id) {
    const node = document.getElementById(id);
    if (node) node.innerHTML = '<div class="empty">' + esc(edgeNote) + "</div>";
  });
  const moduleChart = document.getElementById("module-chart");
  if (moduleChart) moduleChart.innerHTML = '<div class="empty">' + esc(edgeNote) + "</div>";
  const usersList = document.getElementById("users-list-panel");
  if (usersList) usersList.classList.add("hidden");
  const usersUnavailable = document.getElementById("users-unavailable");
  if (usersUnavailable) usersUnavailable.classList.add("hidden");

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

function rememberDeploymentReplicas(items) {
  deploymentReplicas = {};
  (items || []).forEach(function (item) {
    deploymentReplicas[item.name] = Number(item.replicas) || 0;
  });
  syncScaleReplicas(false);
}

function syncScaleReplicas(force) {
  const row = document.querySelector('.action[data-id="scale_component"]');
  if (!row) return;
  const component = row.querySelector('[data-param="component"]');
  const input = row.querySelector('[data-param="replicas"]');
  if (!component || !input) return;
  const replicas = deploymentReplicas[component.value];
  if (!Number.isFinite(replicas)) return;
  if (force || input.getAttribute("data-dirty") !== "1") {
    input.value = String(replicas);
  }
}

function safeConsoleUrl(value, sameHostOnly) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.href);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password) return null;
    if (sameHostOnly && url.hostname !== window.location.hostname) return null;
    return url.toString();
  } catch (err) {
    return null;
  }
}

function navigationTargets() {
  const seen = {};
  const targets = [];
  const here = window.location.origin + window.location.pathname;
  function add(name, label, raw, sameHostOnly) {
    const url = safeConsoleUrl(raw, sameHostOnly);
    if (!url) return;
    const parsed = new URL(url);
    const key = parsed.origin + parsed.pathname;
    if (key === here || seen[key]) return;
    seen[key] = true;
    targets.push({ name: name, label: label, url: url });
  }
  if (actionCatalog.role === "edge") {
    // 边缘控制台只需要回云主机；agent 列表在这边往往指向自己，不能列出来。
    add("host-console", "云主机控制台", actionCatalog.host_console, false);
    add("source-console", "云主机控制台（来源页面）", returnConsoleUrl, true);
    return targets;
  }
  add("source-console", "边缘控制台（来源页面）", returnConsoleUrl, true);
  (actionCatalog.agents || []).forEach(function (agent) {
    add(agent.name, agent.name + "（" + agent.url + "）", agent.url, false);
  });
  return targets;
}

function selectedTarget() {
  if (targetSelection === "local") return null;
  return navigationTargets().filter(function (target) {
    return target.url === targetSelection;
  })[0] || null;
}

function localTargetLabel() {
  return actionCatalog.role === "edge" ? "本机（边缘端）" : "本机（云主机）";
}

function actionGuard(item) {
  if (item.blocked) return item.blocked;
  const target = selectedTarget();
  const roles = item.roles || [];
  if (target) {
    return "当前已选择其他控制台。请在刚打开的新标签页中操作，或切回本机后执行本地命令";
  }
  if (actionCatalog.role === "host" && roles.length === 1 && roles[0] === "edge") {
    return "这是边缘节点操作，请在「执行位置」里选择边缘控制台跳转，或直接到边缘机器上执行";
  }
  return null;
}

function renderActions() {
  const box = document.getElementById("actions");
  const items = actionCatalog.items || [];
  const groups = actionCatalog.groups || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">当前角色没有可用操作</div>';
    renderHealthActions();
    return;
  }
  const parts = [];
  groups.forEach(function (group) {
    const groupItems = items.filter(function (item) {
      return item.group === group.id && item.id !== "prepare_node";
    });
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
  syncScaleReplicas(false);
  const meta = document.getElementById("actions-meta");
  meta.textContent = actionCatalog.dry_run ? "演示 / dry-run：只打印命令" : "";
  renderHealthActions();
}

function renderHealthActions() {
  const box = document.getElementById("checks-actions");
  if (!box) return;
  const item = (actionCatalog.items || []).filter(function (entry) {
    return entry.id === "prepare_node";
  })[0];
  if (!item) {
    box.innerHTML = "";
    box.classList.add("hidden");
    return;
  }
  const blocked = actionGuard(item);
  box.classList.remove("hidden");
  box.innerHTML =
    '<button class="btn btn-primary" type="button" data-run="prepare_node" data-default-label="' +
    esc(item.title) + '"' +
    (blocked ? ' disabled data-blocked="1"' : "") + ">" + esc(item.title) + "</button>" +
    (blocked ? '<span class="action-blocked">' + esc(blocked) + "</span>" : "");
}

function renderTargetSelector() {
  const wrap = document.getElementById("agent-target");
  const select = document.getElementById("agent-select");
  const targets = navigationTargets();
  if (!targets.length) {
    if (actionCatalog.role === "edge") {
      select.innerHTML =
        '<option value="local">' + localTargetLabel() + "</option>" +
        '<option value="" disabled>未配置 host_console，暂时无法跳回云主机</option>';
      select.value = "local";
      targetSelection = "local";
      wrap.classList.remove("hidden");
      return;
    }
    wrap.classList.add("hidden");
    return;
  }
  const options = ['<option value="local">' + localTargetLabel() + "</option>"].concat(
    targets.map(function (target) {
      return '<option value="' + esc(target.url) + '">' + esc(target.label) + "</option>";
    })
  );
  select.innerHTML = options.join("");
  const selected = targets.some(function (target) { return target.url === targetSelection; })
    ? targetSelection
    : "local";
  select.value = selected;
  targetSelection = selected;
  wrap.classList.remove("hidden");
}

function consoleNavigationUrl(raw) {
  const url = new URL(raw, window.location.href);
  if (["127.0.0.1", "localhost", "::1"].indexOf(url.hostname) >= 0) {
    url.hostname = window.location.hostname;
  }
  url.searchParams.set("from", window.location.origin);
  return url.toString();
}

function renderPending(title, message) {
  const box = document.getElementById("action-result");
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="result-head"><span class="dot dot-warn"></span><strong>' + esc(title) + "</strong></div>" +
    '<div class="progress-line"><span class="spinner"></span><span>' + esc(message) + "</span></div>";
}

function renderNotice(title, message) {
  const box = document.getElementById("action-result");
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="result-head"><span class="dot dot-ok"></span><strong>' + esc(title) + "</strong></div>" +
    '<div class="result-notice">' + esc(message) + "</div>";
}

function deploymentMap(items) {
  const result = {};
  (items || []).forEach(function (item) {
    result[item.name] = item;
  });
  return result;
}

function componentRecovery(snapshot, component) {
  if (!snapshot) {
    return { recovered: false, detail: "等待集群状态" };
  }
  const item = (snapshot.deployments || {})[component.name];
  const pods = (snapshot.pods || []).filter(function (pod) {
    return pod.deployment === component.name && pod.status !== "Succeeded";
  });
  const replicas = item ? Number(item.replicas) || 0 : 0;
  const ready = item ? Number(item.ready) || 0 : 0;
  const scaledDown = !item || replicas === 0 && ready === 0;
  if (!item && !pods.length) return { recovered: true, detail: "未发现，视为已回收" };
  if (!scaledDown) {
    return { recovered: false, detail: "副本 " + replicas + "，就绪 " + ready };
  }
  if (pods.length) {
    return { recovered: false, detail: "Pod 清理中 " + pods.length + " 个" };
  }
  return { recovered: true, detail: "已回收" };
}

function recoveryCount(snapshot) {
  return SHUTDOWN_COMPONENTS.filter(function (component) {
    return componentRecovery(snapshot, component).recovered;
  }).length;
}

function componentStartup(snapshot, component) {
  if (!snapshot) {
    return { ready: false, detail: "等待集群状态" };
  }
  const item = (snapshot.deployments || {})[component.name];
  if (!item) {
    return { ready: false, detail: "等待 Deployment" };
  }
  const replicas = Number(item.replicas) || 0;
  const ready = Number(item.ready) || 0;
  const pods = (snapshot.pods || []).filter(function (pod) {
    return pod.deployment === component.name;
  });
  const running = pods.filter(function (pod) {
    return pod.status === "Running";
  }).length;
  if (replicas < 1) {
    return { ready: false, detail: "副本仍为 0" };
  }
  if (ready < 1 || running < 1) {
    return {
      ready: false,
      detail: "副本 " + ready + "/" + replicas + "，Running Pod " + running,
    };
  }
  return { ready: true, detail: "副本 " + ready + "/" + replicas + "，Pod Running" };
}

function startupCount(snapshot, components) {
  return components.filter(function (component) {
    return componentStartup(snapshot, component).ready;
  }).length;
}

function renderRecoveryStatus(title, snapshot, message, done) {
  const box = document.getElementById("action-result");
  const count = recoveryCount(snapshot);
  const componentRows = SHUTDOWN_COMPONENTS.map(function (component) {
    const recovery = componentRecovery(snapshot, component);
    return '<div class="recovery-item">' +
      '<span class="dot ' + (recovery.recovered ? "dot-ok" : "dot-warn") + '"></span>' +
      '<strong>' + esc(component.label) + "</strong>" +
      '<span class="muted">' + esc(recovery.detail) + "</span></div>";
  }).join("");
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="result-head">' +
    '<span class="dot ' + (done ? "dot-ok" : "dot-warn") + '"></span>' +
    "<strong>" + esc(title) + "</strong>" +
    '<span class="tag">' + count + "/" + SHUTDOWN_COMPONENTS.length + " 已回收</span>" +
    "</div>" +
    '<div class="progress-line">' + (done ? "" : '<span class="spinner"></span>') +
    "<span>" + esc(message) + "</span></div>" +
    '<div class="recovery-grid">' + componentRows + "</div>";
}

function renderStartupStatus(title, snapshot, components, message, done) {
  const box = document.getElementById("action-result");
  const count = startupCount(snapshot, components);
  const componentRows = components.map(function (component) {
    const startup = componentStartup(snapshot, component);
    return '<div class="recovery-item">' +
      '<span class="dot ' + (startup.ready ? "dot-ok" : "dot-warn") + '"></span>' +
      '<strong>' + esc(component.label) + "</strong>" +
      '<span class="muted">' + esc(startup.detail) + "</span></div>";
  }).join("");
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="result-head">' +
    '<span class="dot ' + (done ? "dot-ok" : "dot-warn") + '"></span>' +
    "<strong>" + esc(title) + "</strong>" +
    '<span class="tag">' + count + "/" + components.length + " 已就绪</span>" +
    "</div>" +
    '<div class="progress-line">' + (done ? "" : '<span class="spinner"></span>') +
    "<span>" + esc(message) + "</span></div>" +
    '<div class="recovery-grid">' + componentRows + "</div>";
}

function sleep(ms) {
  return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
}

async function waitForRecovery(title) {
  const token = ++actionPollToken;
  const deadline = Date.now() + 120000;
  let lastSnapshot = null;
  let lastError = "";
  renderRecoveryStatus(title, lastSnapshot, "命令已提交，等待业务组件回收。", false);
  while (Date.now() < deadline && token === actionPollToken) {
    try {
      const response = await fetch("/api/discovery", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const snapshot = await response.json();
      lastSnapshot = {
        deployments: deploymentMap(snapshot.deployments || []),
        pods: snapshot.pods || [],
      };
      const count = recoveryCount(lastSnapshot);
      if (count === SHUTDOWN_COMPONENTS.length) {
        renderRecoveryStatus(title, lastSnapshot, "业务组件及 Pod 已全部回收，云侧主机仍在运行。", true);
        await load();
        await loadLog();
        return;
      }
      renderRecoveryStatus(
        title,
        lastSnapshot,
        "正在等待 Pod 回收，已完成 " + count + "/" + SHUTDOWN_COMPONENTS.length + "。",
        false
      );
    } catch (err) {
      lastError = err.message;
      renderRecoveryStatus(title, lastSnapshot, "暂时无法读取集群状态：" + lastError, false);
    }
    await sleep(2000);
  }
  if (token === actionPollToken) {
    renderRecoveryStatus(
      title,
      lastSnapshot,
      lastError ? "等待超时：" + lastError : "等待超时，仍有业务组件未完成回收。",
      false
    );
    await load();
    await loadLog();
  }
}

async function waitForStartup(title, componentNames) {
  const components = STARTUP_COMPONENTS.filter(function (component) {
    return componentNames.indexOf(component.name) >= 0;
  });
  const token = ++actionPollToken;
  const deadline = Date.now() + 120000;
  let lastSnapshot = null;
  let lastError = "";
  renderStartupStatus(title, lastSnapshot, components, "命令已提交，等待 Deployment 和 Pod 就绪。", false);
  while (Date.now() < deadline && token === actionPollToken) {
    try {
      const response = await fetch("/api/discovery", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const snapshot = await response.json();
      lastSnapshot = {
        deployments: deploymentMap(snapshot.deployments || []),
        pods: snapshot.pods || [],
      };
      const count = startupCount(lastSnapshot, components);
      if (count === components.length) {
        renderStartupStatus(title, lastSnapshot, components, "业务组件已就绪。", true);
        await load();
        await loadLog();
        syncScaleReplicas(true);
        return;
      }
      renderStartupStatus(
        title,
        lastSnapshot,
        components,
        "正在等待真实状态，已完成 " + count + "/" + components.length + "。",
        false
      );
    } catch (err) {
      lastError = err.message;
      renderStartupStatus(title, lastSnapshot, components, "暂时无法读取集群状态：" + lastError, false);
    }
    await sleep(2000);
  }
  if (token === actionPollToken) {
    renderStartupStatus(
      title,
      lastSnapshot,
      components,
      lastError ? "等待超时：" + lastError : "等待超时，组件可能仍在启动，请查看执行记录或 Pod 状态。",
      false
    );
    await load();
    await loadLog();
  }
}

function renderResult(data) {
  const box = document.getElementById("action-result");
  box.classList.remove("hidden");
  const failedStep = (data.steps || []).filter(function (step) { return !step.ok; })[0];
  let failureText = failedStep
    ? String(failedStep.error || failedStep.stderr || failedStep.stdout || "命令返回非零状态").replace(/\s+/g, " ").trim()
    : "";
  if (failureText.length > 240) failureText = failureText.slice(0, 240) + "…";
  const head =
    '<div class="result-head">' +
    '<span class="dot ' + (data.ok ? "dot-ok" : "dot-fail") + '"></span>' +
    "<strong>" + esc(data.title || data.id || "操作") + "</strong>" +
    '<span class="badge ' + (data.ok ? "badge-ok" : "badge-fail") + '">' +
    (data.ok ? "执行成功" : "执行失败") + "</span>" +
    (data.forwarded_to ? '<span class="tag">转发到 ' + esc(data.forwarded_to) + "</span>"
      : '<span class="tag">本机执行</span>') +
    (data.dry_run ? '<span class="tag">dry-run</span>' : "") +
    (data.aborted ? '<span class="tag tag-confirm">已中断</span>' : "") +
    "</div>";
  const failureNotice = data.ok ? "" :
    '<div class="result-notice">失败原因：' + esc(failureText || "命令执行失败") +
    "。请查看下方执行记录中的完整输出，或继续查看 Pod 日志。</div>";
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
  box.innerHTML = head + failureNotice + (steps || '<div class="empty">没有执行任何命令</div>');
}

function renderFailure(title, message) {
  const box = document.getElementById("action-result");
  box.classList.remove("hidden");
  box.innerHTML =
    '<div class="result-head"><span class="dot dot-fail"></span><strong>' + esc(title) + "</strong>" +
    '<span class="badge badge-fail">执行失败</span></div>' +
    '<div class="result-notice">失败原因：' + esc(message) +
    "。请查看下方执行记录中的完整输出，或继续查看 Pod 日志。</div>" +
    '<div class="step"><div class="step-body"><pre class="step-out step-err">' + esc(message) + "</pre></div></div>";
}

function flashButton(button, ok) {
  if (!button) return;
  const defaultLabel = button.getAttribute("data-default-label") || "执行";
  button.textContent = ok ? "成功" : "失败";
  window.setTimeout(function () {
    if (button.getAttribute("data-blocked") === "1") return;
    button.disabled = false;
    button.textContent = defaultLabel;
  }, 1400);
}

async function runAction(id, triggerButton) {
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
  const button = triggerButton || document.querySelector('[data-run="' + id + '"]');
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
    button.textContent = "执行中…";
  }
  renderPending(item.title, "正在提交操作，请等待命令返回。");
  let succeeded = false;
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: id,
        params: params,
        confirm: item.confirm ? true : undefined,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    succeeded = Boolean(data.ok);
    if (id === "scale_component") {
      const replicasInput = row && row.querySelector('[data-param="replicas"]');
      if (replicasInput) replicasInput.removeAttribute("data-dirty");
    }
    const startupComponents = STARTUP_ACTION_COMPONENTS[id] || [];
    if (startupComponents.length && data.ok) {
      await waitForStartup(item.title, startupComponents);
    } else if (id === "shutdown_stack" && data.ok) {
      await waitForRecovery(item.title);
    } else {
      renderResult(data);
      await loadLog();
      await load();
      syncScaleReplicas(true);
    }
  } catch (err) {
    renderFailure(item.title, err.message);
  } finally {
    if (button) {
      button.classList.remove("is-loading");
      button.disabled = button.getAttribute("data-blocked") === "1";
      flashButton(button, succeeded);
    }
  }
}

function yamlQuote(value) {
  return JSON.stringify(String(value));
}

function validDnsName(value) {
  return /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(value) && value.length <= 63;
}

function applyField(id) {
  const control = document.getElementById(id);
  return control ? control.value.trim() : "";
}

function appendContainerSpec(lines, indent, name, image, port, cpuRequest, memRequest, cpuLimit, memLimit) {
  lines.push(indent + "containers:");
  lines.push(indent + "  - name: " + yamlQuote(name));
  lines.push(indent + "    image: " + yamlQuote(image));
  if (port) {
    lines.push(indent + "    ports:");
    lines.push(indent + "      - containerPort: " + port);
  }
  const requests = [];
  if (cpuRequest) requests.push(indent + "        cpu: " + yamlQuote(cpuRequest));
  if (memRequest) requests.push(indent + "        memory: " + yamlQuote(memRequest));
  const limits = [];
  if (cpuLimit) limits.push(indent + "        cpu: " + yamlQuote(cpuLimit));
  if (memLimit) limits.push(indent + "        memory: " + yamlQuote(memLimit));
  if (requests.length || limits.length) {
    lines.push(indent + "    resources:");
    if (requests.length) {
      lines.push(indent + "      requests:");
      lines.push.apply(lines, requests);
    }
    if (limits.length) {
      lines.push(indent + "      limits:");
      lines.push.apply(lines, limits);
    }
  }
}

function appendNodeSelector(lines, indent, selector) {
  if (!selector) return;
  lines.push(indent + "nodeSelector:");
  lines.push(indent + "  " + yamlQuote(selector.key) + ": " + yamlQuote(selector.value));
}

function applySelector() {
  const value = applyField("apply-schedule");
  if (!value) return null;
  const index = value.indexOf("=");
  if (index <= 0 || index === value.length - 1) {
    throw new Error("调度限制格式无效");
  }
  return { key: value.slice(0, index), value: value.slice(index + 1) };
}

function buildGeneratedYaml() {
  const kind = applyField("apply-kind") || "Deployment";
  const namespace = applyField("apply-namespace");
  const name = applyField("apply-name");
  const image = applyField("apply-image");
  const portText = applyField("apply-port");
  const replicasText = applyField("apply-replicas") || "1";
  if (!validDnsName(namespace)) throw new Error("命名空间需符合 Kubernetes DNS 名称规则");
  if (!validDnsName(name)) throw new Error("资源名称需符合 Kubernetes DNS 名称规则");
  let port = 0;
  if (portText) {
    port = Number(portText);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error("端口需要在 1 - 65535 之间");
    }
  }
  let replicas = 1;
  if (["Deployment", "StatefulSet"].indexOf(kind) >= 0) {
    replicas = Number(replicasText);
    if (!Number.isInteger(replicas) || replicas < 0 || replicas > 100) {
      throw new Error("副本数需要在 0 - 100 之间");
    }
  }
  if (kind !== "Service" && !image) throw new Error("镜像不能为空");

  const selector = applySelector();
  const cpuRequest = applyField("apply-cpu-request");
  const memRequest = applyField("apply-mem-request");
  const cpuLimit = applyField("apply-cpu-limit");
  const memLimit = applyField("apply-mem-limit");
  const lines = [];

  if (kind === "Service") {
    lines.push("apiVersion: v1", "kind: Service", "metadata:",
      "  name: " + yamlQuote(name), "  namespace: " + yamlQuote(namespace),
      "spec:", "  selector:", "    app: " + yamlQuote(name), "  ports:",
      "    - name: tcp", "      port: " + (port || 80), "      targetPort: " + (port || 80));
    return lines.join("\n") + "\n";
  }

  if (kind === "Pod") {
    lines.push("apiVersion: v1", "kind: Pod", "metadata:",
      "  name: " + yamlQuote(name), "  namespace: " + yamlQuote(namespace),
      "  labels:", "    app: " + yamlQuote(name), "spec:");
    appendContainerSpec(lines, "  ", name, image, port, cpuRequest, memRequest, cpuLimit, memLimit);
    appendNodeSelector(lines, "  ", selector);
    return lines.join("\n") + "\n";
  }

  lines.push("apiVersion: apps/v1", "kind: " + kind, "metadata:",
    "  name: " + yamlQuote(name), "  namespace: " + yamlQuote(namespace), "spec:");
  if (kind === "StatefulSet") lines.push("  serviceName: " + yamlQuote(name));
  if (kind === "Deployment" || kind === "StatefulSet") lines.push("  replicas: " + replicas);
  lines.push("  selector:", "    matchLabels:", "      app: " + yamlQuote(name),
    "  template:", "    metadata:", "      labels:", "        app: " + yamlQuote(name),
    "    spec:");
  appendContainerSpec(lines, "      ", name, image, port, cpuRequest, memRequest, cpuLimit, memLimit);
  appendNodeSelector(lines, "      ", selector);
  return lines.join("\n") + "\n";
}

function syncApplyFields() {
  const kind = applyField("apply-kind") || "Deployment";
  const serviceOnly = kind === "Service";
  const replicasOnly = kind === "Deployment" || kind === "StatefulSet";
  document.querySelectorAll("#apply-panel .apply-image-field").forEach(function (item) {
    item.classList.toggle("hidden", serviceOnly);
  });
  document.querySelectorAll("#apply-panel .apply-replicas-field").forEach(function (item) {
    item.classList.toggle("hidden", !replicasOnly);
  });
}

function generateYaml() {
  const editor = document.getElementById("apply-yaml");
  try {
    editor.value = buildGeneratedYaml();
    editor.focus();
  } catch (err) {
    renderFailure("生成工作负载 YAML", err.message);
  }
}

async function submitYaml() {
  const editor = document.getElementById("apply-yaml");
  const button = document.getElementById("apply-submit");
  if (selectedTarget()) {
    renderFailure(
      "应用工作负载 YAML",
      "当前执行位置已切到其他控制台，请到新标签页的云主机控制台中应用，或先把执行位置切回本机"
    );
    return;
  }
  const text = editor.value;
  if (!text.trim()) {
    renderFailure("应用工作负载 YAML", "请先生成或填写 YAML 内容");
    return;
  }
  button.disabled = true;
  button.classList.add("is-loading");
  button.textContent = "应用中…";
  renderPending("应用工作负载 YAML", "正在执行 kubectl apply -f -。");
  let succeeded = false;
  try {
    const response = await fetch("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ yaml: text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    succeeded = Boolean(data.ok);
    renderResult(data);
    await loadLog();
    await sleep(500);
    await load();
  } catch (err) {
    renderFailure("应用工作负载 YAML", err.message);
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
    flashButton(button, succeeded);
  }
}

async function loadActions() {
  try {
    const response = await fetch("/api/actions", { cache: "no-store" });
    if (!response.ok) return;
    actionCatalog = await response.json();
    const applyPanel = document.getElementById("apply-panel");
    if (applyPanel) applyPanel.classList.toggle("hidden", actionCatalog.role !== "host");
    const applyEdgeNote = document.getElementById("apply-edge-note");
    if (applyEdgeNote) applyEdgeNote.classList.toggle("hidden", actionCatalog.role === "host");
    renderTargetSelector();
    renderActions();
    renderSettings();
  } catch (err) {
    document.getElementById("actions").innerHTML =
      '<div class="empty">无法读取操作目录：' + esc(err.message) + "</div>";
  }
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) return;
    healthData = await response.json();
    renderSettings();
  } catch (err) {
    /* 运行信息面板失败不影响主视图 */
  }
}

function logCommandType(item) {
  const command = (item.argv || []).join(" ");
  if (command.indexOf("kubectl") >= 0) return "kubectl";
  if (command.indexOf("amari-") >= 0) return "amari";
  return "other";
}

function renderLogs() {
  const state = listState.logs;
  syncPageSize("log-page-size", state);
  const filtered = logItems.filter(function (item) {
    const ok = item.returncode === 0;
    if (state.result === "ok" && !ok) return false;
    if (state.result === "fail" && ok) return false;
    if (state.type && logCommandType(item) !== state.type) return false;
    return includesQuery([
      (item.argv || []).join(" "),
      item.error,
      item.stderr,
      item.stdout,
    ].join(" "), state.query);
  });
  const visible = paginate(filtered, state);
  const tbody = document.querySelector("#log tbody");
  if (!visible.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">没有匹配的执行记录</td></tr>';
    renderPager("log-pager", "logs", filtered.length);
    return;
  }
  tbody.innerHTML = visible.map(function (item) {
    const ok = item.returncode === 0;
    const time = item.time
      ? new Date(item.time).toLocaleString("zh-CN", { hour12: false })
      : "-";
    const summary = String(item.error || item.stderr || item.stdout || "")
      .replace(/\s+/g, " ")
      .trim();
    const shortSummary = summary.length > 180 ? summary.slice(0, 180) + "…" : summary;
    return "<tr>" +
      "<td>" + esc(time) + "</td>" +
      '<td class="num"><span class="status"><span class="dot ' + (ok ? "dot-ok" : "dot-fail") +
      '"></span>' + esc(item.returncode) + "</span></td>" +
      "<td><code>" + esc((item.argv || []).join(" ")) + "</code></td>" +
      '<td title="' + esc(summary) + '">' + esc(shortSummary) + "</td>" +
      "</tr>";
  }).join("");
  renderPager("log-pager", "logs", filtered.length);
}

async function loadLog() {
  try {
    const response = await fetch("/api/log?limit=200", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    logItems = (data.items || []).slice().reverse();
    document.getElementById("log-meta").textContent =
      "来自审计日志，当前显示 " + logItems.length + " 条记录";
    renderLogs();
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
      clearDomainPanels();
      renderEdge(data);
      renderSettings();
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
      rememberDeploymentReplicas(data.deployments || []);
      renderDomain(data);

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
  loadHealth();
  loadLog();
});

function renderList(kind) {
  if (kind === "deployments") renderDeployments(deploymentItems);
  if (kind === "pods") renderPods(podItems);
  if (kind === "logs") renderLogs();
  if (kind === "users") renderUsers();
  if (kind === "modules") renderModules();
}

[
  { id: "deploy-search", kind: "deployments", key: "query", event: "input" },
  { id: "deploy-node", kind: "deployments", key: "node", event: "change" },
  { id: "deploy-namespace", kind: "deployments", key: "namespace", event: "change" },
  { id: "deploy-state", kind: "deployments", key: "state", event: "change" },
  { id: "deploy-system", kind: "deployments", key: "includeSystem", event: "change", checked: true },
  { id: "pod-search", kind: "pods", key: "query", event: "input" },
  { id: "pod-node", kind: "pods", key: "node", event: "change" },
  { id: "pod-namespace", kind: "pods", key: "namespace", event: "change" },
  { id: "pod-status", kind: "pods", key: "status", event: "change" },
  { id: "pod-system", kind: "pods", key: "includeSystem", event: "change", checked: true },
  { id: "log-search", kind: "logs", key: "query", event: "input" },
  { id: "log-result", kind: "logs", key: "result", event: "change" },
  { id: "log-type", kind: "logs", key: "type", event: "change" },
  { id: "user-search", kind: "users", key: "query", event: "input" },
  { id: "user-state", kind: "users", key: "state", event: "change" },
  { id: "user-cell", kind: "users", key: "cell", event: "change" },
  { id: "module-search", kind: "modules", key: "query", event: "input" },
  { id: "module-state", kind: "modules", key: "state", event: "change" },
].forEach(function (config) {
  const control = document.getElementById(config.id);
  if (!control) return;
  control.addEventListener(config.event, function () {
    const state = listState[config.kind];
    state[config.key] = config.checked
      ? control.checked
      : (config.key === "query" ? control.value.trim() : control.value);
    state.page = 1;
    renderList(config.kind);
  });
});

[
  { id: "deploy-page-size", kind: "deployments" },
  { id: "pod-page-size", kind: "pods" },
  { id: "log-page-size", kind: "logs" },
  { id: "user-page-size", kind: "users" },
].forEach(function (config) {
  const control = document.getElementById(config.id);
  if (!control) return;
  control.addEventListener("change", function () {
    const state = listState[config.kind];
    state.size = Number(control.value) || 10;
    state.page = 1;
    renderList(config.kind);
  });
});

function pageFromHash() {
  const name = (window.location.hash || "").replace(/^#/, "");
  return PAGE_ORDER.indexOf(name) >= 0 ? name : "overview";
}

function showPage(name) {
  const target = PAGE_ORDER.indexOf(name) >= 0 ? name : "overview";
  document.querySelectorAll(".page").forEach(function (section) {
    section.classList.toggle("hidden", section.getAttribute("data-page") !== target);
  });
  document.querySelectorAll(".nav-item").forEach(function (item) {
    item.classList.toggle("active", item.getAttribute("data-page") === target);
  });
  return target;
}

document.getElementById("nav").addEventListener("click", function (event) {
  const link = event.target.closest("[data-page]");
  if (!link) return;
  event.preventDefault();
  const target = showPage(link.getAttribute("data-page"));
  if (window.location.hash !== "#" + target) {
    window.location.hash = target;
  }
});

window.addEventListener("hashchange", function () {
  showPage(pageFromHash());
});

document.addEventListener("click", function (event) {
  const button = event.target.closest("[data-page-kind]");
  if (!button || button.disabled) return;
  const kind = button.getAttribute("data-page-kind");
  if (!listState[kind]) return;
  listState[kind].page = Number(button.getAttribute("data-page")) || 1;
  renderList(kind);
});

document.getElementById("actions").addEventListener("click", function (event) {
  const button = event.target.closest("button[data-run]");
  if (!button || button.disabled) return;
  runAction(button.getAttribute("data-run"), button);
});

document.getElementById("checks-actions").addEventListener("click", function (event) {
  const button = event.target.closest("button[data-run]");
  if (!button || button.disabled) return;
  runAction(button.getAttribute("data-run"), button);
});

document.getElementById("actions").addEventListener("change", function (event) {
  const row = event.target.closest(".action");
  if (!row || row.getAttribute("data-id") !== "scale_component") return;
  if (event.target.getAttribute("data-param") === "component") {
    const replicas = row.querySelector('[data-param="replicas"]');
    if (replicas) replicas.removeAttribute("data-dirty");
    syncScaleReplicas(true);
  }
  if (event.target.getAttribute("data-param") === "replicas") {
    event.target.setAttribute("data-dirty", "1");
  }
});

document.getElementById("agent-select").addEventListener("change", function (event) {
  targetSelection = event.target.value;
  const target = selectedTarget();
  if (target) {
    const url = consoleNavigationUrl(target.url);
    const opened = window.open(url, "_blank");
    if (opened) {
      try {
        opened.opener = null;
      } catch (err) {
        /* 跨域时忽略 */
      }
      renderNotice(
        "已在新标签页打开 " + target.label,
        "本页执行位置已切到该控制台，本地按钮会保持禁用；切回「" + localTargetLabel() +
        "」即可继续在本机操作。"
      );
    } else {
      renderFailure(
        "打开控制台",
        "浏览器拦截了新标签页，请允许本站弹出窗口，或直接在地址栏访问 " + url +
        "；本页执行位置已切到该控制台，切回本机即可继续。"
      );
    }
    renderActions();
    return;
  }
  renderActions();
});

document.getElementById("apply-generate").addEventListener("click", generateYaml);
document.getElementById("apply-submit").addEventListener("click", submitYaml);
document.getElementById("apply-kind").addEventListener("change", syncApplyFields);

document.addEventListener("visibilitychange", function () {
  if (!document.hidden) {
    load();
    loadActions();
    loadHealth();
  }
});

showPage(pageFromHash());
load();
loadActions();
loadHealth();
loadLog();
syncApplyFields();
schedule();
