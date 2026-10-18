let providers = [];
let runs = [];

const providerRows = document.querySelector("#providerRows");
const runRows = document.querySelector("#runRows");
const toast = document.querySelector("#toast");
const providerSelect = document.querySelector("#providerSelect");
const modelInput = document.querySelector("#modelInput");
const endpointInput = document.querySelector("#endpointInput");
const apiKeyField = document.querySelector("#apiKeyField");
const apiKeyInput = document.querySelector("#apiKeyInput");
const runButton = document.querySelector("#runButton");
let configuredProviders = [];
let testMode = "local";
let toastTimer;
let submitting = false;
let comparisonReport = null;
let benchmarkReports = [];
let benchmarkEstimateReady = false;
let selectedBenchmarkReport = null;
let benchmarkCatalog = null;

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", "\"": "&quot;"
  })[character]);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function renderProviders() {
  providerRows.innerHTML = providers.length ? providers.map((provider) => {
    const metrics = provider.metrics;
    const state = provider.samples === 0 ? "暂无样本" : metrics.failure_rate > 0 ? "有失败" : "正常";
    const stateClass = provider.samples === 0 ? "warning" : metrics.failure_rate > 0 ? "failed" : "good";
    return `
    <tr>
      <td><span class="provider-name">${escapeHtml(provider.display_name)}<small>${escapeHtml(provider.endpoint)}</small></span></td>
      <td><span class="health ${stateClass}">${state}</span></td>
      <td>${provider.samples ? `${(metrics.success_rate * 100).toFixed(0)}%` : "-"}</td>
      <td>${formatDuration(metrics.total_latency_ms?.p50)}</td><td>${formatDuration(metrics.total_latency_ms?.p95)}</td>
      <td>${formatDuration(metrics.ttft_ms?.p50)}</td><td>${metrics.tokens_per_second?.p50?.toFixed(1) ?? "-"}</td>
      <td>${provider.latest_at ? new Date(provider.latest_at).toLocaleString("zh-CN") : "-"}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="8">尚未配置 Provider。</td></tr>`;
}

function renderTrend(data) {
  const values = data.trend.flatMap((bucket) => [bucket.p50_ms, bucket.p95_ms]).filter((value) => value !== null);
  const max = values.length ? Math.max(100, ...values) * 1.15 : 1000;
  const path = (field) => data.trend.map((bucket, index) => {
    if (bucket[field] === null) return null;
    const x = 54 + index * (644 / 6);
    const y = 205 - bucket[field] / max * 177;
    return { x, y };
  }).reduce((result, point, index, points) => {
    if (!point) return result;
    return `${result}${!index || !points[index - 1] ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)} `;
  }, "");
  document.querySelector("#p50Line").setAttribute("d", path("p50_ms"));
  document.querySelector("#p95Line").setAttribute("d", path("p95_ms"));
  document.querySelector("#p95Area").setAttribute("d", "");
  document.querySelectorAll(".y-labels text").forEach((label, index) => {
    label.textContent = Math.round(max * (3 - index) / 3);
  });
  document.querySelectorAll(".x-labels text").forEach((label, index) => {
    const date = new Date(data.trend[index].started_at);
    label.textContent = data.window === "7d" ? `${date.getMonth() + 1}/${date.getDate()}` : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  });
  document.querySelector("#trendEmpty").hidden = values.length > 0;
}

async function loadInsights() {
  if (testMode !== "local") return;
  const selected = document.querySelector(".segmented button.active")?.dataset.range || "24h";
  const response = await fetch(`/api/insights?window=${selected}`);
  if (!response.ok) throw new Error("历史指标加载失败");
  const data = await response.json();
  const metrics = data.metrics;
  const label = { "1h": "1 小时", "24h": "24 小时", "7d": "7 天" }[selected];
  document.querySelector("#overviewSummary").textContent = `最近 ${label} · ${data.sample_count} 个样本`;
  document.querySelector("#trendSubtitle").textContent = `最近 ${label} · 成功样本延迟（毫秒）`;
  document.querySelector("#successMetric").textContent = data.sample_count ? `${(metrics.success_rate * 100).toFixed(0)}%` : "-";
  document.querySelector("#latencyMetric").textContent = formatDuration(metrics.total_latency_ms?.p95);
  document.querySelector("#ttftMetric").textContent = formatDuration(metrics.ttft_ms?.p50);
  document.querySelector("#speedMetric").textContent = metrics.tokens_per_second?.p50?.toFixed(1) ?? "-";
  providers = data.providers;
  renderProviders();
  renderTrend(data);
}

function renderRuns(filter = "all") {
  document.querySelector("#exportButton").disabled = !runs.some((run) => run.real && run.status !== "running");
  const visibleRuns = filter === "all" ? runs : runs.filter((run) => run.status === filter);
  runRows.innerHTML = visibleRuns.length ? visibleRuns.map((run) => `
    <tr>
      <td><span class="run-name">${run.id}<small>Performance test</small></span></td>
      <td>${escapeHtml(run.provider)}<br><small>${escapeHtml(run.model)}</small></td><td>${run.mode}</td><td>${run.samples}</td>
      <td><span class="health ${run.status}">${run.statusText}</span></td><td>${run.started}</td>
      <td><button class="table-action" type="button" data-report="${run.id}">${run.status === "running" ? "查看进度" : "查看报告"}</button></td>
    </tr>`).join("") : `<tr><td colspan="7">当前筛选条件下没有任务。</td></tr>`;
}

function selectedProvider() {
  return configuredProviders.find((provider) => provider.provider_id === providerSelect.value);
}

function syncProviderFields() {
  const provider = selectedProvider();
  if (!provider) return;
  endpointInput.value = provider.endpoint;
  modelInput.value = provider.default_model;
}

async function loadProviderOptions() {
  const status = document.querySelector("#runStatus");
  try {
    if (location.protocol === "file:") throw new Error("请通过后端服务地址打开网站，例如 http://127.0.0.1:8000");
    const response = await fetch("/api/test-config");
    if (!response.ok) throw new Error("Provider 配置加载失败");
    const config = await response.json();
    testMode = config.mode;
    configuredProviders = config.providers || [];
    document.body.classList.toggle("local-mode", testMode === "local");
    if (testMode === "public") {
      runs = [];
      renderRuns();
      providerSelect.closest("label").hidden = true;
      apiKeyField.hidden = false;
      apiKeyInput.required = true;
      endpointInput.readOnly = false;
      endpointInput.value = "";
      modelInput.value = "";
      document.querySelector("#runTitle").textContent = "测试自己的 API";
      document.querySelector("#runDescription").textContent = "OpenAI-compatible · 普通 / Streaming";
      document.querySelector("#dataNotice").textContent = "Key 经本站服务器发送到你填写的服务商，不会保存。测试会消耗你的额度；结果仅保留在当前页面。";
      document.querySelector("#publicComparisonTargets").hidden = false;
      document.querySelector("#comparisonSamplesField").hidden = true;
    } else {
      document.querySelector("#samplesInput").max = "20";
      document.querySelector("#dataNotice").textContent = "本机 Provider 测试已启用；概览、对比和评测来自真实记录。真实请求会消耗 Provider 额度。";
      if (!configuredProviders.length) throw new Error("没有可用的测试 Provider");
      providerSelect.innerHTML = configuredProviders.map((provider) =>
        `<option value="${escapeHtml(provider.provider_id)}">${escapeHtml(provider.display_name)}</option>`
      ).join("");
      providerSelect.disabled = false;
      apiKeyField.hidden = true;
      endpointInput.readOnly = true;
      syncProviderFields();
      await loadInsights();
      await loadBenchmark();
      document.querySelector("#localComparisonTargets").hidden = false;
      document.querySelector("#localTargetOptions").innerHTML = configuredProviders.map((provider) =>
        `<label><input type="checkbox" value="${escapeHtml(provider.provider_id)}" /><span>${escapeHtml(provider.display_name)}</span></label>`
      ).join("");
      if (configuredProviders.length < 2) {
        document.querySelector("#comparisonButton").disabled = true;
        document.querySelector("#comparisonStatus").textContent = "至少配置两条线路后可运行对比。";
      }
    }
    modelInput.disabled = false;
    runButton.disabled = false;
    status.textContent = "就绪";
  } catch (error) {
    providerSelect.innerHTML = "<option>配置不可用</option>";
    status.textContent = "配置错误";
    status.className = "status-pill failed";
    document.querySelector("#runDescription").textContent = error.message;
    showToast(error.message);
  }
}

function updateComparisonCost() {
  const count = testMode === "public" ? 2 : document.querySelectorAll("#localTargetOptions input:checked").length;
  const samples = testMode === "public" ? 1 : Number(document.querySelector("#comparisonSamples").value) || 1;
  document.querySelector("#comparisonCost").textContent = `共 ${count * samples} 次请求`;
}

function renderComparison(report) {
  document.querySelector("#comparisonRows").innerHTML = report.entries.map((entry) => {
    const metrics = entry.summary.metrics;
    const complete = entry.summary.status === "completed" && entry.summary.failed === 0;
    return `<tr>
      <td><span class="provider-name">${escapeHtml(entry.display_name)}<small>${escapeHtml(entry.model)}</small></span></td>
      <td><span class="health ${complete ? "good" : "failed"}">${complete ? "完成" : "有失败"}</span></td>
      <td>${metrics ? `${(metrics.success_rate * 100).toFixed(0)}%` : "-"}</td>
      <td>${formatDuration(metrics?.total_latency_ms?.p50)}</td>
      <td>${formatDuration(metrics?.total_latency_ms?.p95)}</td>
      <td>${formatDuration(metrics?.ttft_ms?.p50)}</td>
      <td>${metrics?.tokens_per_second?.p50?.toFixed(1) ?? "-"}</td>
      <td>${metrics?.total_tokens ?? "未提供"}</td>
    </tr>`;
  }).join("");
  document.querySelector("#exportComparison").disabled = false;
}

document.querySelector("#comparisonForm").addEventListener("change", updateComparisonCost);
document.querySelector("#comparisonForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#comparisonButton");
  const status = document.querySelector("#comparisonStatus");
  let payload;
  let path;
  if (testMode === "public") {
    const targets = [...document.querySelectorAll(".comparison-target")].map((element) => ({
      display_name: element.querySelector('[name="display_name"]').value.trim(),
      endpoint: element.querySelector('[name="endpoint"]').value.trim(),
      model: element.querySelector('[name="model"]').value.trim(),
      api_key: element.querySelector('[name="api_key"]').value
    }));
    payload = { targets, mode: document.querySelector("#comparisonMode").value, samples: 1 };
    path = "/api/self-comparisons";
    document.querySelectorAll('.comparison-target [name="api_key"]').forEach((input) => { input.value = ""; });
  } else {
    const provider_ids = [...document.querySelectorAll("#localTargetOptions input:checked")].map((input) => input.value);
    if (provider_ids.length < 2) { status.textContent = "请至少选择两条线路。"; return; }
    payload = { provider_ids, mode: document.querySelector("#comparisonMode").value, samples: Number(document.querySelector("#comparisonSamples").value) };
    path = "/api/comparisons";
  }
  button.disabled = true;
  status.textContent = "正在逐条测试...";
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error(await responseError(response));
    comparisonReport = await response.json();
    renderComparison(comparisonReport);
    status.textContent = `已完成 ${comparisonReport.entries.length} 条线路的对比。`;
    if (testMode === "local") loadInsights().catch((error) => showToast(error.message));
  } catch (error) {
    status.textContent = error.message;
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#exportComparison").addEventListener("click", () => {
  if (!comparisonReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(comparisonReport, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "api-comparison.json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll('.comparison-target [name="api_key"]').forEach((input) => { input.value = ""; });
});

function formatDuration(value) {
  if (value === null || value === undefined) return "不适用";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value.toFixed(1)}ms`;
}

async function responseError(response) {
  try {
    const document = await response.json();
    const detail = document.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      return `${detail.code || "REQUEST.FAILED"}: ${detail.message || "Provider 请求失败"}`;
    }
  } catch (_) {
    // The status text is the safest fallback for a non-JSON upstream error.
  }
  return `请求失败（HTTP ${response.status}）`;
}

function openReport(id) {
  const run = runs.find((item) => item.id === id);
  if (!run) return;
  document.querySelector("#dialogTitle").textContent = `${run.id} · ${run.provider}`;
  document.querySelector("#dialogContent").innerHTML = `
    <div class="report-grid">
      <div class="report-item"><span>模型</span><strong>${escapeHtml(run.model)}</strong></div>
      <div class="report-item"><span>运行状态</span><strong>${run.statusText}</strong></div>
      <div class="report-item"><span>P95 总延迟</span><strong>${run.latency || "计算中"}</strong></div>
      <div class="report-item"><span>TTFT</span><strong>${run.ttft || "计算中"}</strong></div>
      <div class="report-item"><span>成功率</span><strong>${run.success || "计算中"}</strong></div>
      <div class="report-item"><span>样本</span><strong>${run.samples}</strong></div>
    </div>
    <p class="report-note">${run.real ? escapeHtml(run.error || `Provider 返回：${run.content || "（空内容）"}`) : "这是界面原型生成的演示报告。真实版本将展示原始时间点、错误分类、Token Usage、请求参数版本和可追溯记录。"}</p>`;
  document.querySelector("#dialogFootnote").textContent = run.real ? "真实测试 · 小样本不代表长期稳定性" : "演示数据 · 不代表真实 API 测量";
  document.querySelector("#reportDialog").showModal();
}

renderRuns();
loadProviderOptions();

document.querySelectorAll("[data-toast]").forEach((button) => button.addEventListener("click", () => showToast(button.dataset.toast)));
document.querySelector("#dismissNotice").addEventListener("click", () => { document.querySelector(".notice").hidden = true; });
document.querySelector("#exportButton").addEventListener("click", () => {
  const reports = runs.filter((run) => run.real && run.status !== "running");
  if (!reports.length) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({ exported_at: new Date().toISOString(), reports }, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "api-test-reports.json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.querySelector("#samplesInput").addEventListener("input", (event) => {
  document.querySelector("#sampleCost").textContent = `本轮 ${event.target.value || 0} 次请求`;
});
window.addEventListener("pageshow", () => { apiKeyInput.value = ""; });
providerSelect.addEventListener("change", syncProviderFields);
document.querySelector("#runFilter").addEventListener("change", (event) => renderRuns(event.target.value));
document.querySelector("#runRows").addEventListener("click", (event) => { const button = event.target.closest("[data-report]"); if (button) openReport(button.dataset.report); });
document.querySelector("#closeDialog").addEventListener("click", () => document.querySelector("#reportDialog").close());
document.querySelector("#dialogDone").addEventListener("click", () => document.querySelector("#reportDialog").close());

document.querySelectorAll(".nav-item[href]").forEach((link) => link.addEventListener("click", () => {
  document.querySelectorAll(".nav-item[href]").forEach((item) => item.classList.remove("active"));
  link.classList.add("active");
}));

document.querySelectorAll(".segmented button").forEach((button) => button.addEventListener("click", async () => {
  document.querySelectorAll(".segmented button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  try { await loadInsights(); } catch (error) { showToast(error.message); }
}));

document.querySelector("#runForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting) return;
  const button = runButton;
  const status = document.querySelector("#runStatus");
  const track = document.querySelector("#progressTrack");
  const bar = document.querySelector("#progressBar");
  const resultPanel = document.querySelector("#testResult");
  const provider = selectedProvider();
  if (testMode === "local" && !provider) return;
  const model = modelInput.value.trim();
  const endpoint = endpointInput.value.trim();
  const modeValue = document.querySelector("#modeSelect").value;
  const mode = modeValue === "streaming" ? "Streaming" : "普通请求";
  const id = `LIVE-${crypto.randomUUID()}`;
  const providerName = testMode === "public" ? "自有 API" : provider.display_name;
  const samplesCount = parseInt(document.querySelector("#samplesInput")?.value, 10) || 1;
  const run = { id, provider: providerName, model, mode, samples: `0 / ${samplesCount}`, status: "running", statusText: "运行中", started: new Date().toLocaleTimeString(), real: true };
  const payload = JSON.stringify(testMode === "public"
    ? { endpoint, api_key: apiKeyInput.value, model, mode: modeValue, samples: samplesCount }
    : { provider_id: provider.provider_id, model, mode: modeValue, samples: samplesCount });
  apiKeyInput.value = "";
  submitting = true;
  const controls = [...document.querySelectorAll("#runForm input, #runForm select")];
  const originalDisabled = controls.map((control) => control.disabled);
  controls.forEach((control) => { control.disabled = true; });
  const controller = new AbortController();
  const timeout = testMode === "public" ? window.setTimeout(() => controller.abort(), 80000) : null;

  button.disabled = true;
  button.querySelector("span:last-child").textContent = "检测运行中";
  status.textContent = "运行中";
  status.className = "status-pill running";
  track.hidden = false;
  bar.style.width = "18%";
  resultPanel.hidden = true;
  resultPanel.classList.remove("error");
  runs.unshift(run);
  runs = runs.slice(0, 50);
  renderRuns(document.querySelector("#runFilter").value);

  const progressTimer = window.setTimeout(() => { bar.style.width = "68%"; }, 500);
  try {
    const response = await fetch(testMode === "public" ? "/api/self-runs" : "/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      signal: controller.signal
    });
    if (!response.ok) throw new Error(await responseError(response));
    const summary = await response.json();
    const metrics = summary.metrics;
    const usage = metrics && metrics.total_tokens !== null
      ? `${metrics.total_input_tokens || 0} / ${metrics.total_output_tokens || 0} / ${metrics.total_tokens || 0}`
      : "未提供";

    const p50Duration = metrics ? formatDuration(metrics.total_latency_ms?.p50) : "不适用";
    const p95Duration = metrics ? formatDuration(metrics.total_latency_ms?.p95) : "不适用";
    const ttftDuration = metrics?.ttft_ms ? formatDuration(metrics.ttft_ms.p50) : "不适用";
    const successRateStr = metrics ? `${(metrics.success_rate * 100).toFixed(0)}%` : "0%";

    Object.assign(run, {
      id: summary.run_id,
      samples: `${summary.succeeded} / ${summary.total_samples}`,
      status: summary.status === "completed" && summary.failed === 0 ? "completed" : "failed",
      statusText: summary.status === "completed" && summary.failed === 0 ? "已完成" : (summary.succeeded > 0 ? "部分失败" : "失败"),
      latency: p95Duration,
      ttft: ttftDuration,
      success: successRateStr,
      metrics,
      content: `已执行 ${summary.succeeded + summary.failed} 次请求（成功 ${summary.succeeded}，失败 ${summary.failed}）`
    });

    document.querySelector("#testLatency").textContent = p50Duration;
    const p95El = document.querySelector("#testLatencyP95");
    if (p95El) p95El.textContent = p95Duration;
    document.querySelector("#testTtft").textContent = ttftDuration;
    const successEl = document.querySelector("#testSuccess");
    if (successEl) successEl.textContent = successRateStr;
    document.querySelector("#testUsage").textContent = usage;
    const categories = { auth: "认证失败", timeout: "请求超时", connect: "连接失败", http: "服务商 HTTP 错误", rate_limit: "服务商限流", protocol: "响应格式不兼容", provider: "服务商错误", parse: "响应解析失败", internal: "内部错误" };
    const failures = Object.entries(metrics?.error_counts || {}).map(([key, count]) => `${categories[key] || "请求失败"} ${count} 次`).join("，");
    run.error = failures;
    document.querySelector("#testResponse").textContent = `${run.content}。${failures || "小样本结果仅反映本次测试。"}`;
    resultPanel.hidden = false;
    status.textContent = run.statusText;
    status.className = run.status === "completed" ? "status-pill completed" : "status-pill failed";
    showToast(`${providerName} 测试完成`);
    if (testMode === "local") loadInsights().catch((error) => showToast(error.message));
  } catch (error) {
    if (error.name === "AbortError") error = new Error("等待测试结果超时，请稍后重试。");
    Object.assign(run, { samples: `0 / ${samplesCount}`, status: "failed", statusText: "失败", success: "0%", ttft: "不适用", error: error.message });
    document.querySelector("#testLatency").textContent = "-";
    const p95El = document.querySelector("#testLatencyP95");
    if (p95El) p95El.textContent = "-";
    document.querySelector("#testTtft").textContent = "-";
    const successEl = document.querySelector("#testSuccess");
    if (successEl) successEl.textContent = "-";
    document.querySelector("#testUsage").textContent = "-";
    document.querySelector("#testResponse").textContent = error.message;
    resultPanel.classList.add("error");
    resultPanel.hidden = false;
    status.textContent = "失败";
    status.className = "status-pill failed";
    showToast(`${providerName} 测试失败`);
  } finally {
    submitting = false;
    window.clearTimeout(timeout);
    controls.forEach((control, index) => { control.disabled = originalDisabled[index]; });
    if (testMode === "public") apiKeyInput.value = "";
    window.clearTimeout(progressTimer);
    bar.style.width = "100%";
    renderRuns(document.querySelector("#runFilter").value);
    button.disabled = false;
    button.querySelector("span:last-child").textContent = "再次运行测试";
    window.setTimeout(() => { track.hidden = true; bar.style.width = "0"; }, 500);
  }
});

function benchmarkPayload() {
  return {
    provider_id: document.querySelector("#benchmarkProvider").value,
    model: document.querySelector("#benchmarkModel").value.trim(),
    samples: Number(document.querySelector("#benchmarkSamples").value),
    input_cny_per_million: document.querySelector("#benchmarkInputPrice").value,
    output_cny_per_million: document.querySelector("#benchmarkOutputPrice").value,
    max_budget_cny: document.querySelector("#benchmarkBudget").value
  };
}

function renderBenchmarkReports() {
  document.querySelector("#benchmarkReports").innerHTML = benchmarkReports.length
    ? benchmarkReports.slice().reverse().map((report) => `<tr>
      <td>${escapeHtml(report.batch_id.slice(-10))}</td>
      <td>${escapeHtml(report.provider_id)}<br><small>${escapeHtml(report.model)}</small></td>
      <td>${report.capability_score === null ? "-" : `${(report.capability_score * 100).toFixed(0)}%`}</td>
      <td>${report.success_rate === null ? "-" : `${(report.success_rate * 100).toFixed(0)}%`}</td>
      <td>${report.reference?.agreement == null ? "未导入" : `${(report.reference.agreement * 100).toFixed(0)}%（未验证身份）`}</td>
      <td>${report.observed_samples} / ${report.planned_samples}</td>
      <td><button class="table-action" type="button" data-benchmark-report="${escapeHtml(report.batch_id)}">详情</button></td>
    </tr>`).join("")
    : `<tr><td colspan="7">暂无评测记录。</td></tr>`;
}

function showBenchmarkReport(report) {
  selectedBenchmarkReport = report;
  document.querySelector("#exportBenchmark").disabled = false;
  const detail = document.querySelector("#benchmarkDetail");
  const categoryScores = Object.entries(report.category_scores)
    .map(([category, score]) => `${escapeHtml(category)} ${(score * 100).toFixed(0)}%`).join(" · ");
  detail.innerHTML = `<h3>评测批次 ${escapeHtml(report.batch_id)}</h3>
    <p>${categoryScores || "暂无有效样本"} · 预估上限 ¥${escapeHtml(report.estimated_max_cny)}</p>
    <p>${report.reference ? `参考模型：${escapeHtml(report.reference.reference_model)} · 来源：${escapeHtml(report.reference.source)} · 答案一致率不代表模型身份` : "尚无同版本参考答案。"}</p>
    <div class="table-wrap"><table><thead><tr><th>题目</th><th>类别</th><th>状态</th><th>结果</th><th>回答</th><th>延迟</th></tr></thead><tbody>
    ${report.rows.map((row) => `<tr><td>${escapeHtml(row.case_id)}</td><td>${escapeHtml(row.category)}</td><td>${escapeHtml(row.status)}</td><td>${row.passed === null ? "规则已变化" : row.passed ? "通过" : "未通过"}</td><td class="answer-cell">${escapeHtml(row.answer ?? "-")}</td><td>${formatDuration(row.latency_ms)}</td></tr>`).join("")}
    </tbody></table></div>`;
  detail.hidden = false;
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function loadBenchmark() {
  const response = await fetch("/api/benchmark");
  if (!response.ok) throw new Error("评测题集加载失败");
  const catalog = await response.json();
  benchmarkCatalog = catalog;
  document.querySelector("#benchmarkVersion").textContent = `${catalog.version} · SHA256 ${catalog.sha256}`;
  document.querySelector("#benchmarkCases").innerHTML = catalog.cases.map((item) => `<tr>
    <td>${escapeHtml(item.case_id)}</td><td>${escapeHtml(item.category)}</td>
    <td class="answer-cell">${escapeHtml(item.prompt)}</td><td>${escapeHtml(item.expected)}</td></tr>`).join("");
  const select = document.querySelector("#benchmarkProvider");
  select.innerHTML = configuredProviders.map((item) => `<option value="${escapeHtml(item.provider_id)}">${escapeHtml(item.display_name)}</option>`).join("");
  document.querySelector("#benchmarkModel").value = configuredProviders[0]?.default_model || "";
  document.querySelector("#referenceStatus").textContent = catalog.reference
    ? `已导入 ${catalog.reference.answers.length} 条参考答案：${catalog.reference.reference_model}，来源 ${catalog.reference.source}。身份未验证。`
    : "尚未导入参考答案。导入文件需要与当前题集版本和 SHA256 一致。";
  const reports = await fetch("/api/benchmark/reports");
  if (!reports.ok) throw new Error("评测历史加载失败");
  benchmarkReports = await reports.json();
  renderBenchmarkReports();
}

document.querySelector("#benchmarkProvider").addEventListener("change", (event) => {
  document.querySelector("#benchmarkModel").value = configuredProviders.find((item) => item.provider_id === event.target.value)?.default_model || "";
});
document.querySelector("#benchmarkForm").addEventListener("input", () => {
  benchmarkEstimateReady = false;
  document.querySelector("#benchmarkRun").disabled = true;
  document.querySelector("#benchmarkCost").textContent = "参数已变化，请重新计算";
});
document.querySelector("#benchmarkEstimate").addEventListener("click", async () => {
  const form = document.querySelector("#benchmarkForm");
  if (!form.reportValidity()) return;
  try {
    const response = await fetch("/api/benchmark/estimate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(benchmarkPayload())
    });
    if (!response.ok) throw new Error(await responseError(response));
    const estimate = await response.json();
    benchmarkEstimateReady = estimate.within_budget;
    document.querySelector("#benchmarkRun").disabled = !estimate.within_budget;
    document.querySelector("#benchmarkCost").textContent = `${estimate.requests} 次请求 · 预估上限 ¥${Number(estimate.estimated_max_cny).toFixed(4)}${estimate.within_budget ? "" : " · 超出本轮上限"}`;
  } catch (error) { showToast(error.message); }
});
document.querySelector("#benchmarkForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!benchmarkEstimateReady) return;
  const button = document.querySelector("#benchmarkRun");
  button.disabled = true;
  button.textContent = "运行中";
  document.querySelector("#benchmarkCost").textContent = "正在顺序运行评测，请不要关闭页面";
  try {
    const response = await fetch("/api/benchmark/runs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(benchmarkPayload())
    });
    if (!response.ok) throw new Error(await responseError(response));
    const report = await response.json();
    benchmarkReports.push(report);
    renderBenchmarkReports();
    showBenchmarkReport(report);
    document.querySelector("#benchmarkCost").textContent = `完成 ${report.observed_samples} / ${report.planned_samples} 个样本`;
  } catch (error) {
    document.querySelector("#benchmarkCost").textContent = error.message;
    showToast(error.message);
  } finally {
    benchmarkEstimateReady = false;
    button.textContent = "运行真实评测";
    loadInsights().catch((error) => showToast(error.message));
  }
});
document.querySelector("#benchmarkReports").addEventListener("click", (event) => {
  const button = event.target.closest("[data-benchmark-report]");
  if (button) showBenchmarkReport(benchmarkReports.find((item) => item.batch_id === button.dataset.benchmarkReport));
});
document.querySelector("#exportBenchmark").addEventListener("click", () => {
  if (!selectedBenchmarkReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(selectedBenchmarkReport, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `benchmark-${selectedBenchmarkReport.batch_id}.json`;
  link.click();
  URL.revokeObjectURL(url);
});
document.querySelector("#downloadReferenceTemplate").addEventListener("click", () => {
  if (!benchmarkCatalog) return;
  const template = {
    benchmark_version: benchmarkCatalog.version,
    benchmark_sha256: benchmarkCatalog.sha256,
    answers: benchmarkCatalog.cases.map((item) => ({ case_id: item.case_id, answer: "" }))
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(template, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "reference-template.json";
  link.click();
  URL.revokeObjectURL(url);
});
document.querySelector("#referenceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = document.querySelector("#referenceFile").files[0];
  if (!file || file.size > 80000) { showToast("参考文件不能超过 80 KB"); return; }
  try {
    const contents = JSON.parse(await file.text());
    const payload = {
      benchmark_version: contents.benchmark_version,
      benchmark_sha256: contents.benchmark_sha256,
      answers: contents.answers,
      reference_model: document.querySelector("#referenceModel").value.trim(),
      source: document.querySelector("#referenceSource").value.trim()
    };
    const response = await fetch("/api/benchmark/reference", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json();
    document.querySelector("#referenceStatus").textContent = `已导入 ${data.imported} 条参考答案。模型身份未验证。`;
    const reports = await fetch("/api/benchmark/reports");
    benchmarkReports = await reports.json();
    renderBenchmarkReports();
  } catch (error) { showToast(error.message); }
});
