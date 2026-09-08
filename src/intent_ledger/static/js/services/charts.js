// Chart is a global from the vendored UMD build (static/js/external/), which
// auto-registers every built-in controller/element/scale/plugin - unlike the
// ES-module build, there's nothing to Chart.register() here.

function chartGradient(ctx, color) {
  const area = ctx.chart.chartArea;
  if (!area) return color;

  const gradient = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
  gradient.addColorStop(0, `${color}40`);
  gradient.addColorStop(1, `${color}00`);
  return gradient;
}

function lastValue(data) {
  for (let i = data.length - 1; i >= 0; i -= 1) {
    if (data[i] !== null && data[i] !== undefined) return data[i];
  }
  return null;
}

function formatValue(value) {
  return typeof value === "number"
    ? value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : value;
}

function externalTooltip(context) {
  const { chart, tooltip } = context;
  const wrap = chart.canvas.parentNode;

  let el = wrap.querySelector(".chart-tooltip");
  if (!el) {
    el = document.createElement("div");
    el.className = "chart-tooltip";
    wrap.appendChild(el);
  }

  if (tooltip.opacity === 0) {
    el.style.opacity = "0";
    return;
  }

  const rows = tooltip.dataPoints.map((point) => `
    <div class="flex items-center justify-between gap-6 py-0.5">
      <span class="flex items-center gap-1.5 text-text-secondary">
        <span class="inline-block h-2 w-2 shrink-0 rounded-full" style="background:${point.dataset.borderColor}"></span>
        ${point.dataset.label}
      </span>
      <span class="mono font-semibold text-text">${formatValue(point.raw)}</span>
    </div>
  `).join("");

  el.innerHTML = `
    <div class="mb-1 text-xs font-semibold text-text">${tooltip.title?.[0] ?? ""}</div>
    ${rows}
  `;

  el.style.opacity = "1";
  el.style.left = `${tooltip.caretX}px`;
  el.style.top = `${tooltip.caretY}px`;
}

const crosshairPlugin = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements();
    if (!active || !active.length) return;

    const { ctx, chartArea } = chart;
    const x = active[0].element.x;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#34373b";
    ctx.stroke();
    ctx.restore();
  },
};

Chart.register(crosshairPlugin);

function renderChart(canvas) {
  if (canvas.dataset.chartRendered) return;
  canvas.dataset.chartRendered = "1";

  const spec = JSON.parse(canvas.dataset.chart);
  const type = canvas.dataset.chartType || "line";
  const datasets = spec.datasets || [];
  const card = canvas.closest(".chart-card");
  const valueEl = card?.querySelector("[data-chart-value]");
  const legendEl = card?.querySelector("[data-chart-legend]");
  const isSingleLine = type === "line" && datasets.length === 1;
  const showTooltip = canvas.dataset.chartTooltip !== "off";
  const showAxes = canvas.dataset.chartAxes === "on";

  if (valueEl) {
    const value = datasets[0] ? lastValue(datasets[0].data) : null;
    valueEl.textContent = value == null ? "-" : formatValue(value);
  }

  if (legendEl && datasets.length > 1) {
    legendEl.innerHTML = datasets.map((d) => `
      <span class="inline-flex items-center gap-1.5 text-xs text-text-secondary">
        <span class="inline-block h-2.5 w-2.5 rounded-full border-2" style="border-color:${d.color}"></span>
        ${d.label}
      </span>
    `).join("");
  }

  new Chart(canvas, {
    type,
    data: {
      labels: spec.labels,
      datasets: datasets.map((dataset) => ({
        label: dataset.label,
        data: dataset.data,
        borderColor: dataset.color,
        backgroundColor: isSingleLine
          ? (context) => chartGradient(context, dataset.color)
          : dataset.color,
        borderWidth: 2,
        borderRadius: type === "bar" ? 3 : undefined,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHitRadius: 12,
        pointHoverBackgroundColor: dataset.color,
        pointHoverBorderColor: "#18191c",
        pointHoverBorderWidth: 2,
        tension: 0.4,
        fill: isSingleLine,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: false,
          external: showTooltip ? externalTooltip : undefined,
        },
      },
      scales: {
        x: {
          display: showAxes,
          grid: { display: false },
          border: { color: "#34373b" },
          ticks: { color: "#6a6a6a", font: { size: 10 } },
        },
        y: {
          display: showAxes,
          grid: { color: "#2a2c30" },
          border: { display: false },
          ticks: { color: "#6a6a6a", font: { size: 10 }, maxTicksLimit: 4 },
        },
      },
    },
  });
}

function renderFinanceCharts(root) {
  (root || document).querySelectorAll("canvas[data-chart]").forEach(renderChart);
}

window.renderFinanceCharts = renderFinanceCharts;
renderFinanceCharts(document);
