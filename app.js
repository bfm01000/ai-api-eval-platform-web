const providers = [
  { name: "北辰线路", endpoint: "api.northstar.demo", state: "正常", stateClass: "good", success: "99.82%", p50: "1.12s", p95: "1.96s", ttft: "386ms", speed: "68.2", checked: "2 分钟前" },
  { name: "云桥 API", endpoint: "gateway.cloudbridge.demo", state: "正常", stateClass: "good", success: "99.36%", p50: "1.34s", p95: "2.18s", ttft: "428ms", speed: "62.4", checked: "5 分钟前" },
  { name: "Vertex Relay", endpoint: "relay.vertex.demo", state: "波动", stateClass: "warning", success: "97.94%", p50: "1.71s", p95: "3.42s", ttft: "612ms", speed: "51.8", checked: "8 分钟前" }
];

let runs = [
  { id: "RUN-0248", provider: "北辰线路", model: "gpt-5.2", mode: "Streaming", samples: "20 / 20", status: "completed", statusText: "已完成", started: "今天 20:42", latency: "1.96s", ttft: "386ms", success: "100%" },
  { id: "RUN-0247", provider: "云桥 API", model: "gpt-5.2", mode: "Streaming", samples: "20 / 20", status: "completed", statusText: "已完成", started: "今天 20:28", latency: "2.18s", ttft: "428ms", success: "100%" },
  { id: "RUN-0246", provider: "Vertex Relay", model: "gpt-5.2", mode: "普通请求", samples: "19 / 20", status: "failed", statusText: "部分失败", started: "今天 19:55", latency: "3.42s", ttft: "不适用", success: "95%" },
  { id: "RUN-0245", provider: "北辰线路", model: "claude-sonnet-4.5", mode: "Streaming", samples: "10 / 10", status: "completed", statusText: "已完成", started: "今天 18:31", latency: "2.06s", ttft: "401ms", success: "100%" }
];

const rangeData = {
  "1h": { subtitle: "每 10 分钟聚合 · 毫秒", p95: "54,82 161,75 269,89 376,70 483,78 591,65 698,72", p50: "54,158 161,152 269,162 376,149 483,155 591,146 698,151", area: "M54 82 L161 75 L269 89 L376 70 L483 78 L591 65 L698 72 L698 205 L54 205 Z", labels: ["-60m", "-50m", "-40m", "-30m", "-20m", "-10m", "现在"] },
  "24h": { subtitle: "每 4 小时聚合 · 毫秒", p95: "54,70 161,79 269,64 376,92 483,84 591,62 698,76", p50: "54,151 161,158 269,148 376,165 483,159 591,145 698,154", area: "M54 70 L161 79 L269 64 L376 92 L483 84 L591 62 L698 76 L698 205 L54 205 Z", labels: ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "现在"] },
  "7d": { subtitle: "每日聚合 · 毫秒", p95: "54,96 161,89 269,74 376,81 483,69 591,85 698,76", p50: "54,168 161,162 269,153 376,157 483,148 591,160 698,154", area: "M54 96 L161 89 L269 74 L376 81 L483 69 L591 85 L698 76 L698 205 L54 205 Z", labels: ["周四", "周五", "周六", "周日", "周一", "周二", "今天"] }
};

const providerRows = document.querySelector("#providerRows");
const runRows = document.querySelector("#runRows");
const toast = document.querySelector("#toast");
let toastTimer;

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
  const visibleRuns = filter === "all" ? runs : runs.filter((run) => run.status === filter);
  runRows.innerHTML = visibleRuns.length ? visibleRuns.map((run) => `
    <tr>
      <td><span class="run-name">${run.id}<small>Performance test</small></span></td>
      <td>${run.provider}<br><small>${run.model}</small></td><td>${run.mode}</td><td>${run.samples}</td>
      <td><span class="health ${run.status}">${run.statusText}</span></td><td>${run.started}</td>
      <td><button class="table-action" type="button" data-report="${run.id}">${run.status === "running" ? "查看进度" : "查看报告"}</button></td>
    </tr>`).join("") : `<tr><td colspan="7">当前筛选条件下没有任务。</td></tr>`;
}

function updateEstimate() {
  const count = Math.max(1, Math.min(20, Number(document.querySelector("#requestCount").value) || 1));
  document.querySelector("#costEstimate").textContent = `${count} 次 · 约 ${Math.max(1, Math.ceil(count * 0.2))} 分钟`;
}

function openReport(id) {
  const run = runs.find((item) => item.id === id);
  if (!run) return;
  document.querySelector("#dialogTitle").textContent = `${run.id} · ${run.provider}`;
  document.querySelector("#dialogContent").innerHTML = `
    <div class="report-grid">
      <div class="report-item"><span>模型</span><strong>${run.model}</strong></div>
      <div class="report-item"><span>运行状态</span><strong>${run.statusText}</strong></div>
      <div class="report-item"><span>P95 总延迟</span><strong>${run.latency || "计算中"}</strong></div>
      <div class="report-item"><span>TTFT</span><strong>${run.ttft || "计算中"}</strong></div>
      <div class="report-item"><span>成功率</span><strong>${run.success || "计算中"}</strong></div>
      <div class="report-item"><span>样本</span><strong>${run.samples}</strong></div>
    </div>
    <p class="report-note">这是界面原型生成的演示报告。真实版本将展示原始时间点、错误分类、Token Usage、请求参数版本和可追溯记录。</p>`;
  document.querySelector("#reportDialog").showModal();
}

renderProviders();
renderRuns();
updateEstimate();

document.querySelectorAll("[data-toast]").forEach((button) => button.addEventListener("click", () => showToast(button.dataset.toast)));
document.querySelector("#dismissNotice").addEventListener("click", () => { document.querySelector(".notice").hidden = true; });
document.querySelector("#exportButton").addEventListener("click", () => showToast("演示报告已准备，正式导出将在报告模块接入后开放"));
document.querySelector("#requestCount").addEventListener("input", updateEstimate);
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

document.querySelector("#runForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = document.querySelector("#runButton");
  const status = document.querySelector("#runStatus");
  const track = document.querySelector("#progressTrack");
  const bar = document.querySelector("#progressBar");
  const provider = document.querySelector("#providerSelect").value;
  const model = document.querySelector("#modelSelect").value;
  const count = Number(document.querySelector("#requestCount").value);
  const mode = document.querySelector("#modeSelect").value === "stream" ? "Streaming" : "普通请求";
  const id = `RUN-${String(249 + runs.length).padStart(4, "0")}`;

  button.disabled = true;
  button.querySelector("span:last-child").textContent = "检测运行中";
  status.textContent = "运行中";
  status.className = "status-pill running";
  track.hidden = false;
  bar.style.width = "12%";
  runs.unshift({ id, provider, model, mode, samples: `0 / ${count}`, status: "running", statusText: "运行中", started: "刚刚" });
  renderRuns(document.querySelector("#runFilter").value);
  document.querySelector("#runs").scrollIntoView({ behavior: "smooth", block: "start" });

  window.setTimeout(() => { bar.style.width = "58%"; runs[0].samples = `${Math.max(1, Math.floor(count / 2))} / ${count}`; renderRuns(document.querySelector("#runFilter").value); }, 650);
  window.setTimeout(() => {
    bar.style.width = "100%";
    Object.assign(runs[0], { samples: `${count} / ${count}`, status: "completed", statusText: "已完成", latency: "2.04s", ttft: mode === "Streaming" ? "412ms" : "不适用", success: "100%" });
    renderRuns(document.querySelector("#runFilter").value);
    status.textContent = "已完成";
    status.className = "status-pill completed";
    button.disabled = false;
    button.querySelector("span:last-child").textContent = "再次运行演示检测";
    showToast(`${id} 演示检测已完成`);
    window.setTimeout(() => { track.hidden = true; bar.style.width = "0"; }, 500);
  }, 1500);
});
