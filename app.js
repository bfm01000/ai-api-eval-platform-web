const providers = [
  { name: "北辰线路", endpoint: "api.northstar.demo", state: "正常", stateClass: "good", success: "99.82%", p50: "1.12s", p95: "1.96s", ttft: "386ms", speed: "68.2", checked: "2 分钟前" },
  { name: "云桥 API", endpoint: "gateway.cloudbridge.demo", state: "正常", stateClass: "good", success: "99.36%", p50: "1.34s", p95: "2.18s", ttft: "428ms", speed: "62.4", checked: "5 分钟前" },
  { name: "Vertex Relay", endpoint: "relay.vertex.demo", state: "波动", stateClass: "warning", success: "97.94%", p50: "1.71s", p95: "3.42s", ttft: "612ms", speed: "51.8", checked: "8 分钟前" }
];

let runs = [];

const rangeData = {
  "1h": { subtitle: "每 10 分钟聚合 · 毫秒", p95: "54,82 161,75 269,89 376,70 483,78 591,65 698,72", p50: "54,158 161,152 269,162 376,149 483,155 591,146 698,151", area: "M54 82 L161 75 L269 89 L376 70 L483 78 L591 65 L698 72 L698 205 L54 205 Z", labels: ["-60m", "-50m", "-40m", "-30m", "-20m", "-10m", "现在"] },
  "24h": { subtitle: "每 4 小时聚合 · 毫秒", p95: "54,70 161,79 269,64 376,92 483,84 591,62 698,76", p50: "54,151 161,158 269,148 376,165 483,159 591,145 698,154", area: "M54 70 L161 79 L269 64 L376 92 L483 84 L591 62 L698 76 L698 205 L54 205 Z", labels: ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "现在"] },
  "7d": { subtitle: "每日聚合 · 毫秒", p95: "54,96 161,89 269,74 376,81 483,69 591,85 698,76", p50: "54,168 161,162 269,153 376,157 483,148 591,160 698,154", area: "M54 96 L161 89 L269 74 L376 81 L483 69 L591 85 L698 76 L698 205 L54 205 Z", labels: ["周四", "周五", "周六", "周日", "周一", "周二", "今天"] }
};

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
  providerRows.innerHTML = providers.map((provider) => `
    <tr>
      <td><span class="provider-name">${provider.name}<small>${provider.endpoint}</small></span></td>
      <td><span class="health ${provider.stateClass}">${provider.state}</span></td>
      <td>${provider.success}</td><td>${provider.p50}</td><td>${provider.p95}</td><td>${provider.ttft}</td><td>${provider.speed}</td><td>${provider.checked}</td>
    </tr>`).join("");
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
    } else {
      document.querySelector("#samplesInput").max = "20";
      document.querySelector("#dataNotice").textContent = "本机 Provider 测试已启用；概览和服务商对比仍为演示数据。";
      if (!configuredProviders.length) throw new Error("没有可用的测试 Provider");
      providerSelect.innerHTML = configuredProviders.map((provider) =>
        `<option value="${escapeHtml(provider.provider_id)}">${escapeHtml(provider.display_name)}</option>`
      ).join("");
      providerSelect.disabled = false;
      apiKeyField.hidden = true;
      endpointInput.readOnly = true;
      syncProviderFields();
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

renderProviders();
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

document.querySelectorAll(".segmented button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".segmented button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  const data = rangeData[button.dataset.range];
  document.querySelector("#trendSubtitle").textContent = data.subtitle;
  document.querySelector("#p95Line").setAttribute("points", data.p95);
  document.querySelector("#p50Line").setAttribute("points", data.p50);
  document.querySelector("#p95Area").setAttribute("d", data.area);
  document.querySelectorAll(".x-labels text").forEach((label, index) => { label.textContent = data.labels[index]; });
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
