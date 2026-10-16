import { tokens } from "../../static/spectra-chart.js";

// X ticks per history window; longer windows tick at local midnight and are
// labelled with the weekday instead of the hour.
const TICK_HOURS = { 6: 1, 12: 2, 24: 3, 48: 6 };
// X and Y tick font sizes in px per cell size. The compact charts keep both
// axes small; lg enlarges them so they stay legible beside a large chart.
const AXIS_FONT_PX = { xs: [12, 12], sm: [12, 12], md: [12, 14], lg: [16, 18] };
// fitMetricValues() rescales by the measured overflow, so a few passes settle
// it; the margin keeps subpixel rounding from clipping the last glyph.
const FIT_PASSES = 3;
const FIT_MARGIN = 0.98;
const FIT_MIN_FONT_PX = 10;
const FIT_SETTLED_PX = 0.5;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

// The host locale as Intl accepts it. Host runtimes may expose POSIX locale
// names such as "C", which Intl rejects.
function intlLocale(locale) {
  try {
    return Intl.getCanonicalLocales(locale)[0] ?? "en";
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return "en";
  }
}

function metricMarkup(data, prefix, ctx, locale) {
  const rawValue = data[`${prefix}_value`];
  // server.py fixes the decimal places of a numeric state with a "." separator;
  // the locale supplies the separators here and the decimal part is drawn smaller.
  let value = escapeHtml(rawValue);
  const numeric = /^-?\d+(?:\.(\d+))?$/.exec(rawValue);
  if (numeric) {
    const decimals = numeric[1]?.length ?? 0;
    const parts = new Intl.NumberFormat(locale, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).formatToParts(Number(rawValue));
    const text = (list) => escapeHtml(list.map((part) => part.value).join(""));
    const fraction = parts.findIndex((part) => part.type === "decimal");
    value = fraction < 0
      ? text(parts)
      : `${text(parts.slice(0, fraction))}<span class="metric-fraction">${text(parts.slice(fraction))}</span>`;
  }
  const unit = escapeHtml(data[`${prefix}_unit`]);
  const labelKey = data[`${prefix}_label_key`];
  const label = escapeHtml(
    labelKey ? ctx.t(labelKey, data[`${prefix}_label`]) : data[`${prefix}_label`],
  );
  const showLabel = ctx.cell.options[`show_${prefix}_label`] !== false;
  const icon = data[`${prefix}_icon`];
  const iconMarkup = icon === "none"
    ? ""
    : `<i class="metric-icon ph-bold ph-${escapeHtml(icon)}" aria-hidden="true"></i>`;
  return `
    <div class="metric" data-value="${prefix}">
      ${iconMarkup}
      <div class="metric-copy">
        <div class="metric-value"><span>${value}</span>${unit ? `<small>${unit}</small>` : ""}</div>
        ${showLabel ? `<div class="metric-label">${label}</div>` : ""}
      </div>
    </div>`;
}

// The whole-card states (setup, failed load) use the host's standard card body:
// a muted paragraph under the title bar, as the bundled ha_* widgets do.
function noteMarkup(message, detail = "") {
  return `
    <div class="w-body">
      <p class="u-muted">${escapeHtml(message)}</p>
      ${detail ? `<small class="u-muted">${escapeHtml(detail)}</small>` : ""}
    </div>`;
}

function chartMessageMarkup(message, detail = "") {
  return `
    <div class="chart-message">
      <p>${escapeHtml(message)}</p>
      ${detail ? `<small>${escapeHtml(detail)}</small>` : ""}
    </div>`;
}

// Height of the visible icon/value/label group, including the gaps between them.
function metricGroupHeight(metric) {
  const rects = Array.from(metric.children, (child) => child.getBoundingClientRect())
    .filter((rect) => rect.width || rect.height);
  return Math.max(...rects.map((rect) => rect.bottom)) - Math.min(...rects.map((rect) => rect.top));
}

// Sizes each value to the largest font at which it spans the metric's width
// while the whole icon/value/label group still fits the metric's height. The
// text is only known at render time, so CSS alone cannot do this; the CSS font
// size is the starting point and what remains if the stylesheet never loads.
// Visible icons go above their values or beside them, the same way for both
// values: whichever leaves the smaller of the iconed values the larger font, so
// the shape of the value blocks decides, not the widget size. Ties keep the
// icons above. Both icons end up the same size.
function fitMetricValues(shadow) {
  const metrics = Array.from(shadow.querySelectorAll(".metric"));
  const iconed = metrics.filter(
    (metric) => metric.querySelector(".metric-icon")?.getBoundingClientRect().width,
  );
  const arrange = (side) => {
    for (const metric of iconed) metric.toggleAttribute("data-icon-side", side);
    return Math.min(...iconed.map(fitMetricValue));
  };

  for (const metric of metrics) {
    if (!iconed.includes(metric)) fitMetricValue(metric);
  }
  if (!iconed.length) return;
  const stackedFont = arrange(false);
  const sideFont = arrange(true);
  const iconFont = sideFont > stackedFont ? sideFont : arrange(false);

  // Both icons take the size of the smaller value's icon. The larger value was
  // fitted with a larger icon, so the smaller one only frees space there.
  for (const metric of iconed) metric.style.setProperty("--icon-value-size", `${iconFont}px`);
}

// Fits the metric's value in its current icon arrangement and returns the
// resulting font size in pixels.
function fitMetricValue(metric) {
  const value = metric.querySelector(".metric-value");
  const icon = metric.querySelector(".metric-icon");
  const label = metric.querySelector(".metric-label");
  const side = metric.hasAttribute("data-icon-side");
  metric.style.removeProperty("--metric-scale");
  metric.style.removeProperty("--value-size");
  metric.style.removeProperty("--icon-value-size");
  value.style.whiteSpace = "";
  value.style.overflowWrap = "";

  const metricRect = metric.getBoundingClientRect();
  const metricStyle = getComputedStyle(metric);
  const safeRight = metricRect.right - Number.parseFloat(metricStyle.paddingRight);
  const availableHeight = metricRect.height
    - Number.parseFloat(metricStyle.paddingTop)
    - Number.parseFloat(metricStyle.paddingBottom);

  // The graph share is honoured exactly, so a short values row can be lower
  // than the group at its CSS sizes. The whole group then shrinks as one unit:
  // --metric-scale scales the label and gaps, the number gets the same factor
  // here and the icon follows it, and the passes below only adjust the number
  // to the width.
  const groupScale = availableHeight / metricGroupHeight(metric);
  if (groupScale < 1) {
    const scale = Math.max(0, groupScale) * FIT_MARGIN;
    metric.style.setProperty("--metric-scale", String(scale));
    metric.style.setProperty("--value-size", `${Number.parseFloat(getComputedStyle(value).fontSize) * scale}px`);
  }

  let fits = false;
  for (let pass = 0; pass < FIT_PASSES; pass += 1) {
    const rect = value.getBoundingClientRect();
    const iconRect = icon?.getBoundingClientRect() ?? new DOMRect();
    const availableWidth = Math.max(0, Math.min(rect.width, safeRight - rect.left));
    const contentRight = Math.max(
      rect.left,
      ...Array.from(value.children, (child) => child.getBoundingClientRect().right),
    );
    const contentWidth = contentRight - rect.left;
    const groupHeight = metricGroupHeight(metric);
    if (!availableWidth || !availableHeight || !contentWidth || !rect.height) {
      fits = true;
      break;
    }

    // Grow toward the width only while the whole icon/value/label group still
    // fits vertically. The icon is sized from the value font, so it scales with
    // the value: beside it the icon shares the row width with the value and
    // must leave the label its full width, above it the icon adds to the
    // growing height. The label and gaps stay fixed.
    const fontSize = Number.parseFloat(getComputedStyle(value).fontSize);
    const rowWidth = safeRight - iconRect.left - (rect.left - iconRect.right);
    const widthScale = side
      ? Math.min(
        rowWidth / (iconRect.width + contentWidth),
        label ? (rowWidth - label.scrollWidth) / iconRect.width : Infinity,
      )
      : availableWidth / contentWidth;
    const growingHeight = rect.height + (side ? 0 : iconRect.height);
    const heightScale = (availableHeight - groupHeight + growingHeight) / growingHeight;
    const fittedFontSize = Math.max(
      FIT_MIN_FONT_PX,
      fontSize * Math.min(widthScale, heightScale) * FIT_MARGIN,
    );
    if (Math.abs(fittedFontSize - fontSize) < FIT_SETTLED_PX) {
      fits = contentWidth <= availableWidth && groupHeight <= availableHeight;
      break;
    }
    metric.style.setProperty("--value-size", `${fittedFontSize}px`);
  }
  if (!fits) {
    value.style.whiteSpace = "normal";
    value.style.overflowWrap = "anywhere";
  }
  return Number.parseFloat(getComputedStyle(value).fontSize);
}

// Plots the history against real time. The shared lineChart() helper spaces
// points by sample index, which would make an irregular HA history look evenly
// sampled, so this chart is configured here with a linear time axis instead.
function drawHistory(canvas, palette, data, ctx, locale) {
  const times = data.history_times;
  const values = data.history_values;

  const tickHours = TICK_HOURS[data.history_hours] ?? 24;
  const labelOptions = tickHours < 24 ? { hour: "numeric", hourCycle: "h23" } : { weekday: "short" };
  const tickFormat = new Intl.DateTimeFormat(locale, labelOptions);

  // Ticks sit on local clock hours, not on samples. setHours() works in local
  // time, so a daily step stays on midnight across DST changes.
  const tick = new Date(times[0]);
  tick.setHours(tick.getHours() - (tick.getHours() % tickHours), 0, 0, 0);
  if (tick.getTime() < times[0]) tick.setHours(tick.getHours() + tickHours);
  const tickTimes = [];
  while (tick.getTime() <= times.at(-1)) {
    tickTimes.push(tick.getTime());
    tick.setHours(tick.getHours() + tickHours);
  }

  // A narrow range is widened symmetrically to the configured minimum span.
  // Suggested bounds only ever extend the axis, so they cannot clip the data.
  const low = Math.min(...values);
  const high = Math.max(...values);
  const padding = Math.max(0, data.minimum_y_span - (high - low)) / 2;
  const compact = ctx.cell.size === "xs" || ctx.cell.size === "sm";
  const [xFontSize, yFontSize] = AXIS_FONT_PX[ctx.cell.size];

  new window.Chart(canvas, {
    type: "line",
    data: {
      datasets: [{
        data: values.map((y, index) => ({ x: times[index], y })),
        borderColor: palette.accent4,
        borderWidth: 3,
        tension: 0.25,
        pointRadius: 0,
      }],
    },
    options: {
      // The frame is rasterised, so nothing may be caught mid-animation.
      animation: false,
      // Y tick numbers; without it Chart.js formats them in the runtime's locale.
      locale,
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          type: "linear",
          min: times[0],
          max: times.at(-1),
          // Chart.js annotates tick objects in place, so each build gets new ones.
          afterBuildTicks: (scale) => {
            scale.ticks = tickTimes.map((value) => ({ value }));
          },
          ticks: {
            color: palette.textSecondary,
            font: { family: palette.fontFamily, weight: 700, size: xFontSize },
            // Labels closer than this are skipped evenly; the default 3 px let
            // nine two-digit hours in xs run together.
            autoSkip: true,
            autoSkipPadding: 8,
            maxRotation: 0,
            callback: (value) => tickFormat.format(value),
          },
          grid: { drawOnChartArea: false, color: palette.textMuted },
          border: { display: false },
        },
        y: {
          suggestedMin: low - padding,
          suggestedMax: high + padding,
          // The compact chart of xs/sm keeps only a couple of values.
          ticks: {
            color: palette.textPrimary,
            font: { family: palette.fontFamily, weight: 700, size: yFontSize },
            maxTicksLimit: compact ? 3 : 4,
          },
          // Tick marks match the X axis; the grid lines keep the sunken colour.
          grid: { color: palette.surfaceSunken, tickColor: palette.textMuted },
          border: { display: false },
        },
      },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

const styles = `
  <link rel="stylesheet" href="/static/style/spectra-widgets.css" />
  <style>
    /* Denser than the shell's own padding and gap: with those the two values
       no longer fit at sm and md. */
    .w.dual-history {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: clamp(var(--space-1), 2cqmin, var(--space-3));
      padding: clamp(var(--space-2), 3cqmin, var(--space-4));
    }
    .dual-body {
      min-width: 0;
      min-height: 0;
      display: grid;
      gap: clamp(var(--space-1), 2cqmin, var(--space-3));
    }
    .chart-region {
      grid-area: chart;
      position: relative;
      min-width: 0;
      min-height: 0;
      padding: var(--space-1);
      background: var(--surface-sunken);
    }
    .chart-region canvas {
      display: block;
      width: 100% !important;
      height: 100% !important;
    }
    .values-region {
      grid-area: values;
      min-width: 0;
      min-height: 0;
      display: grid;
      gap: clamp(var(--space-1), 2cqmin, var(--space-2));
    }
    .metric {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      align-content: center;
      align-items: center;
      gap: calc(clamp(var(--space-1), 2cqmin, var(--space-2)) * var(--metric-scale, 1));
      padding: clamp(var(--space-1), 2cqmin, var(--space-2));
    }
    /* fitMetricValues() overrides --value-size; the icon follows it, or the
       shared --icon-value-size once both values are fitted. */
    .metric { --value-size: clamp(var(--fs-value), 12cqmin, var(--fs-jumbo)); }
    .metric:nth-child(2) { background: var(--surface-sunken); }
    .metric[data-icon-side] { grid-template-columns: auto minmax(0, 1fr); }
    /* Above its value the icon keeps the text's left edge. */
    .metric-icon {
      justify-self: start;
      color: var(--icon);
      font-size: calc(var(--icon-value-size, var(--value-size)) * .75);
      line-height: 1;
    }
    .metric-copy {
      align-self: center;
      width: 100%;
      min-width: 0;
    }
    .metric-value {
      min-width: 0;
      white-space: nowrap;
      font-size: var(--value-size);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      line-height: 1;
    }
    .metric-value small {
      margin-left: .18em;
      color: var(--text-muted);
      font-size: .45em;
      font-weight: var(--fw-bold);
    }
    .metric-fraction { font-size: 66.6667%; }
    /* fit-content makes scrollWidth the text width, which fitMetricValue()
       reserves beside a side icon. */
    .metric-label {
      width: fit-content;
      max-width: 100%;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-top: calc(var(--space-1) * var(--metric-scale, 1));
      color: var(--text-secondary);
      font-size: calc(var(--fs-label) * var(--metric-scale, 1));
      font-weight: var(--fw-semi);
    }
    .w-body p { margin: 0; }
    .chart-message {
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-1);
      padding: var(--space-2);
      text-align: center;
      color: var(--text-muted);
      font-size: var(--fs-caption);
      font-weight: var(--fw-semi);
    }

    /* Wide sizes put the chart and the value column side by side. The stored
       left/right option is honoured only here, so it cannot shrink either
       region of the stacked layout. */
    .dual-history:is([data-size="md"], [data-size="lg"]) .dual-body {
      grid-template-columns: minmax(0, var(--chart-fr)) minmax(0, var(--values-fr));
      grid-template-areas: "chart values";
    }
    .dual-history:is([data-size="md"], [data-size="lg"]) .dual-body[data-position="left"] {
      grid-template-columns: minmax(0, var(--values-fr)) minmax(0, var(--chart-fr));
      grid-template-areas: "values chart";
    }
    /* Implicit tracks give a lone primary value the whole column. */
    .dual-history:is([data-size="md"], [data-size="lg"]) .values-region {
      grid-auto-rows: minmax(0, 1fr);
    }

    /* Compact sizes stack the chart above the two values, so the chart_width
       share applies to the height here. A large share leaves the values row
       lower than its content; fitMetricValues then shrinks each metric group. */
    .dual-history:is([data-size="xs"], [data-size="sm"]) .dual-body {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: minmax(0, var(--chart-fr)) minmax(0, var(--values-fr));
      grid-template-areas: "chart" "values";
    }
    .dual-history:is([data-size="xs"], [data-size="sm"]) .values-region {
      grid-auto-flow: column;
      grid-auto-columns: minmax(0, 1fr);
      grid-template-rows: minmax(0, 1fr);
    }

    .dual-history[data-size="xs"] {
      gap: var(--space-1);
      padding: var(--space-2);
    }
    .dual-history[data-size="xs"] .dual-body { gap: var(--space-1); }
    .dual-history[data-size="xs"] .values-region { gap: var(--space-1); }
    .dual-history[data-size="xs"] .metric {
      --value-size: clamp(var(--fs-lead), 13cqmin, var(--fs-value));
      padding: var(--space-1);
    }
    .dual-history[data-size="xs"] .metric-label {
      margin-top: 0;
      font-size: calc(var(--fs-caption) * var(--metric-scale, 1));
    }
  </style>`;

export default function render(shadow, ctx) {
  // server.py owns every normalization and fallback, so its payload is rendered
  // as received. When that call raised or timed out the host substitutes a bare
  // {error}, or null if it produced nothing; both read as a failed load.
  const data = ctx.data ?? { error_key: "error.fetch" };
  // CSS zoom scales the host's zoom-locked title bar as one unit: icon, text,
  // min-height and rule. A blank or invalid value leaves it at 1.
  const titleScale = Number(ctx.cell.options.title_scale) || 1;
  const mount = (icon, title, body) => {
    shadow.innerHTML = `${styles}
    <div class="w dual-history" data-widget="ha_dual_history" data-size="${ctx.cell.size}">
      <div class="w-title" style="zoom: ${titleScale}">
        <i class="ph-bold ${icon}" aria-hidden="true"></i>
        <h3>${escapeHtml(title)}</h3>
      </div>
      ${body}
    </div>`;
  };
  // A card without sensor data has no entity name to fall back to.
  const cardTitle = data.title || ctx.t("title.default", "Dual History");

  if (data.setup) {
    mount("ph-chart-line-up", cardTitle, noteMarkup(
      ctx.t("setup.select_entities", "Select a primary Home Assistant entity."),
    ));
    return;
  }
  if (data.error_key || data.error) {
    const key = data.error_key ?? "error.fetch";
    const fallback = key === "error.missing_core"
      ? "Install and configure Home Assistant Core for this widget."
      : "Could not load current Home Assistant states. Check the Home Assistant Core settings.";
    mount("ph-warning-circle", cardTitle, noteMarkup(ctx.t(key, fallback), data.error));
    return;
  }

  const locale = intlLocale(ctx.locale);
  let chartBody = '<canvas aria-label="History"></canvas>';
  if (data.chart_error_key) {
    chartBody = chartMessageMarkup(
      ctx.t(data.chart_error_key, "Could not load history"),
      data.chart_error,
    );
  } else if (!data.history_values.length) {
    chartBody = chartMessageMarkup(ctx.t("chart.no_data", "No data for this period"));
  }

  mount("ph-chart-line-up", data.title, `
      <div class="w-body dual-body" data-position="${escapeHtml(data.values_position)}"
        style="--chart-fr: ${data.chart_width}fr; --values-fr: ${100 - data.chart_width}fr">
        <div class="chart-region">${chartBody}</div>
        <div class="values-region">
          ${metricMarkup(data, "primary", ctx, locale)}
          ${data.secondary_value ? metricMarkup(data, "secondary", ctx, locale) : ""}
        </div>
      </div>`);

  const canvas = shadow.querySelector("canvas");
  if (canvas) drawHistory(canvas, tokens(shadow.host), data, ctx, locale);

  // The fit measures text whose metrics settle only when the shared stylesheet
  // and then the fonts it selects have loaded, so it runs at exactly those two
  // moments rather than on a frame timer. The first pass lays the text out,
  // which is what starts a font load the second pass waits for. The listener
  // is not awaited: a <link> detached by a newer render() never fires, and a
  // promise bound to it would hang the superseded call.
  shadow.querySelector("link").addEventListener("load", () => {
    fitMetricValues(shadow);
    document.fonts.ready.then(() => fitMetricValues(shadow));
  });
}
