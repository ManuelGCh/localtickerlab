/* StockLab · gráfico de velas en canvas puro (sin dependencias) */
(function () {
  "use strict";

  const COLORS = {
    up: "#34d399",
    down: "#f87171",
    upSoft: "rgba(52, 211, 153, 0.28)",
    downSoft: "rgba(248, 113, 113, 0.28)",
    grid: "rgba(148, 163, 184, 0.10)",
    axis: "rgba(148, 163, 184, 0.62)",
    crosshair: "rgba(226, 232, 240, 0.35)",
    sma20: "#fbbf24",
    sma50: "#818cf8",
    sma200: "#f472b6",
    rsi: "#22d3ee",
  };

  const priceFmt = new Intl.NumberFormat("es-ES", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const volFmt = new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 });

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  class PriceChart {
    constructor(canvas, tooltip) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.tooltip = tooltip;
      this.data = null;
      this.range = 126;
      this.hover = null;
      this._raf = null;
      this._onMove = this._onMove.bind(this);
      this._onLeave = this._onLeave.bind(this);
      canvas.addEventListener("mousemove", this._onMove);
      canvas.addEventListener("mouseleave", this._onLeave);
      this._resize = this._resize.bind(this);
      if (window.ResizeObserver) {
        this._observer = new ResizeObserver(() => this._resize());
        this._observer.observe(canvas.parentElement);
      } else {
        window.addEventListener("resize", this._resize);
      }
    }

    setData(data) {
      this.data = data;
      this.hover = null;
      this._resize();
    }

    resize() {
      this._resize();
    }

    setRange(points) {
      this.range = points;
      this.hover = null;
      this.draw();
    }

    destroy() {
      this.canvas.removeEventListener("mousemove", this._onMove);
      this.canvas.removeEventListener("mouseleave", this._onLeave);
      if (this._observer) this._observer.disconnect();
      if (this._raf) cancelAnimationFrame(this._raf);
    }

    /* ---------------------------------------------------------------- */
    _resize() {
      const rect = this.canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      this.canvas.width = Math.round(rect.width * dpr);
      this.canvas.height = Math.round(rect.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.width = rect.width;
      this.height = rect.height;
      this.draw();
    }

    _view() {
      const d = this.data;
      if (!d || !d.dates || !d.dates.length) return null;
      const n = this.range > 0 ? Math.min(this.range, d.dates.length) : d.dates.length;
      const start = d.dates.length - n;
      return { start, end: d.dates.length, n };
    }

    _bounds(view) {
      const d = this.data;
      let lo = Infinity;
      let hi = -Infinity;
      for (let i = view.start; i < view.end; i++) {
        const c = d.candles[i];
        if (!c) continue;
        lo = Math.min(lo, c[2]);
        hi = Math.max(hi, c[1]);
      }
      for (const key of ["sma20", "sma50", "sma200"]) {
        const series = d[key] || [];
        for (let i = view.start; i < view.end; i++) {
          const v = series[i];
          if (v != null) {
            lo = Math.min(lo, v);
            hi = Math.max(hi, v);
          }
        }
      }
      if (!isFinite(lo) || !isFinite(hi)) {
        lo = 0;
        hi = 1;
      }
      const pad = (hi - lo) * 0.06 || hi * 0.02 || 1;
      return { lo: lo - pad, hi: hi + pad };
    }

    /* ---------------------------------------------------------------- */
    draw() {
      const ctx = this.ctx;
      if (!ctx || !this.width) return;
      ctx.clearRect(0, 0, this.width, this.height);
      const view = this._view();
      if (!view) {
        ctx.fillStyle = COLORS.axis;
        ctx.font = "12px system-ui, sans-serif";
        ctx.fillText("Sin datos", 16, 24);
        return;
      }
      const d = this.data;
      const padL = 6;
      const padR = 58;
      const padT = 8;
      const padB = 20;
      const innerW = Math.max(10, this.width - padL - padR);
      const usableH = this.height - padT - padB;
      const priceH = usableH * 0.63;
      const gap = 12;
      const volH = usableH * 0.16;
      const rsiH = usableH - priceH - volH - gap * 2;

      const priceTop = padT;
      const volTop = priceTop + priceH + gap;
      const rsiTop = volTop + volH + gap;

      const bounds = this._bounds(view);
      const yPrice = (v) =>
        priceTop + priceH - ((v - bounds.lo) / (bounds.hi - bounds.lo)) * priceH;
      const slot = innerW / view.n;
      const xAt = (i) => padL + (i - view.start + 0.5) * slot;

      /* Rejilla de precios */
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      ctx.fillStyle = COLORS.axis;
      ctx.font = "10.5px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      const gridLines = 5;
      for (let g = 0; g <= gridLines; g++) {
        const value = bounds.lo + ((bounds.hi - bounds.lo) * g) / gridLines;
        const y = Math.round(yPrice(value)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(padL + innerW, y);
        ctx.stroke();
        ctx.fillText(priceFmt.format(value), padL + innerW + 8, y);
      }

      /* Velas */
      const bodyW = Math.max(1, Math.min(14, slot * 0.64));
      for (let i = view.start; i < view.end; i++) {
        const c = d.candles[i];
        if (!c) continue;
        const [o, h, l, cl] = c;
        const up = cl >= o;
        const x = xAt(i);
        ctx.strokeStyle = up ? COLORS.up : COLORS.down;
        ctx.fillStyle = up ? COLORS.up : COLORS.down;
        ctx.lineWidth = Math.max(1, Math.min(2, slot * 0.14));
        ctx.beginPath();
        ctx.moveTo(x, yPrice(h));
        ctx.lineTo(x, yPrice(l));
        ctx.stroke();
        const top = yPrice(Math.max(o, cl));
        const bottom = yPrice(Math.min(o, cl));
        const bodyH = Math.max(1, bottom - top);
        ctx.fillRect(x - bodyW / 2, top, bodyW, bodyH);
      }

      /* Medias móviles */
      const drawLine = (series, color, width) => {
        if (!series) return;
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.beginPath();
        let started = false;
        for (let i = view.start; i < view.end; i++) {
          const v = series[i];
          if (v == null) {
            started = false;
            continue;
          }
          const x = xAt(i);
          const y = yPrice(v);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        }
        ctx.stroke();
      };
      drawLine(d.sma200, COLORS.sma200, 1.3);
      drawLine(d.sma50, COLORS.sma50, 1.3);
      drawLine(d.sma20, COLORS.sma20, 1.3);

      /* Último precio */
      const last = d.candles[view.end - 1];
      if (last) {
        const y = yPrice(last[3]);
        ctx.setLineDash([3, 4]);
        ctx.strokeStyle = "rgba(226, 232, 240, 0.35)";
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(padL + innerW, y);
        ctx.stroke();
        ctx.setLineDash([]);
        const label = priceFmt.format(last[3]);
        ctx.font = "600 10.5px system-ui, sans-serif";
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(30, 41, 59, 0.95)";
        ctx.fillRect(padL + innerW + 4, y - 9, tw + 10, 18);
        ctx.fillStyle = "#e2e8f0";
        ctx.fillText(label, padL + innerW + 9, y);
      }

      /* Volumen */
      let maxVol = 0;
      for (let i = view.start; i < view.end; i++) maxVol = Math.max(maxVol, d.volume[i] || 0);
      if (maxVol > 0) {
        for (let i = view.start; i < view.end; i++) {
          const c = d.candles[i];
          if (!c) continue;
          const up = c[3] >= c[0];
          const h = ((d.volume[i] || 0) / maxVol) * volH;
          ctx.fillStyle = up ? COLORS.upSoft : COLORS.downSoft;
          ctx.fillRect(xAt(i) - bodyW / 2, volTop + volH - h, bodyW, h);
        }
        ctx.fillStyle = COLORS.axis;
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(volFmt.format(maxVol), padL + innerW + 8, volTop + 8);
      }

      /* RSI */
      const rsiSeries = d.rsi || [];
      if (rsiH > 20) {
        const yRsi = (v) => rsiTop + rsiH - (clamp(v, 0, 100) / 100) * rsiH;
        ctx.strokeStyle = COLORS.grid;
        for (const level of [30, 70]) {
          const y = Math.round(yRsi(level)) + 0.5;
          ctx.setLineDash([3, 4]);
          ctx.beginPath();
          ctx.moveTo(padL, y);
          ctx.lineTo(padL + innerW, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = COLORS.axis;
          ctx.font = "9.5px system-ui, sans-serif";
          ctx.fillText(String(level), padL + innerW + 8, y);
        }
        ctx.strokeStyle = COLORS.rsi;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        let started = false;
        for (let i = view.start; i < view.end; i++) {
          const v = rsiSeries[i];
          if (v == null) {
            started = false;
            continue;
          }
          const x = xAt(i);
          const y = yRsi(v);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        }
        ctx.stroke();
        ctx.fillStyle = COLORS.axis;
        ctx.font = "9.5px system-ui, sans-serif";
        ctx.fillText("RSI 14", padL + 5, rsiTop + 9);
      }

      /* Fechas */
      const labels = Math.max(3, Math.min(7, Math.floor(innerW / 110)));
      ctx.fillStyle = COLORS.axis;
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let k = 0; k < labels; k++) {
        const idx = Math.round(view.start + ((view.end - 1 - view.start) * k) / (labels - 1));
        const date = d.dates[idx];
        if (!date) continue;
        const x = clamp(xAt(idx), 24, padL + innerW - 24);
        ctx.fillText(shortDate(date), x, this.height - padB + 6);
      }

      /* Crosshair */
      if (this.hover != null && this.hover >= view.start && this.hover < view.end) {
        const x = Math.round(xAt(this.hover)) + 0.5;
        ctx.strokeStyle = COLORS.crosshair;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(x, priceTop);
        ctx.lineTo(x, rsiTop + rsiH);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      this._viewInfo = { view, padL, innerW, xAt, yPrice, priceTop, priceH };
    }

    /* ---------------------------------------------------------------- */
    _onMove(event) {
      if (!this.data || !this._viewInfo) return;
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const { view, padL, innerW } = this._viewInfo;
      if (x < padL - 4 || x > padL + innerW + 4) {
        this._onLeave();
        return;
      }
      const rel = (x - padL) / innerW;
      const idx = clamp(view.start + Math.floor(rel * view.n), view.start, view.end - 1);
      this.hover = idx;
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = requestAnimationFrame(() => {
        this.draw();
        this._showTooltip(idx, event);
      });
    }

    _onLeave() {
      this.hover = null;
      this.tooltip.classList.add("hidden");
      this.draw();
    }

    _showTooltip(idx, event) {
      const d = this.data;
      const c = d.candles[idx];
      if (!c) return;
      const prev = idx > 0 ? d.candles[idx - 1][3] : null;
      const chg = prev ? (c[3] / prev - 1) * 100 : 0;
      const rsi = d.rsi[idx];
      this.tooltip.innerHTML =
        `<div class="t-date">${formatDate(d.dates[idx])}</div>` +
        `<div>O <b>${priceFmt.format(c[0])}</b> · H <b>${priceFmt.format(c[1])}</b></div>` +
        `<div>L <b>${priceFmt.format(c[2])}</b> · C <b>${priceFmt.format(c[3])}</b></div>` +
        `<div style="margin-top:3px" class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</div>` +
        (rsi != null ? `<div class="muted">RSI ${rsi.toFixed(0)}</div>` : "");
      this.tooltip.classList.remove("hidden");
      const wrap = this.canvas.parentElement;
      const tw = this.tooltip.offsetWidth;
      const th = this.tooltip.offsetHeight;
      const rect = this.canvas.getBoundingClientRect();
      let left = event.clientX - rect.left + 16;
      if (left + tw > rect.width - 8) left = event.clientX - rect.left - tw - 16;
      let top = event.clientY - rect.top + 12;
      if (top + th > rect.height - 8) top = rect.height - th - 8;
      this.tooltip.style.left = Math.max(8, left) + "px";
      this.tooltip.style.top = Math.max(8, top) + "px";
    }
  }

  function shortDate(iso) {
    const [, m, d] = iso.split("-");
    return `${d}/${m}`;
  }

  function formatDate(iso) {
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  }


  /* ------------------------------------------------------------------ */
  /* Gráfico de líneas simple (curvas de capital, IC, etc.)              */
  /* ------------------------------------------------------------------ */
  class LineChart {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.data = null;
      this._resize = this._resize.bind(this);
      if (window.ResizeObserver) {
        this._observer = new ResizeObserver(() => this._resize());
        this._observer.observe(canvas.parentElement);
      } else {
        window.addEventListener("resize", this._resize);
      }
    }

    setData(data) {
      this.data = data;
      this._resize();
    }

    resize() {
      this._resize();
    }

    _resize() {
      const rect = this.canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      this.canvas.width = Math.round(rect.width * dpr);
      this.canvas.height = Math.round(rect.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.width = rect.width;
      this.height = rect.height;
      this.draw();
    }

    draw() {
      const ctx = this.ctx;
      const d = this.data;
      if (!ctx || !this.width) return;
      ctx.clearRect(0, 0, this.width, this.height);
      if (!d || !d.series || !d.series.length) return;

      const padL = 54, padR = 12, padT = 12, padB = 24;
      const w = Math.max(10, this.width - padL - padR);
      const h = Math.max(10, this.height - padT - padB);

      let lo = Infinity, hi = -Infinity;
      d.series.forEach((serie) => {
        (serie.values || []).forEach((v) => {
          if (v == null) return;
          lo = Math.min(lo, v);
          hi = Math.max(hi, v);
        });
      });
      if (!isFinite(lo) || !isFinite(hi)) return;
      if (hi === lo) { hi += 0.01; lo -= 0.01; }
      const pad = (hi - lo) * 0.08;
      lo -= pad; hi += pad;

      const xAt = (i) => padL + (d.series[0].values.length <= 1 ? 0 : (i / (d.series[0].values.length - 1)) * w);
      const yAt = (v) => padT + h - ((v - lo) / (hi - lo)) * h;

      ctx.font = "10px system-ui, sans-serif";
      ctx.textBaseline = "middle";
      for (let g = 0; g <= 4; g++) {
        const value = lo + ((hi - lo) * g) / 4;
        const y = Math.round(yAt(value)) + 0.5;
        ctx.strokeStyle = "rgba(148, 163, 184, 0.10)";
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(padL + w, y);
        ctx.stroke();
        ctx.fillStyle = "rgba(148, 163, 184, 0.62)";
        ctx.textAlign = "right";
        ctx.fillText(value.toFixed(2), padL - 8, y);
      }

      d.series.forEach((serie) => {
        ctx.strokeStyle = serie.color || "#8b93ff";
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        let started = false;
        (serie.values || []).forEach((v, i) => {
          if (v == null) return;
          const x = xAt(i), y = yAt(v);
          if (!started) { ctx.moveTo(x, y); started = true; }
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });

      const dates = d.dates || [];
      const labels = Math.max(2, Math.min(6, Math.floor(w / 100)));
      ctx.fillStyle = "rgba(148, 163, 184, 0.62)";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let k = 0; k < labels; k++) {
        const idx = Math.round(((dates.length - 1) * k) / (labels - 1 || 1));
        const date = dates[idx];
        if (!date) continue;
        ctx.fillText(date.slice(2, 7), xAt(idx), padT + h + 7);
      }
    }
  }

  window.PriceChart = PriceChart;
  window.LineChart = LineChart;
})();
