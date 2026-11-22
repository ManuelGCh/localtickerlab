/* StockLab · lógica del dashboard */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (e) {
        /* modo privado: se ignora */
      }
    },
  };

  const nf2 = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nf0 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
  const compact = new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 2 });

  const fmt = {
    price: (v) => (v == null ? "—" : nf2.format(v)),
    num: (v, d = 0) => (v == null ? "—" : v.toFixed(d)),
    pct: (v, digits = 2, sign = true) => {
      if (v == null) return "—";
      const s = sign && v > 0 ? "+" : "";
      return s + (v * 100).toFixed(digits) + "%";
    },
    pctRaw: (v, digits = 2) => (v == null ? "—" : v.toFixed(digits) + "%"),
    money: (v) => {
      if (v == null) return "—";
      return (v < 0 ? "-$" : "$") + compact.format(Math.abs(v));
    },
    volume: (v) => (v == null ? "—" : compact.format(v)),
    date: (iso) => {
      if (!iso) return "—";
      const [y, m, d] = iso.split("-");
      return `${d}/${m}/${y}`;
    },
  };

  function storedArray(key, fallback) {
    const value = store.get(key, fallback);
    return Array.isArray(value) ? value : fallback;
  }

  const DEFAULT_HIDDEN_COLS = [
    "revenue_growth",
    "eps_surprise",
    "days_to_earnings",
    "trend",
    "momentum",
    "vol_60d",
    "max_drawdown",
  ];

  const state = {
    tab: "screen",
    universe: store.get("sl.universe", "ndx"),
    watchlist: storedArray("sl.watchlist", []).filter((s) => typeof s === "string"),
    rows: [],
    sort: { key: "score", dir: -1 },
    sortTouched: false,
    search: "",
    searchRaw: "",
    searchResults: null,
    searchTotal: 0,
    searching: false,
    searchToken: 0,
    signal: "",
    qualityMin: 0,
    noEarnings: false,
    enrichRunning: false,
    regime: {},
    hiddenCols: storedArray("sl.hiddenCols.v3", [...DEFAULT_HIDDEN_COLS]).filter(
      (s) => typeof s === "string"
    ),
    views: storedArray("sl.views", []).filter((v) => v && typeof v === "object"),
    capital: store.get("sl.capital", 10000),
    riskPct: store.get("sl.riskPct", 1),
    jobId: null,
    screenTotal: 0,
    screenCounts: {},
    loadedAll: false,
    screenDirty: false,
    latestToken: 0,
    pollTimer: null,
    hideProgressTimer: null,
    hideBtTimer: null,
    current: null,
    chart: null,
    chartRange: 126,
    compareChart: null,
    analyzeTab: "resumen",
    portfolioTab: "positions",
    watchRows: {},
    loadingWatch: false,
    btJobId: null,
    screenPollErrors: 0,
    btPollErrors: 0,
    enrichPollErrors: 0,
    btTimer: null,
    btChart: null,
    btResult: null,
    btCalibration: null,
    fundToken: 0,
    newsToken: 0,
    fundamentals: null,
  };

  const ALERT_KINDS = {
    price_above: "Precio por encima de",
    price_below: "Precio por debajo de",
    signal: "Señal igual a",
    rsi_above: "RSI por encima de",
    rsi_below: "RSI por debajo de",
    earnings_within: "Resultados en ≤ días",
  };
  const SIGNALS = ["COMPRA FUERTE", "COMPRA", "NEUTRAL", "VENTA", "VENTA FUERTE"];

  const OPTIONAL_COLUMNS = {
    pct_score: "Percentil",
    rs_qqq_3m: "Fuerza vs QQQ",
    revenue_growth: "Crecimiento",
    eps_surprise: "Sorpresa EPS",
    days_to_earnings: "Días a resultados",
    trend: "Tendencia",
    momentum: "Momento",
    rsi: "RSI",
    ret_3m: "Retorno 3M",
    ret_1y: "Retorno 1A",
    vol_60d: "Volatilidad",
    max_drawdown: "Máx. caída",
  };

  const ACTION_CLASS = {
    "COMPRA FUERTE": "sig-buy-strong",
    "COMPRA": "sig-buy",
    "NEUTRAL": "sig-neutral",
    "VENTA": "sig-sell",
    "VENTA FUERTE": "sig-sell-strong",
  };

  function actionBadge(action) {
    if (!action) return "—";
    return `<span class="badge-action ${ACTION_CLASS[action] || "sig-neutral"}">${action}</span>`;
  }

  /* ------------------------------------------------------------------ */
  async function api(path, params = {}) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") query.set(k, v);
    });
    const url = query.toString() ? `${path}?${query}` : path;
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      throw new Error(`Respuesta no válida del servidor (${res.status})`);
    }
    if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
    return data;
  }

  /* ------------------------------------------------------------------ */
  function parseHash() {
    const raw = location.hash.replace(/^#/, "");
    if (!raw) return {};
    const [tab, query] = raw.split("?");
    const params = new URLSearchParams(query || "");
    const atabs = ["resumen", "resultados", "noticias", "metricas", "comparar"];
    const ptabs = ["positions", "watch", "alerts", "system"];
    const atab = params.get("atab") || "";
    const ptab = params.get("ptab") || "";
    const signal = params.get("signal") || "";
    return {
      tab: ["screen", "analyze", "backtest", "portfolio"].includes(tab) ? tab : "screen",
      universe: params.get("universe") || "",
      signal: SIGNALS.includes(signal) ? signal : "",
      symbol: (params.get("symbol") || "").toUpperCase() || "",
      atab: atabs.includes(atab) ? atab : "",
      ptab: ptabs.includes(ptab) ? ptab : "",
    };
  }

  function init() {
    state.initializing = true;
    wireTabs();
    wireScreen();
    wireAnalyze();
    wireWatch();
    wireBacktest();
    wirePortfolio();
    loadStatus();
    restoreUniverse();
    refreshWatchBadge();
    updateScanNote();
    const initial = parseHash();
    if (["ndx", "all", "watchlist"].includes(initial.universe)) {
      $("#universe").value = initial.universe;
      state.universe = initial.universe;
      store.set("sl.universe", initial.universe);
    }
    if (initial.signal) {
      $("#signalFilter").value = initial.signal;
      state.signal = initial.signal;
    }
    setTab(initial.tab || "screen");
    if (initial.atab) setAnalyzeTab(initial.atab);
    if (initial.atab === "comparar" && !initial.symbol) {
      $("#analyzeEmpty").classList.add("hidden");
      $("#analyzeContent").classList.remove("hidden");
      $("#analyzeContent").classList.add("compare-only");
      $("#compareBack").classList.remove("hidden");
    }
    if (initial.ptab) setPortfolioTab(initial.ptab);
    if (initial.symbol) {
      $("#symbolSearch").value = initial.symbol;
      analyze(initial.symbol);
    }
    loadLatest(
      undefined,
      !initial.symbol && (initial.tab || "screen") === "screen"
    );
    refreshScanStatus();
    window.setInterval(refreshScanStatus, 3000);
    window.addEventListener("hashchange", () => {
      const initial = parseHash();
      if (initial.symbol && (!state.current || state.current.symbol !== initial.symbol)) {
        setTab("analyze");
        analyze(initial.symbol);
        return;
      }
      if (
        ["ndx", "all", "watchlist"].includes(initial.universe) &&
        initial.universe !== state.universe
      ) {
        state.universe = initial.universe;
        $("#universe").value = initial.universe;
        store.set("sl.universe", initial.universe);
        state.loadedAll = false;
        updateScanNote();
        loadLatest(initial.universe);
      }
      if (initial.atab && initial.atab !== state.analyzeTab) setAnalyzeTab(initial.atab);
      if (initial.ptab && initial.ptab !== state.portfolioTab) setPortfolioTab(initial.ptab);
      if (initial.tab && initial.tab !== state.tab) setTab(initial.tab);
    });
    state.initializing = false;
    updateHash();
  }

  function wireTabs() {
    $$(".tab").forEach((btn) => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });
  }

  function updateHash() {
    if (state.initializing) return;
    const params = new URLSearchParams();
    if (state.tab === "screen" && state.universe) {
      params.set("universe", state.universe);
      if (state.signal) params.set("signal", state.signal);
    }
    if (state.tab === "analyze") {
      if (state.current) params.set("symbol", state.current.symbol);
      if (state.analyzeTab && state.analyzeTab !== "resumen") params.set("atab", state.analyzeTab);
    }
    if (state.tab === "portfolio" && state.portfolioTab !== "positions") {
      params.set("ptab", state.portfolioTab);
    }
    const suffix = params.toString();
    history.replaceState(null, "", `#${state.tab}${suffix ? `?${suffix}` : ""}`);
  }

  function setTab(tab) {
    state.tab = tab;
    $$(".tab").forEach((b) => {
      const active = b.dataset.tab === tab;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
    $$(".view").forEach((v) => v.classList.add("hidden"));
    const view = $("#view-" + tab);
    if (view) view.classList.remove("hidden");
    if (tab === "portfolio") {
      loadPortfolio();
      renderWatchlist();
    }
    if (tab === "screen") {
      if (!state.rows.length && !state.jobId) loadLatest(state.universe, false);
      else if (state.screenDirty) {
        state.screenDirty = false;
        renderScreenTable();
      }
    }
    if (tab === "backtest" && !state.btResult) loadBacktest();
    updateHash();
  }

  async function refreshScanStatus() {
    const banner = $("#scanBanner");
    if (!banner) return;
    try {
      const scan = await api("/api/scan");
      const running = scan && scan.running && !scan.stale;
      if (!running) {
        if (scan && scan.phase === "done" && scan.finished_at) {
          const finished = Date.parse(scan.finished_at);
          if (Date.now() - finished < 60000) {
            banner.classList.remove("hidden");
            $("#scanTitle").textContent = "Escaneo completado";
            $("#scanDetail").textContent = `${nf0.format(scan.done || 0)} valores · ${nf0.format(scan.errors || 0)} errores`;
            $("#scanBar").style.width = "100%";
            $("#scanPct").textContent = "100%";
            return;
          }
        }
        if (scan && scan.stale) {
          banner.classList.remove("hidden");
          $("#scanTitle").textContent = "Escaneo interrumpido";
          $("#scanDetail").textContent = scan.message || "Sin actualizaciones recientes";
          $("#scanBar").style.width = "0%";
          $("#scanPct").textContent = "";
          return;
        }
        banner.classList.add("hidden");
        return;
      }
      const pct = scan.total ? Math.round((scan.done / scan.total) * 100) : 0;
      const phases = {
        preparando: "Preparando escaneo",
        precios: "Descargando precios y calculando",
        enriquecimiento: "Enriqueciendo fundamentales",
        alertas: "Comprobando alertas",
        done: "Escaneo completado",
      };
      banner.classList.remove("hidden");
      $("#scanTitle").textContent = (phases[scan.phase] || "Escaneando") + "…";
      $("#scanDetail").textContent = `${nf0.format(scan.done || 0)} / ${nf0.format(scan.total || 0)} valores · ${nf0.format(scan.errors || 0)} errores`;
      $("#scanBar").style.width = pct + "%";
      $("#scanPct").textContent = pct + "%";
    } catch (e) {
      /* servidor antiguo o sin escaneo */
    }
  }

  /* ------------------------------------------------------------------ */
  async function loadStatus() {
    try {
      const data = await api("/api/status");
      const db = data.db || {};
      const universeRun =
        state.universe !== "watchlist"
          ? ((data.universes || {})[state.universe] || {}).last_run
          : null;
      const stamp = (universeRun && universeRun.finished_at) || data.last_run;
      let stale = false;
      let label = "Sin datos";
      if (stamp) {
        const when = new Date(stamp);
        stale = Date.now() - when.getTime() > 2 * 86400 * 1000;
        label =
          "Escaneo " +
          when.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit" }) +
          " " +
          when.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
      }
      $("#dbChipText").textContent = label;
      $("#dbChip").title = `${nf0.format(db.cached_symbols || 0)} valores · ${db.db_mb || 0} MB · ${
        stale ? "datos antiguos, ejecuta el escaneo diario" : "última actualización"
      }`;
      const dot = $("#dbChip .dot");
      if (dot) dot.style.background = stale ? "var(--warn)" : "var(--pos)";
      $("#footDisclaimer").textContent = data.disclaimer || "";
    } catch (e) {
      $("#dbChipText").textContent = "sin conexión";
    }
  }

  /* ================= SCREENER ================= */
  function restoreUniverse() {
    const select = $("#universe");
    const allowed = ["ndx", "all", "watchlist"];
    let stored = store.get("sl.universe", "ndx");
    if (typeof stored !== "string" || !allowed.includes(stored)) stored = "ndx";
    select.value = stored;
    state.universe = stored;
    store.set("sl.universe", stored);
    if (stored === "all") $("#maxSymbols").value = "600";
    if (stored === "watchlist") $("#maxSymbols").value = "0";
    select.addEventListener("change", () => {
      state.universe = select.value;
      store.set("sl.universe", state.universe);
      updateHash();
      if (state.universe === "all") $("#maxSymbols").value = "600";
      if (state.universe === "watchlist") $("#maxSymbols").value = "0";
      updateScanNote();
      state.loadedAll = false;
      loadLatest(state.universe);
    });
  }

  function currentViewConfig() {
    return {
      universe: $("#universe").value,
      signal: $("#signalFilter").value,
      qualityMin: $("#qualityFilter").value,
      noEarnings: $("#noEarnings").checked,
      maxSymbols: $("#maxSymbols").value,
      minPrice: $("#minPrice").value,
      minCap: $("#minCap").value,
      excludeFunds: $("#excludeFunds").checked,
      hiddenCols: [...state.hiddenCols],
    };
  }

  function renderViewsPanel() {
    const panel = $("#viewsPanel");
    if (!panel) return;
    panel.innerHTML =
      `<button class="btn ghost" id="saveView">Guardar vista actual</button>` +
      `<div class="views-list">` +
      (state.views.length
        ? state.views
            .map(
              (view, index) => `<div class="view-item">
                <button class="view-apply" data-view-apply="${index}">${escapeHtml(view.name)}</button>
                <button class="icon-btn" data-view-delete="${index}" title="Eliminar">✕</button>
              </div>`
            )
            .join("")
        : `<span class="muted small">Sin vistas guardadas.</span>`) +
      `</div>`;
    panel.querySelector("#saveView").addEventListener("click", saveView);
    panel.querySelectorAll("[data-view-apply]").forEach((btn) => {
      btn.addEventListener("click", () => applyView(Number(btn.dataset.viewApply)));
    });
    panel.querySelectorAll("[data-view-delete]").forEach((btn) => {
      btn.addEventListener("click", () => deleteView(Number(btn.dataset.viewDelete)));
    });
  }

  function saveView() {
    const name = (window.prompt("Nombre de la vista:") || "").trim();
    if (!name) return;
    const config = currentViewConfig();
    const existing = state.views.findIndex((view) => view.name === name);
    if (existing >= 0) state.views[existing] = { name, config };
    else state.views.push({ name, config });
    store.set("sl.views", state.views);
    renderViewsPanel();
  }

  function applyView(index) {
    const view = state.views[index];
    if (!view) return;
    const config = view.config || {};
    const universe = ["ndx", "all", "watchlist"].includes(config.universe)
      ? config.universe
      : "ndx";
    $("#universe").value = universe;
    state.universe = universe;
    $("#maxSymbols").value = config.maxSymbols ?? "600";
    $("#minPrice").value = config.minPrice ?? "3";
    $("#minCap").value = config.minCap ?? "300000000";
    $("#excludeFunds").checked = config.excludeFunds !== false;
    const signal = SIGNALS.includes(config.signal) ? config.signal : "";
    $("#signalFilter").value = signal;
    state.signal = signal;
    const qualityMin = ["", "45", "60", "75"].includes(String(config.qualityMin ?? ""))
      ? String(config.qualityMin ?? "")
      : "";
    $("#qualityFilter").value = qualityMin;
    state.qualityMin = Number(qualityMin) || 0;
    $("#noEarnings").checked = !!config.noEarnings;
    state.noEarnings = !!config.noEarnings;
    state.hiddenCols = Array.isArray(config.hiddenCols)
      ? config.hiddenCols.filter((key) => typeof key === "string")
      : [...DEFAULT_HIDDEN_COLS];
    store.set("sl.hiddenCols.v3", state.hiddenCols);
    store.set("sl.universe", state.universe);
    state.loadedAll = false;
    buildColumnPanel();
    applyColumns();
    updateScanNote();
    updateHash();
    renderScreenTable();
    $("#viewsPanel").classList.add("hidden");
    loadLatest(universe);
  }

  function deleteView(index) {
    state.views.splice(index, 1);
    store.set("sl.views", state.views);
    renderViewsPanel();
  }

  function buildColumnPanel() {
    const panel = $("#columnsPanel");
    if (!panel) return;
    store.set("sl.hiddenCols.v3", state.hiddenCols);
    panel.innerHTML =
      Object.entries(OPTIONAL_COLUMNS)
        .map(
          ([key, label]) =>
            `<label class="col-option"><input type="checkbox" data-col-toggle="${key}" ${
              state.hiddenCols.includes(key) ? "" : "checked"
            } /> ${label}</label>`
        )
        .join("") + `<button class="btn ghost" id="resetColumns">Restaurar</button>`;
    panel.querySelectorAll("[data-col-toggle]").forEach((box) => {
      box.addEventListener("change", () => {
        const key = box.dataset.colToggle;
        if (box.checked) {
          state.hiddenCols = state.hiddenCols.filter((item) => item !== key);
        } else if (!state.hiddenCols.includes(key)) {
          state.hiddenCols.push(key);
        }
        store.set("sl.hiddenCols.v3", state.hiddenCols);
        applyColumns();
      });
    });
    panel.querySelector("#resetColumns").addEventListener("click", () => {
      state.hiddenCols = [...DEFAULT_HIDDEN_COLS];
      store.set("sl.hiddenCols.v3", state.hiddenCols);
      buildColumnPanel();
      applyColumns();
    });
    applyColumns();
  }

  function applyColumns() {
    document.querySelectorAll("#screenTable [data-col]").forEach((cell) => {
      cell.classList.toggle("col-hidden", state.hiddenCols.includes(cell.dataset.col));
    });
  }

  function updateScanNote() {
    const universe = $("#universe").value;
    const note = $("#scanNote");
    const num = Number($("#maxSymbols").value);
    if (universe === "watchlist") {
      note.textContent = "Se analizan solo los valores marcados con estrella en la watchlist.";
      return;
    }
    if (universe === "ndx") {
      note.textContent = "NASDAQ-100: 101 valores. La primera ejecución tarda ~1 min; después todo sale de la caché.";
      return;
    }
    if (num === 0) {
      note.innerHTML =
        "<strong>Escaneo completo:</strong> ~6.900 valores. La primera vez puede tardar 20-35 min. El trabajo sigue en el servidor si cierras la pestaña y los datos quedan en caché.";
    } else {
      note.innerHTML = `<strong>NASDAQ Composite:</strong> se analizan los ${num} valores con más liquidez. Primera ejecución ~${Math.max(1, Math.ceil(num / 300))} min; después, segundos.`;
    }
  }

  function wireScreen() {
    $("#runScreen").addEventListener("click", () => startScreen(false));
    $("#filtersBtn").addEventListener("click", () => {
      const panel = $("#filtersBtn").closest(".controls");
      panel.classList.toggle("is-expanded");
      $("#filtersBtn").textContent = panel.classList.contains("is-expanded")
        ? "Ocultar filtros"
        : "Filtros";
    });
    $("#regimeToggle").addEventListener("click", () => {
      const panel = $("#regimePanel");
      panel.classList.toggle("is-expanded");
      $("#regimeToggle").textContent = panel.classList.contains("is-expanded")
        ? "Ocultar ▴"
        : "Detalle ▾";
    });
    $("#dataBtn").addEventListener("click", (e) => {
      e.stopPropagation();
      $("#columnsPanel").classList.add("hidden");
      $("#viewsPanel").classList.add("hidden");
      $("#dataPanel").classList.toggle("hidden");
    });
    $("#viewsBtn").addEventListener("click", (e) => {
      e.stopPropagation();
      $("#columnsPanel").classList.add("hidden");
      $("#dataPanel").classList.add("hidden");
      renderViewsPanel();
      $("#viewsPanel").classList.toggle("hidden");
    });
    $("#scanAll").addEventListener("click", () => {
      $("#universe").value = "all";
      state.universe = "all";
      $("#maxSymbols").value = "0";
      $("#minCap").value = "0";
      $("#minPrice").value = "1";
      store.set("sl.universe", "all");
      updateScanNote();
      updateHash();
      const ok = window.confirm(
        "Escaneo completo del NASDAQ Composite (~5.000 valores con precio ≥ 1 $). " +
          "La primera ejecución tarda 15-25 minutos y continúa en el servidor aunque cierres la pestaña. ¿Empezar?"
      );
      if (ok) startScreen(false, false);
    });
    $("#cancelScreen").addEventListener("click", cancelScreen);
    let searchTimer = null;
    buildColumnPanel();
    $("#columnsBtn").addEventListener("click", (e) => {
      e.stopPropagation();
      $("#dataPanel").classList.add("hidden");
      $("#viewsPanel").classList.add("hidden");
      $("#columnsPanel").classList.toggle("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".col-menu")) {
        $("#columnsPanel").classList.add("hidden");
        $("#dataPanel").classList.add("hidden");
        $("#viewsPanel").classList.add("hidden");
      }
    });

    $("#tableSearch").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      const raw = e.target.value.trim();
      searchTimer = setTimeout(async () => {
        state.search = raw.toLowerCase();
        state.searchRaw = raw;
        state.sortTouched = false;
        state.searchResults = null;
        state.searchTotal = 0;
        const needServer =
          raw.length > 0 && state.jobId && state.screenTotal > state.rows.length;
        if (needServer) {
          state.searching = true;
          renderScreenTable();
          const token = ++state.searchToken;
          try {
            const res = await api("/api/screen/find", { id: state.jobId, q: raw });
            if (token !== state.searchToken) return;
            state.searchResults = res.rows || [];
            state.searchTotal = res.total || 0;
          } catch (err) {
            if (token !== state.searchToken) return;
            state.searchResults = [];
            state.searchTotal = 0;
          }
          state.searching = false;
        }
        renderScreenTable();
      }, 220);
    });
    $("#screenCards").addEventListener("click", (ev) => {
      const card = ev.target.closest("[data-card-symbol]");
      if (card) openSymbol(card.dataset.cardSymbol);
    });
    $("#screenBody").addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-action]");
      if (!el) return;
      const symbol = el.dataset.symbol;
      if (el.dataset.action === "star") {
        ev.stopPropagation();
        toggleWatch(symbol);
        renderScreenTable();
      } else {
        openSymbol(symbol);
      }
    });
    $("#signalFilter").addEventListener("change", (e) => {
      state.signal = e.target.value;
      updateHash();
      renderScreenTable();
    });
    $("#qualityFilter").addEventListener("change", (e) => {
      state.qualityMin = Number(e.target.value) || 0;
      renderScreenTable();
    });
    $("#noEarnings").addEventListener("change", (e) => {
      state.noEarnings = e.target.checked;
      renderScreenTable();
    });
    $("#enrichBtn").addEventListener("click", enrichFundamentals);
    $("#loadAll").addEventListener("click", loadAllRows);
    $("#exportCsv").addEventListener("click", () => {
      if (!state.rows.length) return;
      const query = state.jobId ? `id=${state.jobId}` : `universe=${state.universe}`;
      window.location.href = `/api/screen/export?${query}`;
    });
    $("#maxSymbols").addEventListener("change", updateScanNote);
    $$("#screenTable thead th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sort.key === key) state.sort.dir *= -1;
        else state.sort = { key, dir: key === "symbol" ? 1 : -1 };
        state.sortTouched = true;
        renderScreenTable();
      });
    });
  }

  async function resumeScreen(token) {
    const saved = store.get("sl.screenJob", null);
    if (!saved) return false;
    try {
      const job = await api("/api/screen/status", { id: saved });
      if (token !== state.latestToken) return false;
      if (job.universe !== state.universe) return false;
      if (job.status === "running") {
        state.jobId = job.id;
        state.universe = job.universe;
        $("#universe").value = job.universe;
        state.rows = job.rows || [];
        state.screenTotal = job.done || job.total || 0;
        state.screenCounts = job.action_counts || {};
        state.regime = job.regime || {};
        showProgress(job);
        renderScreenTable();
        pollScreen();
        return true;
      }
      if (job.status === "done" && job.rows && job.rows.length) {
        state.jobId = job.id;
        state.universe = job.universe;
        $("#universe").value = job.universe;
        state.rows = job.rows;
        state.screenTotal = job.done || job.rows.length;
        state.screenCounts = job.action_counts || {};
        state.regime = job.regime || {};
        renderScreenTable();
        store.set("sl.screenJob", null);
        return true;
      }
    } catch (e) {
      /* trabajo perdido tras reiniciar el servidor */
    }
    if (token !== state.latestToken) return false;
    store.set("sl.screenJob", null);
    return false;
  }

  async function loadLatest(universeArg, autoStart = false) {
    const token = (state.latestToken = (state.latestToken || 0) + 1);
    if (await resumeScreen(token)) return;
    if (token !== state.latestToken) return;
    state.enrichRunning = false;
    $("#enrichBtn").disabled = false;
    $("#enrichInfo").textContent = "";
    $("#runScreen").disabled = false;
    $("#cancelScreen").classList.add("hidden");
    $("#progressPanel").classList.add("hidden");
    const universe = universeArg || state.universe;
    if (universe === "watchlist") {
      state.rows = [];
      state.jobId = null;
      state.screenTotal = 0;
      state.screenCounts = {};
      renderScreenTable();
      loadStatus();
      return;
    }
    try {
      const latest = await api("/api/screen/latest", { universe });
      if (token !== state.latestToken) return;
      if (latest && latest.rows && latest.rows.length) {
        $("#universe").value = universe;
        state.universe = universe;
        const params = latest.params || {};
        if (params.min_price != null) $("#minPrice").value = params.min_price;
        if (params.min_market_cap != null) $("#minCap").value = String(params.min_market_cap);
        if (params.max_symbols != null) $("#maxSymbols").value = String(params.max_symbols);
        if (params.exclude_funds != null) $("#excludeFunds").checked = !!params.exclude_funds;
        updateScanNote();
        state.rows = latest.rows;
        state.jobId = latest.id;
        state.search = "";
        state.searchRaw = "";
        state.searchResults = null;
        state.searchTotal = 0;
        state.searching = false;
        state.screenTotal = latest.done || latest.rows.length;
        state.screenCounts = latest.action_counts || {};
        state.regime = latest.regime || {};
        $("#progressPanel").classList.add("hidden");
        if (latest.status === "running") {
          state.jobId = latest.id;
          showProgress(latest);
          pollScreen();
        }
        renderScreenTable();
      } else {
        state.rows = [];
        state.jobId = null;
        state.screenTotal = 0;
        state.screenCounts = {};
        $("#progressPanel").classList.add("hidden");
        renderScreenTable();
        if (autoStart && !sessionStorage.getItem("sl.autostart")) {
          sessionStorage.setItem("sl.autostart", "1");
          startScreen(false);
        }
      }
      loadStatus();
    } catch (e) {
      /* silencioso: el usuario puede lanzarlo a mano */
    }
  }

  async function startScreen(force, confirmLong = true) {
    const universe = $("#universe").value;
    state.universe = universe;
    if (universe === "watchlist" && !state.watchlist.length) {
      setTab("portfolio");
      return;
    }
    if (confirmLong && universe === "all" && Number($("#maxSymbols").value) === 0) {
      const ok = window.confirm(
        "Vas a analizar TODO el NASDAQ Composite (~6.900 valores). La primera ejecución puede tardar 20-35 minutos y seguirá en segundo plano si cierras la pestaña. ¿Continuar?"
      );
      if (!ok) return;
    }
    const params = {
      universe,
      min_price: $("#minPrice").value || 0,
      min_market_cap: $("#minCap").value || 0,
      max_symbols: $("#maxSymbols").value || 0,
      exclude_funds: $("#excludeFunds").checked ? 1 : 0,
      force: force ? 1 : 0,
    };
    if (universe === "watchlist") params.symbols = state.watchlist.join(",");

    $("#runScreen").disabled = true;
    clearTimeout(state.hideProgressTimer);
    state.hideProgressTimer = null;
    try {
      const job = await api("/api/screen/start", params);
      store.set("sl.screenJob", job.id);
      if (universe !== state.universe) {
        $("#runScreen").disabled = false;
        return;
      }
      state.jobId = job.id;
      state.loadedAll = false;
      state.search = "";
      state.searchRaw = "";
      state.searchResults = null;
      state.searchTotal = 0;
      state.searching = false;
      state.enrichRunning = false;
      state.regime = {};
      state.screenPollErrors = 0;
      $("#tableSearch").value = "";
      $("#enrichInfo").textContent = "";
      $("#enrichBtn").disabled = false;
      state.screenTotal = job.total || 0;
      state.screenCounts = job.action_counts || {};
      state.rows = job.rows || [];
      showProgress(job);
      renderScreenTable();
      pollScreen();
    } catch (e) {
      $("#runScreen").disabled = false;
      showScreenError(e.message);
    }
  }

  async function cancelScreen() {
    if (!state.jobId) return;
    try {
      await api("/api/screen/cancel", { id: state.jobId });
    } catch (e) {
      /* nada */
    }
  }

  function pollScreen() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    const jobId = state.jobId;
    const tick = async () => {
      if (!jobId || state.jobId !== jobId) return;
      try {
        const job = await api("/api/screen/status", { id: jobId });
        if (state.jobId !== jobId) return;
        if (job.universe && job.universe !== state.universe) return;
        state.screenPollErrors = 0;
        state.rows = job.rows && job.rows.length ? job.rows : state.rows;
        state.screenTotal = job.done || state.screenTotal;
        state.screenCounts = job.action_counts || state.screenCounts;
        state.regime = job.regime || state.regime;
        showProgress(job);
        renderScreenTable();
        if (job.status === "running") {
          state.pollTimer = setTimeout(tick, 1300);
        } else {
          store.set("sl.screenJob", null);
          $("#runScreen").disabled = false;
          $("#cancelScreen").classList.add("hidden");
          hideProgressSoon();
          loadStatus();
          if (job.status === "error") showScreenError(job.error || "Error durante el análisis");
          if (job.status === "cancelled") {
            $("#progressTitle").textContent = "Análisis cancelado";
          }
        }
      } catch (e) {
        state.screenPollErrors += 1;
        if (state.screenPollErrors >= 5) {
          store.set("sl.screenJob", null);
          $("#runScreen").disabled = false;
          $("#cancelScreen").classList.add("hidden");
          showScreenError("Se perdió la conexión con el escaneo. Vuelve a lanzarlo.");
          return;
        }
        state.pollTimer = setTimeout(tick, 2500);
      }
    };
    state.pollTimer = setTimeout(tick, 400);
  }

  function showProgress(job) {
    clearTimeout(state.hideProgressTimer);
    state.hideProgressTimer = null;
    const panel = $("#progressPanel");
    panel.classList.remove("hidden");
    $("#cancelScreen").classList.toggle("hidden", job.status !== "running");
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
    $("#progressFill").style.width = pct + "%";
    $("#progressPct").textContent = pct + "%";
    const label = {
      ndx: "NASDAQ-100",
      all: "NASDAQ Composite",
      watchlist: "watchlist",
    }[job.universe] || job.universe;
    $("#progressTitle").textContent =
      job.status === "running" ? `Analizando ${label}…` : `Análisis ${job.status === "done" ? "completado" : job.status}`;
    $("#progressDetail").textContent = `${nf0.format(job.done)} / ${nf0.format(job.total)} valores` + (job.errors ? ` · ${job.errors} errores` : "");
    const parts = [];
    if (job.status === "running") {
      if (job.eta) parts.push(`quedan ~${formatEta(job.eta)}`);
      parts.push(`${job.done}/${job.total}`);
      if (job.errors) parts.push(`${job.errors} sin datos`);
    } else {
      parts.push(`${nf0.format(job.done)} procesados`);
      if (job.elapsed) parts.push(`${formatEta(job.elapsed)} en total`);
    }
    $("#progressMeta").textContent = parts.join(" · ");
  }

  function hideProgressSoon() {
    clearTimeout(state.hideProgressTimer);
    state.hideProgressTimer = setTimeout(() => {
      $("#progressPanel").classList.add("hidden");
    }, 6000);
  }

  function formatEta(seconds) {
    if (!seconds || seconds < 1) return "unos segundos";
    if (seconds < 60) return `${Math.round(seconds)} s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    if (m < 60) return `${m} min ${s} s`;
    return `${Math.floor(m / 60)} h ${m % 60} min`;
  }

  function showScreenError(message) {
    $("#progressPanel").classList.remove("hidden");
    $("#progressTitle").textContent = "No se pudo completar el análisis";
    $("#progressDetail").textContent = message;
    $("#progressFill").style.width = "0%";
    $("#progressMeta").textContent = "";
  }

  function scoreColor(score) {
    if (score >= 80) return "#34d399";
    if (score >= 65) return "#22d3ee";
    if (score >= 50) return "#a5b4fc";
    if (score >= 35) return "#fbbf24";
    return "#f87171";
  }

  async function loadAllRows() {
    if (!state.jobId) return;
    $("#loadAll").disabled = true;
    try {
      const data = await api("/api/screen/all", { id: state.jobId });
      if (data.rows && data.rows.length) {
        state.rows = data.rows;
        state.loadedAll = true;
        renderScreenTable();
        $("#loadAll").classList.add("hidden");
      }
    } catch (e) {
      /* se mantiene el top actual */
    } finally {
      $("#loadAll").disabled = false;
    }
  }

  function isEnriched(row) {
    return !!(row.enriched || row.quality != null);
  }

  function renderScreenCards(rows) {
    const box = $("#screenCards");
    if (!box) return;
    const limit = 150;
    const mobileRows = state.search ? rows : rows.slice(0, limit);
    const note =
      rows.length > mobileRows.length
        ? `<div class="muted small" style="padding:4px 2px">Mostrando ${limit} de ${nf0.format(rows.length)}. Usa la búsqueda o los filtros para acotar.</div>`
        : "";
    box.innerHTML =
      note +
      mobileRows
      .map((row) => {
        const change = row.change_pct;
        return `<div class="stock-card" data-card-symbol="${escapeAttr(row.symbol)}">
          <div class="stock-card-head">
            <div class="stock-card-id">
              <strong>${escapeHtml(row.symbol)}</strong>
              <span>${escapeHtml(row.name || "")}</span>
            </div>
            <div class="stock-card-price">
              <strong>${fmt.price(row.price)}</strong>
              <span class="${change == null ? "neutral" : change >= 0 ? "pos" : "neg"}">${fmt.pct(change)}</span>
            </div>
          </div>
          <div class="stock-card-tags">
            ${actionBadge(row.action)}
            ${qualityCell(row)}
            <span class="tag">${Math.round(row.score)}/100</span>
            <span class="tag">Obj. ${fmt.price(row.target_3m)}</span>
            <span class="tag ${(row.upside_3m || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(row.upside_3m, 1)}</span>
          </div>
          <div class="stock-card-foot">
            <span class="muted small">Toca para analizar</span>
          </div>
        </div>`;
      })
      .join("");
  }

  function qualityCell(row) {
    if (row.quality != null) {
      const color = scoreColor(row.quality);
      return `<span class="qbadge" title="Calidad ${Math.round(row.quality)}/100 (contexto, no puntúa)" style="color:${color};background:${color}18;border-color:${color}44">${Math.round(row.quality)}</span>`;
    }
    return isEnriched(row)
      ? `<span class="muted" title="Sin datos financieros para este valor">s/d</span>`
      : `<span class="muted" title="Sin enriquecer: pulsa «+ Fundamentales»">—</span>`;
  }

  function pctCell(row, value) {
    if (value != null) {
      return `<span class="${value >= 0 ? "pos" : "neg"}">${fmt.pct(value, 1)}</span>`;
    }
    return isEnriched(row)
      ? `<span class="muted" title="Sin dato">s/d</span>`
      : `<span class="muted" title="Sin enriquecer: pulsa «+ Fundamentales»">—</span>`;
  }

  function daysCell(row) {
    if (row.days_to_earnings != null) {
      return row.days_to_earnings <= 7
        ? `<span class="warn-text" title="Resultados próximos">${row.days_to_earnings}</span>`
        : `${row.days_to_earnings}`;
    }
    return isEnriched(row)
      ? `<span class="muted" title="Sin fecha confirmada en los próximos 30 días">sin fecha</span>`
      : `<span class="muted" title="Sin enriquecer: pulsa «+ Fundamentales»">—</span>`;
  }

  function updateEnrichHint() {
    if (state.enrichRunning || !state.rows.length) {
      if (!state.rows.length) $("#enrichInfo").textContent = "";
      return;
    }
    const pending = state.rows.filter((r) => !isEnriched(r)).length;
    $("#enrichInfo").textContent = pending
      ? `${nf0.format(pending)} filas sin fundamentales`
      : "";
  }

  async function enrichFundamentals() {
    if (!state.jobId) return;
    const wasLoadedAll = state.loadedAll;
    const limit = Number($("#enrichLimit").value) || 300;
    $("#enrichBtn").disabled = true;
    state.enrichRunning = true;
    state.enrichPollErrors = 0;
    $("#enrichInfo").textContent = "iniciando…";
    try {
      const job = await api("/api/fundamentals/enrich", { id: state.jobId, limit });
      updateEnrichInfo(job);
      pollEnrichment(wasLoadedAll);
    } catch (e) {
      $("#enrichInfo").textContent = e.message;
      $("#enrichBtn").disabled = false;
      state.enrichRunning = false;
      updateEnrichHint();
    }
  }

  function updateEnrichInfo(job) {
    if (!job || !job.enrich_total) {
      $("#enrichInfo").textContent = "";
      return;
    }
    const pct = Math.round((job.enrich_done / job.enrich_total) * 100);
    state.enrichRunning = job.enrich_status === "running";
    $("#enrichInfo").textContent =
      job.enrich_status === "running"
        ? `fundamentales ${job.enrich_done}/${job.enrich_total} (${pct}%)`
        : `fundamentales ${job.enrich_done}/${job.enrich_total}`;
  }

  function pollEnrichment(wasLoadedAll) {
    const jobId = state.jobId;
    setTimeout(async () => {
      if (!jobId || state.jobId !== jobId) return;
      try {
        const job = await api("/api/screen/status", { id: jobId });
        if (state.jobId !== jobId) return;
        if (job.universe && job.universe !== state.universe) return;
        state.enrichPollErrors = 0;
        if (job.rows && job.rows.length) state.rows = job.rows;
        state.screenCounts = job.action_counts || state.screenCounts;
        state.regime = job.regime || state.regime;
        updateEnrichInfo(job);
        renderScreenTable();
        if (job.enrich_status === "running") {
          pollEnrichment(wasLoadedAll);
          return;
        }
        $("#enrichBtn").disabled = false;
        state.enrichRunning = false;
        updateEnrichHint();
        if (wasLoadedAll) loadAllRows();
        loadStatus();
      } catch (e) {
        state.enrichPollErrors += 1;
        if (state.enrichPollErrors >= 5) {
          $("#enrichInfo").textContent = "Se perdió la conexión con el enriquecimiento.";
          $("#enrichBtn").disabled = false;
          state.enrichRunning = false;
          return;
        }
        pollEnrichment(wasLoadedAll);
      }
    }, 1500);
  }

  function renderRegime() {
    const panel = $("#regimePanel");
    const regime = state.regime || {};
    const breadth = regime.breadth;
    if (!breadth || !breadth.n) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    const score = breadth.score || 0;
    const label = $("#regimeLabel");
    label.textContent = `${breadth.label || "Mixto"} · ${Math.round(score)}/100`;
    label.className =
      "regime-badge " + (score >= 60 ? "good" : score <= 40 ? "bad" : "mid");

    const kpi = (k, v, s) =>
      `<div class="regime-kpi"><span class="k">${k}</span><span class="v">${v}</span>${s ? `<span class="s">${s}</span>` : ""}</div>`;
    $("#regimeKpis").innerHTML = [
      kpi("Sobre SMA200", fmt.pctRaw(breadth.above_sma200, 0), `${nf0.format(breadth.n)} valores`),
      kpi("Amplitud", fmt.pctRaw(breadth.advancers, 0), "valores que suben"),
      kpi("QQQ 3M", fmt.pct(breadth.qqq_ret_3m, 1), breadth.qqq_above_sma200 ? "sobre SMA200" : "bajo SMA200"),
      kpi("Mediana 1M", fmt.pct(breadth.median_ret_1m, 1), `3M ${fmt.pct(breadth.median_ret_3m, 1)}`),
      kpi("Máx / Mín 52s", `${nf0.format(breadth.new_highs || 0)} / ${nf0.format(breadth.new_lows || 0)}`, "valores"),
    ].join("");

    const sectors = (regime.sectors || []).slice(0, 10);
    const toggle = $("#regimeToggle");
    if (toggle) toggle.textContent = panel.classList.contains("is-expanded") ? "Ocultar ▴" : "Detalle ▾";
    $("#sectorHeat").innerHTML = sectors
      .map((item) => {
        const value = item.ret_3m || 0;
        const cls = value >= 0 ? "up" : "down";
        return `<span class="sector-chip ${cls}" title="${item.n} valores · ${fmt.pctRaw((item.positive || 0) * 100, 0)} al alza hoy">${escapeHtml(item.sector)} <b>${fmt.pct(value, 1)}</b></span>`;
      })
      .join("");
  }

  function renderScreenTable() {
    state.screenDirty = false;
    const body = $("#screenBody");
    const empty = $("#screenEmpty");
    const table = $("#screenTable");
    let rows = state.rows.slice();

    if (state.search) {
      if (state.searchResults !== null) {
        rows = state.searchResults.slice();
      } else {
        rows = rows.filter((r) =>
          (r.symbol + " " + (r.name || "") + " " + (r.sector || "")).toLowerCase().includes(state.search)
        );
      }
    }
    if (state.signal) rows = rows.filter((r) => r.action === state.signal);
    if (state.qualityMin) {
      rows = rows.filter((r) => r.quality != null && r.quality >= state.qualityMin);
    }
    if (state.noEarnings) {
      rows = rows.filter(
        (r) => r.days_to_earnings == null || r.days_to_earnings > 7
      );
    }

    const key = state.sort.key;
    const dir = state.sort.dir;
    const valueOf = (row, k) => {
      if (k === "trend" || k === "momentum" || k === "risk") return row.subscores ? row.subscores[k] : null;
      return row[k];
    };
    const relevanceMode = !!state.search && !state.sortTouched;
    if (relevanceMode) {
      const needle = state.search;
      const upper = needle.toUpperCase();
      const relevance = (row) => {
        const symbol = (row.symbol || "").toUpperCase();
        if (symbol === upper) return 0;
        if (symbol.startsWith(upper)) return 1;
        if (symbol.includes(upper)) return 2;
        if ((row.name || "").toLowerCase().includes(needle)) return 3;
        if ((row.sector || "").toLowerCase().includes(needle)) return 4;
        return 5;
      };
      rows.sort((a, b) => relevance(a) - relevance(b) || (b.score || 0) - (a.score || 0));
    } else {
      rows.sort((a, b) => {
        const va = valueOf(a, key);
        const vb = valueOf(b, key);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === "string") return va.localeCompare(vb) * dir;
        return (va - vb) * dir;
      });
    }

    $$("#screenTable thead th[data-sort]").forEach((th) => {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (!relevanceMode && th.dataset.sort === key) {
        th.classList.add(dir === 1 ? "sorted-asc" : "sorted-desc");
      }
    });

    const asOfDates = [...new Set(rows.map((row) => row.as_of).filter(Boolean))].sort();
    const asOfText = asOfDates.length
      ? ` · cierre ${
          asOfDates.length === 1 ? fmt.date(asOfDates[0]) : `hasta ${fmt.date(asOfDates[asOfDates.length - 1])}`
        }`
      : "";
    if (state.searching) {
      $("#resultsCount").textContent = "buscando en todo el universo…";
    } else if (state.search && state.searchTotal > rows.length) {
      $("#resultsCount").textContent = `Top ${nf0.format(rows.length)} de ${nf0.format(state.searchTotal)} coincidencias${asOfText}`;
    } else if (!rows.length) {
      $("#resultsCount").textContent = "";
    } else if (!state.loadedAll && state.screenTotal > state.rows.length && !state.search) {
      $("#resultsCount").textContent = `Top ${nf0.format(state.rows.length)} de ${nf0.format(state.screenTotal)}${asOfText}`;
    } else {
      $("#resultsCount").textContent = `${nf0.format(rows.length)} valores${asOfText}`;
    }
    $("#loadAll").classList.toggle(
      "hidden",
      !(state.jobId && !state.loadedAll && state.screenTotal > state.rows.length)
    );
    const emptyTitle = empty.querySelector("h3");
    const emptyText = empty.querySelector("p");
    if (state.searching) {
      emptyTitle.textContent = "Buscando…";
      emptyText.textContent = `Consultando los ${nf0.format(state.screenTotal)} valores analizados.`;
    } else if (state.search && !rows.length) {
      emptyTitle.textContent = "Sin coincidencias";
      emptyText.textContent = `No hay valores que coincidan con “${state.searchRaw}” entre los ${nf0.format(state.screenTotal)} analizados.`;
    } else if (state.rows.length && !rows.length) {
      emptyTitle.textContent = "Sin resultados con estos filtros";
      emptyText.textContent =
        "Prueba a quitar el filtro de señal, el de calidad o el de resultados próximos.";
    } else if (state.universe === "watchlist" && state.watchlist.length) {
      emptyTitle.textContent = "Watchlist pendiente de analizar";
      emptyText.textContent = `Pulsa Analizar para escanear ${state.watchlist.length} ${
        state.watchlist.length === 1 ? "valor" : "valores"
      } de tu seguimiento.`;
    } else if (state.universe === "watchlist") {
      emptyTitle.textContent = "Tu watchlist está vacía";
      emptyText.textContent =
        "Abre un valor en Análisis y pulsa «☆ Watchlist» para seguirlo aquí.";
    } else {
      emptyTitle.textContent = "Aún no hay resultados";
      emptyText.textContent =
        "Elige un universo y pulsa Analizar. La primera ejecución descarga los históricos y puede tardar; después queda en caché.";
    }
    empty.classList.toggle("hidden", rows.length > 0);
    table.classList.toggle("hidden", rows.length === 0);
    updateEnrichHint();
    renderRegime();

    body.innerHTML = rows
      .map((r, i) => {
        const sub = r.subscores || {};
        const chgClass =
          r.change_pct == null ? "neutral" : r.change_pct >= 0 ? "pos" : "neg";
        return `<tr data-symbol="${escapeAttr(r.symbol)}">
          <td class="col-rank"><span class="rank-badge ${i < 3 ? "top" : ""}">${i + 1}</span></td>
          <td>
            <div class="sym-cell" data-action="analyze" data-symbol="${escapeAttr(r.symbol)}">
              <strong>${escapeHtml(r.symbol)}</strong>
              <span title="${escapeAttr(r.name || "")}">${escapeHtml(r.name || r.sector || "")}</span>
            </div>
          </td>
          <td class="num">${fmt.price(r.price)}</td>
          <td class="num ${chgClass}">${fmt.pct(r.change_pct)}</td>
          <td class="num">${actionBadge(r.action)}</td>
          <td class="num">
            <span class="score-pill" style="color:${scoreColor(r.score)};border-color:${scoreColor(r.score)}44;background:${scoreColor(r.score)}18">${Math.round(r.score)}</span>
            <span class="score-bar"><i style="width:${r.score}%;background:${scoreColor(r.score)}"></i></span>
          </td>
          <td class="num">${qualityCell(r)}</td>
          <td class="num" data-col="revenue_growth">${pctCell(r, r.revenue_growth)}</td>
          <td class="num" data-col="eps_surprise">${pctCell(r, r.eps_surprise)}</td>
          <td class="num">
            <div class="target-cell">
              <span>$${fmt.price(r.target_3m)}</span>
              <span class="${(r.upside_3m || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(r.upside_3m, 1)}</span>
            </div>
          </td>
          <td class="num" data-col="pct_score">${r.pct_score != null ? Math.round(r.pct_score) : "—"}</td>
          <td class="num" data-col="rs_qqq_3m"><span class="${(r.rs_qqq_3m || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(r.rs_qqq_3m, 1)}</span></td>
          <td class="num" data-col="trend">${sub.trend != null ? Math.round(sub.trend) : "—"}</td>
          <td class="num" data-col="momentum">${sub.momentum != null ? Math.round(sub.momentum) : "—"}</td>
          <td class="num" data-col="rsi">${r.rsi != null ? r.rsi.toFixed(0) : "—"}</td>
          <td class="num" data-col="ret_3m"><span class="${(r.ret_3m || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(r.ret_3m, 1)}</span></td>
          <td class="num" data-col="ret_1y"><span class="${(r.ret_1y || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(r.ret_1y, 0)}</span></td>
          <td class="num" data-col="vol_60d">${fmt.pctRaw(r.vol_60d == null ? null : r.vol_60d * 100, 0)}</td>
          <td class="num neg" data-col="max_drawdown">${fmt.pctRaw(r.max_drawdown == null ? null : -r.max_drawdown * 100, 0)}</td>
          <td class="num" data-col="days_to_earnings">${daysCell(r)}</td>
        </tr>`;
      })
      .join("");

    applyColumns();
    renderScreenCards(rows);
  }

  /* ================= ANÁLISIS ================= */
  function wireAnalyze() {
    $$("#analyzeTabs .subtab").forEach((btn) => {
      btn.addEventListener("click", () => setAnalyzeTab(btn.dataset.atab));
    });
    setAnalyzeTab("resumen");
    const input = $("#symbolSearch");
    $("#analyzeBtn").addEventListener("click", () => {
      const symbol = input.value.trim().toUpperCase();
      if (symbol) analyze(symbol);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const symbol = input.value.trim().toUpperCase();
        if (symbol) {
          hideSuggestions();
          analyze(symbol);
        }
      }
    });

    let timer = null;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 1) {
        hideSuggestions();
        return;
      }
      timer = setTimeout(() => loadSuggestions(q), 220);
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-box")) hideSuggestions();
    });

    $$("#quickChips .chip").forEach((chip) => {
      chip.addEventListener("click", () => analyze(chip.dataset.symbol));
    });

    $("#starBtn").addEventListener("click", () => {
      if (!state.current) return;
      toggleWatch(state.current.symbol);
      updateStarButton();
    });

    $("#compareBtn").addEventListener("click", compareStocks);
    $("#openCompareEmpty").addEventListener("click", () => {
      $("#analyzeEmpty").classList.add("hidden");
      $("#analyzeContent").classList.remove("hidden");
      $("#analyzeContent").classList.add("compare-only");
      $("#compareBack").classList.remove("hidden");
      setAnalyzeTab("comparar");
      $("#compareInput").focus();
    });
    $("#compareBack").addEventListener("click", () => {
      $("#analyzeContent").classList.add("hidden");
      $("#analyzeContent").classList.remove("compare-only");
      $("#analyzeEmpty").classList.remove("hidden");
      $("#compareBack").classList.add("hidden");
      setAnalyzeTab("resumen");
    });
    $("#compareInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") compareStocks();
    });
    $("#reloadSymbol").addEventListener("click", () => {
      if (state.current) analyze(state.current.symbol, true);
    });

    $$("#rangeBtns .seg").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$("#rangeBtns .seg").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        state.chartRange = Number(btn.dataset.range);
        if (state.chart) state.chart.setRange(state.chartRange);
      });
    });

    const last = store.get("sl.lastSymbol", null);
    if (last) input.placeholder = `Último: ${last}`;
  }

  async function compareStocks() {
    const raw = $("#compareInput").value.trim();
    const symbols = [...new Set(raw.toUpperCase().split(/[,\s]+/).filter(Boolean))].slice(0, 4);
    const error = $("#compareError");
    error.textContent = "";
    if (symbols.length < 2) {
      error.textContent = "Introduce al menos dos tickers.";
      return;
    }
    $("#compareBtn").disabled = true;
    try {
      const results = await Promise.all(symbols.map((symbol) => api("/api/analyze", { symbol })));
      const series = [];
      for (const result of results) {
        const closes = {};
        (result.chart.dates || []).forEach((date, index) => {
          closes[date] = result.chart.candles[index][3];
        });
        series.push({ symbol: result.symbol, closes });
      }
      const common = series
        .map((item) => Object.keys(item.closes))
        .reduce((acc, dates) => acc.filter((date) => dates.includes(date)));
      const dates = common.sort().slice(-252);
      if (dates.length < 20) {
        error.textContent = "No hay suficiente histórico común.";
        return;
      }
      const colors = ["#6d7cff", "#22d3ee", "#fbbf24", "#f472b6"];
      const values = series.map((item) => {
        const base = item.closes[dates[0]];
        return dates.map((date) => (item.closes[date] / base) * 100);
      });
      $("#compareLegend").innerHTML = series
        .map(
          (item, index) =>
            `<span><i class="lg" style="background:${colors[index % colors.length]}"></i>${escapeHtml(item.symbol)}</span>`
        )
        .join("");
      if (!state.compareChart) state.compareChart = new LineChart($("#compareChart"));
      state.compareChart.setData({
        dates,
        series: values.map((data, index) => ({
          name: series[index].symbol,
          color: colors[index % colors.length],
          values: data,
        })),
      });
      state.compareChart.resize();

      const returnsOf = (data) =>
        data.slice(1).map((value, index) => value / data[index] - 1);
      const allReturns = values.map(returnsOf);
      const rows = series.map((item, index) => {
        const data = values[index];
        const returns = allReturns[index];
        const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
        const variance =
          returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
          Math.max(1, returns.length - 1);
        const vol = Math.sqrt(variance) * Math.sqrt(252);
        let peak = -Infinity;
        let drawdown = 0;
        data.forEach((value) => {
          peak = Math.max(peak, value);
          drawdown = Math.max(drawdown, (peak - value) / peak);
        });
        let corr = null;
        if (index > 0) {
          const a = allReturns[0];
          const b = returns;
          const meanA = a.reduce((sum, value) => sum + value, 0) / a.length;
          const meanB = b.reduce((sum, value) => sum + value, 0) / b.length;
          let cov = 0;
          let varA = 0;
          let varB = 0;
          for (let i = 0; i < a.length; i++) {
            cov += (a[i] - meanA) * (b[i] - meanB);
            varA += (a[i] - meanA) ** 2;
            varB += (b[i] - meanB) ** 2;
          }
          corr = varA && varB ? cov / Math.sqrt(varA * varB) : null;
        }
        return { symbol: item.symbol, total: data[data.length - 1] / 100 - 1, vol, drawdown, corr };
      });
      $("#compareTable tbody").innerHTML = rows
        .map(
          (row) => `<tr>
            <td><strong>${escapeHtml(row.symbol)}</strong></td>
            <td class="num ${row.total >= 0 ? "pos" : "neg"}">${fmt.pct(row.total, 1)}</td>
            <td class="num">${fmt.pct(row.vol, 1)}</td>
            <td class="num neg">${fmt.pct(-row.drawdown, 1)}</td>
            <td class="num">${row.corr != null ? row.corr.toFixed(2) : "—"}</td>
          </tr>`
        )
        .join("");
    } catch (e) {
      error.textContent = e.message;
    } finally {
      $("#compareBtn").disabled = false;
    }
  }

  function setAnalyzeTab(tab) {
    state.analyzeTab = tab;
    $$("#analyzeTabs .subtab").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.atab === tab);
    });
    $$("[data-apanel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.apanel !== tab);
    });
    if (tab === "resumen" && state.chart) {
      requestAnimationFrame(() => state.chart.resize());
    }
    if (tab === "comparar" && state.compareChart) {
      requestAnimationFrame(() => state.compareChart.resize());
    }
    updateHash();
  }

  async function loadSuggestions(q) {
    try {
      const data = await api("/api/search", { q });
      const box = $("#suggestions");
      if (!data.items || !data.items.length) {
        hideSuggestions();
        return;
      }
      box.innerHTML = data.items
        .map(
          (item) => `<div class="suggestion" data-symbol="${escapeAttr(item.symbol)}">
            <strong>${escapeHtml(item.symbol)}</strong>
            <span>${escapeHtml(item.name || "")}</span>
          </div>`
        )
        .join("");
      box.classList.remove("hidden");
      box.querySelectorAll(".suggestion").forEach((el) => {
        el.addEventListener("click", () => {
          $("#symbolSearch").value = el.dataset.symbol;
          hideSuggestions();
          analyze(el.dataset.symbol);
        });
      });
    } catch (e) {
      hideSuggestions();
    }
  }

  function hideSuggestions() {
    $("#suggestions").classList.add("hidden");
  }

  function openSymbol(symbol) {
    setTab("analyze");
    $("#symbolSearch").value = symbol;
    analyze(symbol);
  }

  async function analyze(symbol, force = false) {
    symbol = symbol.toUpperCase();
    state.fundamentals = null;
    loadFundamentals(symbol, force);
    loadNews(symbol, force);
    $("#analyzeEmpty").classList.add("hidden");
    $("#analyzeError").classList.add("hidden");
    $("#analyzeContent").classList.add("hidden");
    $("#analyzeLoading").classList.remove("hidden");
    $("#reloadSymbol").disabled = true;
    try {
      const data = await api("/api/analyze", { symbol, force: force ? 1 : 0 });
      state.current = data;
      store.set("sl.lastSymbol", symbol);
      updateHash();
      $("#analyzeContent").classList.remove("hidden");
      $("#analyzeContent").classList.remove("compare-only");
      $("#compareBack").classList.add("hidden");
      renderAnalysis(data);
    } catch (e) {
      $("#analyzeError").textContent = e.message;
      $("#analyzeError").classList.remove("hidden");
      $("#analyzeEmpty").classList.remove("hidden");
    } finally {
      $("#analyzeLoading").classList.add("hidden");
      $("#reloadSymbol").disabled = false;
    }
  }

  function renderAnalysis(d) {
    const m = d.metrics || {};
    const s = d.scores || {};
    $("#dSymbol").textContent = d.symbol;
    $("#dName").textContent = d.name || d.symbol;
    $("#dPrice").textContent = "$" + fmt.price(m.price);
    const chg = m.change_pct || 0;
    $("#dChange").innerHTML = `<span class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "▲" : "▼"} ${fmt.price(Math.abs(m.change || 0))} (${fmt.pct(chg)})</span>`;
    $("#dAsOf").textContent = "Cierre del " + fmt.date(m.as_of);

    const tags = [];
    if (d.sector) tags.push(d.sector);
    if (d.market_cap) tags.push("Cap. " + fmt.money(d.market_cap));
    if (d.country) tags.push(d.country);
    if (m.beta != null) tags.push("Beta " + m.beta.toFixed(2));
    $("#dChips").innerHTML = tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
    appendQualityChip();

    const score = s.composite || 0;
    const circle = 2 * Math.PI * 58;
    const fg = $("#gaugeFg");
    fg.style.strokeDasharray = circle.toFixed(1);
    fg.style.strokeDashoffset = (circle * (1 - score / 100)).toFixed(1);
    fg.style.stroke = scoreColor(score);
    fg.style.filter = `drop-shadow(0 0 8px ${scoreColor(score)}88)`;
    $("#gScore").textContent = Math.round(score);
    $("#gRating").textContent = "de 100";

    const labels = { trend: "Tendencia", momentum: "Momento", risk: "Riesgo", rsi: "RSI", liquidity: "Liquidez" };
    const sub = s.subscores || {};
    $("#subscores").innerHTML = Object.keys(labels)
      .map(
        (key) => `<div class="subscore">
          <span>${labels[key]}</span>
          <div class="bar"><i style="width:${sub[key] || 0}%"></i></div>
          <b>${Math.round(sub[key] || 0)}</b>
        </div>`
      )
      .join("");

    renderEvaluation(d);
    $("#dSummary").textContent = d.summary || "";

    const icon = { positive: "▲", negative: "▼", neutral: "•" };
    const caveats = ((d.evaluation || {}).caveats || []).map((text) => ({
      type: "neutral",
      title: "Precaución",
      detail: text,
    }));
    $("#dSignals").innerHTML = caveats.concat(d.signals || [])
      .map(
        (sig) => `<div class="signal ${sig.type}">
          <div class="icon">${icon[sig.type] || "•"}</div>
          <div><strong>${escapeHtml(sig.title)}</strong><span>${escapeHtml(sig.detail)}</span></div>
        </div>`
      )
      .join("");

    renderMetrics(m, d.evaluation || {});
    renderChart(d.chart);
    updateStarButton();
  }

  function renderEvaluation(d) {
    const ev = d.evaluation || {};
    const cls = ACTION_CLASS[ev.action] || "sig-neutral";
    const panel = $("#actionPanel");
    panel.className = "action-panel " + cls;
    const badge = $("#actionBadge");
    badge.className = "action-badge " + cls;
    badge.textContent = ev.action || "SIN SEÑAL";
    $("#actionSub").textContent =
      ev.confidence != null
        ? `Confianza ${ev.confidence}% · horizonte ${ev.horizon || "3 meses"}`
        : "Señal técnica";
    $("#actionMethod").textContent = ev.method || "";

    const stat = (k, v, s) =>
      `<div class="action-stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s || ""}</div></div>`;
    const range =
      ev.target_low != null && ev.target_high != null
        ? `${fmt.price(ev.target_low)} – ${fmt.price(ev.target_high)}`
        : "";
    $("#actionTargets").innerHTML = [
      stat(
        "Objetivo 3M",
        ev.target_3m != null ? "$" + fmt.price(ev.target_3m) : "—",
        `${fmt.pct(ev.upside_3m, 1)} · rango ${range}`
      ),
      stat(
        "Stop sugerido",
        ev.stop != null ? "$" + fmt.price(ev.stop) : "—",
        ev.stop_pct != null ? fmt.pct(ev.stop_pct, 1) : ""
      ),
      stat(
        "R:R",
        ev.risk_reward != null ? ev.risk_reward.toFixed(2) : "—",
        "beneficio / riesgo"
      ),
      stat(
        "Objetivo 12M",
        ev.target_12m != null ? "$" + fmt.price(ev.target_12m) : "—",
        fmt.pct(ev.upside_12m, 1)
      ),
    ].join("");
  }

  const QUALITY_LABELS = {
    growth: "Crecimiento",
    profitability: "Rentabilidad",
    balance: "Balance",
    consistency: "Consistencia",
    valuation: "Valoración",
  };

  async function loadFundamentals(symbol, force) {
    const token = ++state.fundToken;
    $("#fundamentalsCard").classList.toggle("hidden", state.analyzeTab !== "resultados");
    $("#fundamentalsLoading").classList.remove("hidden");
    $("#fundamentalsBody").classList.add("hidden");
    $("#fundamentalsError").classList.add("hidden");
    try {
      const data = await api("/api/fundamentals", { symbol, force: force ? 1 : 0 });
      if (token !== state.fundToken) return;
      renderFundamentals(data);
    } catch (e) {
      if (token !== state.fundToken) return;
      $("#fundamentalsLoading").classList.add("hidden");
      $("#fundamentalsError").textContent = e.message;
      $("#fundamentalsError").classList.remove("hidden");
    }
  }

  async function loadNews(symbol, force) {
    const token = ++state.newsToken;
    $("#newsCard").classList.toggle("hidden", state.analyzeTab !== "noticias");
    $("#newsLoading").classList.remove("hidden");
    $("#newsBody").classList.add("hidden");
    $("#newsSentiment").textContent = "cargando…";
    $("#newsSentiment").className = "quality-pill";
    try {
      const data = await api("/api/news", { symbol, force: force ? 1 : 0 });
      if (token !== state.newsToken) return;
      renderNews(data);
    } catch (e) {
      if (token !== state.newsToken) return;
      $("#newsLoading").classList.add("hidden");
      $("#newsBody").classList.remove("hidden");
      $("#newsSentiment").textContent = "sin datos";
      $("#newsList").innerHTML = `<p class="muted small">${escapeHtml(e.message)}</p>`;
      $("#newsEvents").innerHTML = "";
      $("#newsNote").textContent = "";
    }
  }

  function safeHref(url) {
    const raw = String(url || "").trim();
    return /^https?:\/\//i.test(raw) ? escapeAttr(raw) : "#";
  }

  function renderNews(data) {
    const sentiment = data.sentiment || {};
    const score = sentiment.score == null ? 50 : sentiment.score;
    const label = sentiment.label || "Neutro";
    const pill = $("#newsSentiment");
    pill.textContent = label;
    pill.title = `Tono de los titulares (${Math.round(score)}/100). Es contexto, no una señal.`;
    pill.className =
      "quality-pill " + (score >= 60 ? "good" : score <= 40 ? "bad" : "mid");

    const dot = (value) =>
      `<span class="news-dot ${value > 0 ? "pos" : value < 0 ? "neg" : "neu"}"></span>`;
    const items = (data.items || []).slice(0, 8);
    const cleanTitle = (item) => {
      let title = item.title || "";
      if (item.source) {
        title = title
          .replace(new RegExp(`\\s*[\\-|–—]\\s*${item.source.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`, "i"), "")
          .trim();
      }
      return title;
    };
    $("#newsList").innerHTML = items
      .map(
        (item) => `<div class="news-item">
          ${dot(item.sentiment)}
          <div>
            <a class="news-title" href="${safeHref(item.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(cleanTitle(item))}</a>
            <div class="news-meta">
              ${item.source ? `<span>${escapeHtml(item.source)}</span>` : ""}
              ${item.published ? `<span>${fmt.date(item.published)}</span>` : ""}
              ${item.event ? `<span class="news-tag">${escapeHtml(item.event)}</span>` : ""}
            </div>
          </div>
        </div>`
      )
      .join("") || `<p class="muted small">Sin titulares recientes.</p>`;

    $("#newsEvents").innerHTML = (data.events || [])
      .slice(0, 6)
      .map((event) => `<span class="sector-chip">${escapeHtml(event.event)} · ${event.count}</span>`)
      .join("");
    $("#newsNote").textContent = data.note || "";
    $("#newsLoading").classList.add("hidden");
    $("#newsBody").classList.remove("hidden");
  }

  function quarterLabel(iso) {
    if (!iso || !iso.includes("-")) return iso || "—";
    const [year, month] = iso.split("-");
    return `T${Math.ceil(Number(month) / 3)} ${year}`;
  }

  function appendQualityChip() {
    const data = state.fundamentals;
    const chips = $("#dChips");
    if (!data || !chips || !state.current) return;
    if (data.symbol !== state.current.symbol) return;
    if (chips.querySelector("[data-quality]")) return;
    const score = data.quality && data.quality.score;
    if (score == null) return;
    chips.insertAdjacentHTML(
      "beforeend",
      `<span class="tag" data-quality="1" title="Contexto fundamental; no entra en la puntuación ni en la señal">Calidad ${Math.round(score)}/100</span>`
    );
  }

  function renderFundamentals(d) {
    state.fundamentals = d;
    const q = d.quality || {};
    const hasScore = q.score != null;
    const score = hasScore ? q.score : 0;
    $("#fundamentalsLoading").classList.add("hidden");
    $("#fundamentalsBody").classList.remove("hidden");

    $("#fScore").textContent = hasScore ? Math.round(score) : "—";
    $("#fLabel").textContent = hasScore ? q.label || "" : "Sin datos suficientes";

    const subs = q.subscores || {};
    $("#fSubscores").innerHTML = Object.keys(QUALITY_LABELS)
      .filter((key) => subs[key] != null)
      .map(
        (key) => `<div class="subscore">
          <span>${QUALITY_LABELS[key]}</span>
          <div class="bar"><i style="width:${subs[key]}%"></i></div>
          <b>${Math.round(subs[key])}</b>
        </div>`
      )
      .join("");

    const company = d.company || {};
    $("#fLabel").textContent = `${hasScore ? q.label || "" : "Sin datos suficientes"}${
      company.pe ? ` · PER ${company.pe.toFixed(1)}` : ""
    }`;

    const ne = d.next_earnings || {};
    let earnings = `<div class="fund-k">Próximos resultados</div>`;
    if (ne.date) {
      const days = ne.days;
      const when = days === 0 ? "hoy" : days === 1 ? "mañana" : `en ${days} días`;
      const time = ne.time ? ne.time.replace("time-", "").replace("-", " ") : "";
      earnings += `<div class="fund-v">${escapeHtml(fmt.date(ne.date))}</div><div class="fund-s">${when}${time ? ` · ${escapeHtml(time)}` : ""}</div>`;
      earnings += `<span class="fund-flag ${days <= 7 ? "" : "ok"}">${days <= 7 ? "Evento próximo" : "Fecha confirmada"}</span>`;
    } else {
      earnings += `<div class="fund-v">${escapeHtml(ne.fiscal_period || "—")}</div><div class="fund-s">Fecha sin confirmar</div>`;
    }
    if (ne.eps_consensus != null) {
      earnings += `<div class="fund-s" style="margin-top:8px">Consenso EPS: <b>${ne.eps_consensus.toFixed(2)} $</b></div>`;
    }

    const analyst = d.analyst || {};
    const priceNow = state.current && state.current.metrics ? state.current.metrics.price : company.reference_price;
    const upside = analyst.target && priceNow ? analyst.target / priceNow - 1 : analyst.upside;
    const dividend = company.dividend_yield != null ? ` · Yield ${company.dividend_yield.toFixed(2)}%` : "";
    $("#fAnalystBlock").innerHTML = `
      <div class="fund-k">Analistas${analyst.count ? ` (${analyst.count})` : ""}</div>
      <div class="fund-v">${analyst.target != null ? "$" + fmt.price(analyst.target) : "—"}</div>
      <div class="fund-s">${upside != null ? fmt.pct(upside, 1) + " potencial" : "Sin objetivo"} · ${escapeHtml(analyst.rating || "—")}${dividend}</div>`;
    $("#fEarningsBlock").innerHTML = earnings;

    $("#fQuarters tbody").innerHTML = (d.quarters || [])
      .slice(0, 6)
      .map(
        (row) => `<tr>
          <td>${escapeHtml(quarterLabel(row.period))}</td>
          <td class="num">${fmt.money(row.revenue)}</td>
          <td class="num">${row.gross_margin != null ? fmt.pctRaw(row.gross_margin * 100, 1) : "—"}</td>
          <td class="num">${row.net_margin != null ? fmt.pctRaw(row.net_margin * 100, 1) : "—"}</td>
          <td class="num ${(row.fcf || 0) < 0 ? "neg" : ""}">${fmt.money(row.fcf)}</td>
        </tr>`
      )
      .join("");

    $("#fSurprises tbody").innerHTML = (d.surprises || [])
      .slice(0, 6)
      .map(
        (row) => `<tr>
          <td>${escapeHtml(row.period || "—")}</td>
          <td class="num">${row.eps != null ? row.eps.toFixed(2) : "—"}</td>
          <td class="num">${row.consensus != null ? row.consensus.toFixed(2) : "—"}</td>
          <td class="num ${(row.surprise_pct || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(row.surprise_pct, 1)}</td>
        </tr>`
      )
      .join("");

    $("#fRisks").innerHTML = (q.risks || [])
      .map((risk) => `<span class="risk-chip">${escapeHtml(risk)}</span>`)
      .join("");
    $("#fNotes").textContent =
      (d.notes || []).join(" ") +
      " La calidad es contexto: no entra en la puntuación ni en la señal.";

    appendQualityChip();
    syncRowFromFundamentals(d);
  }

  function syncRowFromFundamentals(d) {
    if (!d || !d.symbol) return;
    const row =
      state.rows.find((item) => item.symbol === d.symbol) ||
      (state.searchResults || []).find((item) => item.symbol === d.symbol);
    if (!row) return;
    const quality = d.quality || {};
    const annual = d.annual || {};
    const surprise = (d.surprises || [])[0] || {};
    const earnings = d.next_earnings || {};
    const analyst = d.analyst || {};
    const company = d.company || {};
    row.quality = quality.score ?? null;
    row.quality_label = quality.label ?? null;
    row.revenue_growth = annual.revenue_growth ?? null;
    row.eps_surprise = surprise.surprise_pct ?? null;
    row.days_to_earnings = earnings.days ?? null;
    row.next_earnings = earnings.date ?? null;
    row.analyst_upside = analyst.upside ?? null;
    row.pe = company.pe ?? null;
    row.enriched = true;
    if (state.tab === "screen") {
      state.screenDirty = false;
      renderScreenTable();
    } else {
      state.screenDirty = true;
    }
  }

  function renderMetrics(m, ev) {
    ev = ev || {};
    const cards = [
      {
        k: "Soporte",
        v: ev.support != null ? "$" + fmt.price(ev.support) : "—",
        s: "Primer nivel técnico",
      },
      {
        k: "Resistencia",
        v: ev.resistance != null ? "$" + fmt.price(ev.resistance) : "—",
        s: "Primer nivel técnico",
      },
      {
        k: "Objetivo 12M",
        v: ev.target_12m != null ? "$" + fmt.price(ev.target_12m) : "—",
        s: fmt.pct(ev.upside_12m, 1),
        cls: (ev.upside_12m || 0) >= 0 ? "pos" : "neg",
      },
      {
        k: "Confianza",
        v: ev.confidence != null ? ev.confidence + "%" : "—",
        s: "Coherencia de la señal",
      },
      { k: "RSI (14)", v: m.rsi != null ? m.rsi.toFixed(0) : "—", s: rsiLabel(m.rsi) },
      { k: "MACD", v: m.macd_hist != null ? m.macd_hist.toFixed(2) : "—", s: (m.macd_hist || 0) >= 0 ? "Histograma positivo" : "Histograma negativo", cls: (m.macd_hist || 0) >= 0 ? "pos" : "neg" },
      { k: "Volatilidad 60d", v: fmt.pctRaw(m.vol_60d == null ? null : m.vol_60d * 100, 1), s: "Anualizada" },
      { k: "Máx. caída (1A)", v: fmt.pctRaw(m.max_drawdown == null ? null : -m.max_drawdown * 100, 1), s: "Drawdown" },
      { k: "ATR (14)", v: fmt.pctRaw(m.atr_pct == null ? null : m.atr_pct * 100, 2), s: "Rango medio diario" },
      { k: "Beta vs QQQ", v: m.beta != null ? m.beta.toFixed(2) : "—", s: "Últimos 12 meses" },
      { k: "Sharpe (1A)", v: m.sharpe != null ? m.sharpe.toFixed(2) : "—", s: "Sin tasa libre de riesgo" },
      { k: "Retorno 1M", v: fmt.pct(m.ret_1m, 1), s: "", cls: (m.ret_1m || 0) >= 0 ? "pos" : "neg" },
      { k: "Retorno 3M", v: fmt.pct(m.ret_3m, 1), s: "", cls: (m.ret_3m || 0) >= 0 ? "pos" : "neg" },
      { k: "Retorno 6M", v: fmt.pct(m.ret_6m, 1), s: "", cls: (m.ret_6m || 0) >= 0 ? "pos" : "neg" },
      { k: "Retorno 1A", v: fmt.pct(m.ret_1y, 1), s: "", cls: (m.ret_1y || 0) >= 0 ? "pos" : "neg" },
      { k: "YTD", v: fmt.pct(m.ret_ytd, 1), s: "Año en curso", cls: (m.ret_ytd || 0) >= 0 ? "pos" : "neg" },
      { k: "Rango 52 semanas", v: m.range_pos != null ? Math.round(m.range_pos * 100) + "%" : "—", s: m.low_52w && m.high_52w ? `${fmt.price(m.low_52w)} – ${fmt.price(m.high_52w)}` : "" },
      { k: "Volumen medio 20d", v: fmt.volume(m.avg_volume_20d), s: m.dollar_volume_20d ? fmt.money(m.dollar_volume_20d) + " negociados" : "" },
      { k: "SMA 50 / 200", v: m.sma50 && m.sma200 ? fmt.price(m.sma50) + " / " + fmt.price(m.sma200) : "—", s: m.golden_cross ? "Golden cross activo" : "Sin golden cross" },
    ];
    $("#metricGrid").innerHTML = cards
      .map(
        (c) => `<div class="metric">
          <div class="k">${c.k}</div>
          <div class="v ${c.cls || ""}">${c.v || "—"}</div>
          ${c.s ? `<div class="s">${c.s}</div>` : ""}
        </div>`
      )
      .join("");
  }

  function rsiLabel(rsi) {
    if (rsi == null) return "";
    if (rsi >= 70) return "Sobrecompra";
    if (rsi <= 30) return "Sobreventa";
    if (rsi >= 50) return "Zona alcista";
    return "Zona bajista";
  }

  function renderChart(chartData) {
    const canvas = $("#priceChart");
    const tooltip = $("#chartTooltip");
    if (!state.chart) state.chart = new PriceChart(canvas, tooltip);
    state.chart.setData(chartData);
    state.chart.setRange(state.chartRange);
    requestAnimationFrame(() => state.chart.resize());
  }

  /* ================= WATCHLIST ================= */
  function wireWatch() {
    $("#clearWatch").addEventListener("click", () => {
      if (!state.watchlist.length) return;
      state.watchlist = [];
      state.watchRows = {};
      store.set("sl.watchlist", []);
      refreshWatchBadge();
      renderWatchlist();
      renderScreenTable();
      updateStarButton();
    });
    $("#analyzeWatch").addEventListener("click", () => {
      setTab("screen");
      $("#universe").value = "watchlist";
      state.universe = "watchlist";
      $("#maxSymbols").value = "0";
      store.set("sl.universe", "watchlist");
      updateHash();
      updateScanNote();
      startScreen(false);
    });
  }

  function toggleWatch(symbol) {
    const idx = state.watchlist.indexOf(symbol);
    if (idx >= 0) state.watchlist.splice(idx, 1);
    else state.watchlist.push(symbol);
    state.watchlist.sort();
    store.set("sl.watchlist", state.watchlist);
    refreshWatchBadge();
    updateStarButton();
  }

  function refreshWatchBadge() {
    const badge = $("#watchCount");
    if (badge) badge.textContent = String(state.watchlist.length);
  }

  function updateStarButton() {
    const btn = $("#starBtn");
    if (!state.current) return;
    const starred = state.watchlist.includes(state.current.symbol);
    btn.textContent = starred ? "★ En watchlist" : "☆ Watchlist";
    btn.classList.toggle("ghost", true);
  }

  async function renderWatchlist() {
    const body = $("#watchBody");
    const empty = $("#watchEmpty");
    const table = $("#watchTable");
    if (!state.watchlist.length) {
      body.innerHTML = "";
      empty.classList.remove("hidden");
      table.classList.add("hidden");
      return;
    }
    empty.classList.add("hidden");
    table.classList.remove("hidden");
    body.innerHTML = state.watchlist
      .map((sym) => {
        const r = state.watchRows[sym];
        if (!r) {
          return `<tr><td><strong>${escapeHtml(sym)}</strong></td><td colspan="8" class="muted">cargando…</td><td></td></tr>`;
        }
        const m = r.metrics || {};
        const sc = (r.scores || {}).composite || 0;
        return `<tr>
          <td><div class="sym-cell" data-action="analyze" data-symbol="${escapeAttr(sym)}"><strong>${escapeHtml(sym)}</strong><span>${escapeHtml(r.name || "")}</span></div></td>
          <td class="num">${fmt.price(m.price)}</td>
          <td class="num ${(m.change_pct || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(m.change_pct)}</td>
          <td class="num">${actionBadge((r.evaluation || {}).action)}</td>
          <td class="num"><span class="score-pill" style="color:${scoreColor(sc)};border-color:${scoreColor(sc)}44;background:${scoreColor(sc)}18">${Math.round(sc)}</span></td>
          <td class="num ${(m.ret_1m || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(m.ret_1m, 1)}</td>
          <td class="num ${(m.ret_3m || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(m.ret_3m, 1)}</td>
          <td class="num">${m.rsi != null ? m.rsi.toFixed(0) : "—"}</td>
          <td class="num">${fmt.pctRaw(m.vol_60d == null ? null : m.vol_60d * 100, 0)}</td>
          <td class="col-actions"><button class="icon-btn starred" data-action="unstar" data-symbol="${escapeAttr(sym)}">★</button></td>
        </tr>`;
      })
      .join("");

    body.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.dataset.action === "unstar") {
          toggleWatch(el.dataset.symbol);
          delete state.watchRows[el.dataset.symbol];
          renderWatchlist();
        } else {
          openSymbol(el.dataset.symbol);
        }
      });
    });

    const missing = state.watchlist.filter((sym) => !state.watchRows[sym]);
    if (missing.length && !state.loadingWatch) {
      state.loadingWatch = true;
      Promise.all(
        missing.map(async (sym) => {
          try {
            state.watchRows[sym] = await api("/api/analyze", { symbol: sym });
          } catch (e) {
            state.watchRows[sym] = { symbol: sym, metrics: {}, scores: {} };
          }
        })
      ).then(() => {
        state.loadingWatch = false;
        renderWatchlist();
      });
    }
  }

  /* ================= CARTERA Y ALERTAS ================= */
  function wirePortfolio() {
    $$("#portfolioTabs .subtab").forEach((btn) => {
      btn.addEventListener("click", () => setPortfolioTab(btn.dataset.ptab));
    });
    setPortfolioTab("positions");
    $("#alKind").innerHTML = Object.entries(ALERT_KINDS)
      .map(([key, label]) => `<option value="${key}">${label}</option>`)
      .join("");
    $("#alSignal").innerHTML = SIGNALS.map(
      (signal) => `<option value="${signal}">${signal}</option>`
    ).join("");
    $("#alKind").addEventListener("change", updateAlertForm);
    updateAlertForm();

    $("#pfCapital").value = state.capital;
    $("#pfRisk").value = state.riskPct;
    ["#pfCapital", "#pfRisk", "#pfPrice", "#pfStop"].forEach((selector) => {
      $(selector).addEventListener("input", updateSuggestion);
    });
    updateSuggestion();
    $("#pfAdd").addEventListener("click", addPosition);
    $("#alAdd").addEventListener("click", addAlert);
    $("#pfCapital").addEventListener("change", () => {
      state.capital = Number($("#pfCapital").value) || 0;
      store.set("sl.capital", state.capital);
    });
    $("#pfRisk").addEventListener("change", () => {
      state.riskPct = Number($("#pfRisk").value) || 0;
      store.set("sl.riskPct", state.riskPct);
    });
    $("#downloadBackup").addEventListener("click", () => {
      window.location.href = "/api/backup";
    });
    $("#exportData").addEventListener("click", () => {
      window.location.href = "/api/user-data/export";
    });
    $("#importData").addEventListener("click", () => $("#importFile").click());
    $("#importFile").addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      if (!window.confirm(`¿Importar ${file.name}? Se añadirán posiciones y alertas, sin borrar las actuales.`)) {
        event.target.value = "";
        return;
      }
      try {
        const text = await file.text();
        const response = await fetch("/api/user-data/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: text,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Error al importar");
        window.alert(`Importado: ${data.positions} posiciones y ${data.alerts} alertas.`);
        loadPortfolio();
      } catch (e) {
        window.alert(e.message);
      } finally {
        event.target.value = "";
      }
    });
    $("#retryFailed").addEventListener("click", async () => {
      if (!window.confirm("¿Limpiar la lista de fallidos para reintentarlos en el próximo escaneo?")) return;
      const result = await api("/api/system/retry");
      window.alert(`${result.cleared} tickers marcados para reintentar.`);
      loadPortfolio();
    });
    $("#checkAlerts").addEventListener("click", checkAlerts);
    $("#markRead").addEventListener("click", markAlertsRead);
    $("#pfTable").addEventListener("click", (event) => {
      const btn = event.target.closest("[data-pf-delete]");
      if (btn) deletePosition(btn.dataset.pfDelete);
    });
    const pfCards = $("#pfCards");
    if (pfCards) {
      pfCards.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-pf-delete]");
        if (btn) deletePosition(btn.dataset.pfDelete);
      });
    }
    const pfMore = $("#pfMore");
    if (pfMore) {
      pfMore.addEventListener("click", () => {
        const hidden = $("#pfTotalsExtra").classList.toggle("hidden");
        pfMore.textContent = hidden ? "Más métricas" : "Menos métricas";
      });
    }
    $("#alList").addEventListener("click", (event) => {
      const del = event.target.closest("[data-al-delete]");
      const toggle = event.target.closest("[data-al-toggle]");
      if (del) deleteAlert(del.dataset.alDelete);
      if (toggle) toggleAlert(toggle.dataset.alToggle, toggle.dataset.active === "1");
    });
    window.setInterval(refreshAlertBadge, 60000);
    refreshAlertBadge();
  }

  function setPortfolioTab(tab) {
    state.portfolioTab = tab;
    $$("#portfolioTabs .subtab").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.ptab === tab);
    });
    $$("[data-ppanel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.ppanel !== tab);
    });
    if (tab === "watch") renderWatchlist();
    updateHash();
  }

  function updateAlertForm() {
    const kind = $("#alKind").value;
    $("#alSignal").classList.toggle("hidden", kind !== "signal");
    $("#alValue").classList.toggle("hidden", kind === "signal");
  }

  function updateSuggestion() {
    const entry = Number($("#pfPrice").value);
    const stop = Number($("#pfStop").value);
    const capital = Number($("#pfCapital").value) || 0;
    const riskPct = Number($("#pfRisk").value) || 0;
    const target = $("#pfSuggestion");
    if (entry > 0 && stop > 0 && entry > stop && capital > 0 && riskPct > 0) {
      const budget = (capital * riskPct) / 100;
      const shares = Math.floor(budget / (entry - stop));
      target.textContent = `Sugerido: ${nf0.format(shares)} acciones (riesgo ${fmt.money(budget)})`;
    } else {
      target.textContent = "Añade entrada y stop para calcular el tamaño";
    }
  }

  async function loadPortfolio() {
    try {
      const [snapshot, alerts, system] = await Promise.all([
        api("/api/portfolio"),
        api("/api/alerts"),
        api("/api/system"),
      ]);
      renderPortfolio(snapshot);
      renderSectors(snapshot);
      renderRisk(snapshot);
      renderAlerts(alerts);
      renderSystem(system);
    } catch (e) {
      /* sin datos todavía */
    }
  }

  async function refreshAlertBadge() {
    try {
      const data = await api("/api/alerts");
      const badge = $("#alertCount");
      badge.textContent = String(data.unread || 0);
      badge.style.display = data.unread ? "inline-block" : "none";
    } catch (e) {
      /* silencioso */
    }
  }

  function renderPortfolio(data) {
    const totals = data.totals || {};
    const card = (k, v, s, cls) =>
      `<div class="metric"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
    const cards = [
      card("Valor", fmt.money(totals.value), `${totals.count || 0} posiciones`),
      card("Coste", fmt.money(totals.cost), "invertido"),
      card("P&L", fmt.money(totals.pnl), fmt.pct(totals.pnl_pct, 1), (totals.pnl || 0) >= 0 ? "pos" : "neg"),
      card("Día", fmt.money(totals.day_pnl), "variación estimada", (totals.day_pnl || 0) >= 0 ? "pos" : "neg"),
      card(
        "Riesgo abierto",
        fmt.money(totals.open_risk),
        `${fmt.pct(totals.open_risk_pct, 1)} del valor`,
        "neg"
      ),
      ...(data.risk
        ? [
            card("VaR 95%", fmt.money(data.risk.var_95), `${fmt.pct(data.risk.var_95_pct, 2)} diario`, "neg"),
            card("Volatilidad", fmt.pct(data.risk.volatility, 1), "anualizada"),
            card("Máx. caída", fmt.pct(-(data.risk.max_drawdown || 0), 1), "cartera", "neg"),
          ]
        : []),
    ];
    $("#pfTotals").innerHTML = cards.slice(0, 4).join("");
    const extraBox = $("#pfTotalsExtra");
    if (extraBox) extraBox.innerHTML = cards.slice(4).join("");
    const moreBtn = $("#pfMore");
    if (moreBtn) {
      const hasExtra = cards.length > 4;
      moreBtn.classList.toggle("hidden", !hasExtra);
      if (!hasExtra) {
        extraBox.classList.add("hidden");
        moreBtn.textContent = "Más métricas";
      }
    }

    const positions = data.positions || [];
    $("#pfEmpty").classList.toggle("hidden", positions.length > 0);
    $("#pfTable").classList.toggle("hidden", positions.length === 0);
    const pfCards = $("#pfCards");
    if (pfCards) {
      pfCards.innerHTML = positions
        .map(
          (p) => `<div class="stock-card">
            <div class="stock-card-head">
              <div class="stock-card-id"><strong>${escapeHtml(p.symbol)}</strong><span>${nf2.format(p.shares)} acc. · entrada ${fmt.price(p.entry_price)}</span></div>
              <div class="stock-card-price"><strong>${fmt.price(p.price)}</strong><span class="${(p.pnl || 0) >= 0 ? "pos" : "neg"}">${fmt.money(p.pnl)} · ${fmt.pct(p.pnl_pct, 1)}</span></div>
            </div>
            <div class="stock-card-tags">
              ${actionBadge(p.signal)}
              <span class="tag">${fmt.money(p.value)}</span>
              <span class="tag">${fmt.pct(p.weight, 1)}</span>
              ${p.stop ? `<span class="tag">Stop ${fmt.price(p.stop)}</span>` : ""}
              ${p.target ? `<span class="tag">Obj. ${fmt.price(p.target)}</span>` : ""}
            </div>
            <div class="stock-card-foot">
              <span class="muted small">${p.risk_amount != null ? "Riesgo " + fmt.money(p.risk_amount) : ""}</span>
              <button class="icon-btn" data-pf-delete="${p.id}" title="Eliminar">✕</button>
            </div>
          </div>`
        )
        .join("");
    }
    $("#pfTable tbody").innerHTML = positions
      .map(
        (p) => `<tr>
          <td><strong>${escapeHtml(p.symbol)}</strong></td>
          <td class="num">${nf2.format(p.shares)}</td>
          <td class="num">${fmt.price(p.entry_price)}</td>
          <td class="num">${fmt.price(p.price)}</td>
          <td class="num">${fmt.money(p.value)}</td>
          <td class="num ${(p.pnl || 0) >= 0 ? "pos" : "neg"}">${fmt.money(p.pnl)}<br><span class="small">${fmt.pct(p.pnl_pct, 1)}</span></td>
          <td class="num">${fmt.pct(p.weight, 1)}</td>
          <td class="num">${actionBadge(p.signal)}</td>
          <td class="num">${p.risk_amount != null ? fmt.money(p.risk_amount) + '<br><span class="small">' + fmt.pct(p.risk_pct, 1) + "</span>" : "—"}</td>
          <td class="num">${p.r_multiple != null ? p.r_multiple.toFixed(2) + "R" : "—"}</td>
          <td class="num">${p.stop ? fmt.price(p.stop) : "—"}</td>
          <td class="num">${p.target ? fmt.price(p.target) : "—"}</td>
          <td><button class="icon-btn" data-pf-delete="${p.id}" title="Eliminar">✕</button></td>
        </tr>`
      )
      .join("");
  }

  function renderSectors(data) {
    $("#pfSectors").innerHTML = (data.sectors || [])
      .map(
        (item) =>
          `<span class="sector-chip">${escapeHtml(item.sector)} <b>${fmt.pct(item.weight, 0)}</b></span>`
      )
      .join("");
  }

  function renderSystem(data) {
    const quality = data.quality || {};
    const scheduler = data.scheduler || {};
    const db = data.db || {};
    const item = (k, v, s, cls) =>
      `<div class="system-item"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;

    const failed = quality.failed || [];
    $("#systemInfo").innerHTML = [
      item(
        "Escaneo diario",
        scheduler.installed ? "Instalado" : "No instalado",
        scheduler.installed
          ? `launchd · ${scheduler.interval ? `cada ${Math.round(scheduler.interval / 60)} min` : "cada hora"}`
          : "python3 scripts/install_schedule.py",
        scheduler.installed ? "pos" : "neg"
      ),
      item(
        "Última ejecución diaria",
        data.last_daily ? fmt.date(data.last_daily.slice(0, 10)) : "—",
        data.last_daily ? "marcador guardado" : "sin ejecuciones"
      ),
      item("Datos", data.last_run ? fmt.date(data.last_run.slice(0, 10)) : "—", `${nf0.format(db.cached_symbols || 0)} valores`),
      item("Fallidos", nf0.format(quality.failed_count || 0), "sin histórico", quality.failed_count ? "neg" : ""),
      item("Obsoletos", nf0.format(quality.stale_count || 0), "pendientes de cierre"),
      item("Base de datos", `${db.db_mb || 0} MB`, `${nf0.format(db.bars || 0)} barras`),
    ].join("") + (failed.length
      ? `<div class="system-item" style="grid-column:1/-1"><div class="k">Tickers sin datos</div><div class="failed-list">${failed.map((symbol) => `<span class="wchip">${escapeHtml(symbol)}</span>`).join("")}</div></div>`
      : "");

    $("#retryFailed").disabled = !quality.failed_count;
    const log = $("#scanLog");
    if (data.log_tail) {
      log.textContent = data.log_tail;
      log.classList.remove("hidden");
    } else {
      log.classList.add("hidden");
    }
  }

  function renderRisk(data) {
    const risk = data.risk;
    const panel = $("#pfRiskPanel");
    if (!risk || !risk.symbols || risk.symbols.length < 2) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    const symbols = risk.symbols;
    const header = `<tr><th></th>${symbols.map((s) => `<th class="num">${escapeHtml(s)}</th>`).join("")}</tr>`;
    const body = symbols
      .map((symbol, i) => {
        const cells = risk.matrix[i]
          .map((value) => {
            const alpha = Math.min(0.30, Math.abs(value) * 0.30);
            const color = value >= 0 ? `rgba(248,113,113,${alpha})` : `rgba(52,211,153,${alpha})`;
            return `<td class="num" style="background:${color}">${value.toFixed(2)}</td>`;
          })
          .join("");
        return `<tr><th>${escapeHtml(symbol)}</th>${cells}</tr>`;
      })
      .join("");
    $("#pfCorrWrap").innerHTML = `<table class="data-table corr-table"><thead>${header}</thead><tbody>${body}</tbody></table>`;
    $("#pfCorrNote").textContent = `Correlación media ${
      risk.avg_correlation != null ? risk.avg_correlation.toFixed(2) : "—"
    } · diversificación ${risk.diversification || "—"}${
      risk.limited ? " · riesgo calculado sobre el top 10 por valor" : ""
    }`;
  }

  function renderAlerts(data) {
    const alerts = data.alerts || [];
    $("#alList").innerHTML =
      alerts
        .map(
          (alert) => `<div class="al-item ${alert.active ? "" : "off"}">
            <span>${escapeHtml(alertDescription(alert))}</span>
            <span class="al-actions">
              <button class="icon-btn" data-al-toggle="${alert.id}" data-active="${alert.active ? 1 : 0}" title="Activar/desactivar">${alert.active ? "⏸" : "▶"}</button>
              <button class="icon-btn" data-al-delete="${alert.id}" title="Eliminar">✕</button>
            </span>
          </div>`
        )
        .join("") || `<p class="muted small">Sin alertas configuradas.</p>`;

    const events = data.events || [];
    $("#alEvents").innerHTML =
      events
        .slice(0, 20)
        .map(
          (event) => `<div class="event ${event.read ? "read" : ""}">
            <span class="dot"></span>
            <span>${escapeHtml(event.message)}</span>
            <span class="when">${event.created_at ? fmt.date(event.created_at.slice(0, 10)) : ""}</span>
          </div>`
        )
        .join("") || `<p class="muted small">Sin eventos todavía.</p>`;

    $("#alertCount").textContent = String(data.unread || 0);
    $("#alertCount").style.display = data.unread ? "inline-block" : "none";
    $("#alUnread").textContent = data.unread ? `${data.unread} sin leer` : "";
  }

  function alertDescription(alert) {
    if (alert.kind === "signal") return `${alert.symbol}: señal = ${alert.label || "—"}`;
    if (alert.kind === "earnings_within") return `${alert.symbol}: resultados en ≤ ${alert.threshold} días`;
    return `${alert.symbol}: ${ALERT_KINDS[alert.kind] || alert.kind} ${alert.threshold}`;
  }

  async function addPosition() {
    const symbol = $("#pfSymbol").value.trim().toUpperCase();
    const shares = Number($("#pfShares").value);
    const price = Number($("#pfPrice").value);
    if (!symbol || !(shares > 0) || !(price > 0)) return;
    try {
      await api("/api/portfolio/add", {
        symbol,
        shares,
        price,
        stop: $("#pfStop").value || "",
        target: $("#pfTarget").value || "",
      });
      ["#pfSymbol", "#pfShares", "#pfPrice", "#pfStop", "#pfTarget"].forEach(
        (id) => ($(id).value = "")
      );
      updateSuggestion();
      loadPortfolio();
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function deletePosition(id) {
    if (!window.confirm("¿Eliminar esta posición?")) return;
    try {
      await api("/api/portfolio/delete", { id });
      loadPortfolio();
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function addAlert() {
    const symbol = $("#alSymbol").value.trim().toUpperCase();
    const kind = $("#alKind").value;
    if (!symbol) return;
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
    try {
      await api("/api/alerts/add", {
        symbol,
        kind,
        threshold: kind === "signal" ? "" : $("#alValue").value,
        label: kind === "signal" ? $("#alSignal").value : "",
      });
      $("#alSymbol").value = "";
      $("#alValue").value = "";
      loadPortfolio();
      refreshAlertBadge();
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function deleteAlert(id) {
    try {
      await api("/api/alerts/delete", { id });
      loadPortfolio();
      refreshAlertBadge();
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function toggleAlert(id, active) {
    try {
      await api("/api/alerts/toggle", { id, active: active ? 0 : 1 });
      loadPortfolio();
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function checkAlerts() {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
    try {
      const data = await api("/api/alerts/check");
      if (data.triggered && data.triggered.length) {
        if ("Notification" in window && Notification.permission === "granted") {
          new Notification("StockLab · alertas", {
            body: data.triggered.map((item) => item.message).join(" · "),
          });
        }
      }
      loadPortfolio();
      refreshAlertBadge();
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function markAlertsRead() {
    try {
      await api("/api/alerts/read");
      loadPortfolio();
      refreshAlertBadge();
    } catch (e) {
      window.alert(e.message);
    }
  }

  /* ================= BACKTEST ================= */
  function wireBacktest() {
    $("#btRun").addEventListener("click", startBacktest);
    $("#btAdvancedBtn").addEventListener("click", () => {
      const panel = $("#btAdvancedBtn").closest(".controls");
      panel.classList.toggle("is-expanded");
      $("#btAdvancedBtn").textContent = panel.classList.contains("is-expanded")
        ? "Ocultar"
        : "Avanzado";
    });
    $("#btCalibrate").addEventListener("click", calibrateBacktest);
  }

  async function loadBacktest() {
    const saved = store.get("sl.btJob", null);
    if (saved) {
      try {
        const job = await api("/api/backtest/status", { id: saved });
        if (job.status === "running") {
          state.btJobId = job.id;
          $("#btRun").disabled = true;
          showBtProgress(job);
          pollBacktest();
          return;
        }
        if (job.status === "done" && job.result) {
          state.btJobId = job.id;
          renderBacktest(job.result);
          $("#btProgressPanel").classList.add("hidden");
          store.set("sl.btJob", null);
          return;
        }
      } catch (e) {
        /* trabajo perdido tras reiniciar el servidor */
      }
      store.set("sl.btJob", null);
    }
    try {
      const job = await api("/api/backtest/latest");
      if (job && job.result) {
        state.btJobId = job.id;
        renderBacktest(job.result);
      }
    } catch (e) {
      /* sin resultados previos */
    }
  }

  async function startBacktest() {
    const params = {
      symbols: $("#btSymbols").value,
      horizon: $("#btHorizon").value,
      rebalance: $("#btRebalance").value,
      years: $("#btYears").value,
      cost: $("#btCost").value,
      top_n: $("#btTopN").value,
      quality: $("#btQuality").checked ? 1 : 0,
    };
    $("#btRun").disabled = true;
    state.btPollErrors = 0;
    clearTimeout(state.hideBtTimer);
    state.hideBtTimer = null;
    $("#btError").classList.add("hidden");
    $("#btProgressPanel").classList.remove("hidden");
    $("#btPct").textContent = "0%";
    $("#btProgressFill").style.width = "0%";
    $("#btProgressTitle").textContent = "Preparando backtest…";
    $("#btStage").textContent = "";
    $("#btMeta").textContent = "";
    try {
      const job = await api("/api/backtest/start", params);
      state.btJobId = job.id;
      store.set("sl.btJob", job.id);
      showBtProgress(job);
      pollBacktest();
    } catch (e) {
      $("#btRun").disabled = false;
      showBtError(e.message);
    }
  }

  function pollBacktest() {
    if (state.btTimer) clearTimeout(state.btTimer);
    const jobId = state.btJobId;
    const tick = async () => {
      if (!jobId || state.btJobId !== jobId) return;
      try {
        const job = await api("/api/backtest/status", { id: jobId });
        if (state.btJobId !== jobId) return;
        state.btPollErrors = 0;
        showBtProgress(job);
        if (job.status === "running") {
          state.btTimer = setTimeout(tick, 1200);
        } else {
          store.set("sl.btJob", null);
          $("#btRun").disabled = false;
          if (job.status === "done" && job.result) {
            renderBacktest(job.result);
            clearTimeout(state.hideBtTimer);
            state.hideBtTimer = setTimeout(
              () => $("#btProgressPanel").classList.add("hidden"),
              2500
            );
            loadStatus();
          } else {
            showBtError(job.error || "El backtest no terminó correctamente");
          }
        }
      } catch (e) {
        state.btPollErrors += 1;
        if (state.btPollErrors >= 5) {
          store.set("sl.btJob", null);
          showBtError("Se perdió la conexión con el backtest. Vuelve a lanzarlo.");
          return;
        }
        state.btTimer = setTimeout(tick, 2500);
      }
    };
    state.btTimer = setTimeout(tick, 600);
  }

  function showBtProgress(job) {
    clearTimeout(state.hideBtTimer);
    state.hideBtTimer = null;
    const pct = Math.max(0, Math.min(100, job.progress || 0));
    $("#btProgressPanel").classList.remove("hidden");
    $("#btProgressFill").style.width = pct + "%";
    $("#btPct").textContent = pct + "%";
    $("#btProgressTitle").textContent =
      job.status === "running" ? "Ejecutando backtest…" : job.status === "done" ? "Backtest completado" : "Backtest detenido";
    $("#btStage").textContent = job.stage ? `· ${job.stage}` : "";
    if (job.status === "running") {
      $("#btMeta").textContent = "El cálculo sigue en el servidor aunque cambies de pestaña.";
    }
  }

  function showBtError(message) {
    $("#btError").textContent = message;
    $("#btError").classList.remove("hidden");
    $("#btProgressPanel").classList.add("hidden");
    $("#btRun").disabled = false;
  }

  function renderBacktest(r) {
    state.btResult = r;
    $("#btContent").classList.remove("hidden");
    const p = r.portfolio || {};
    const ic = r.ic || {};
    const t = r.targets || {};
    const buckets = r.buckets || {};
    let buyHit = null;
    const buyRows = [buckets["COMPRA FUERTE"], buckets["COMPRA"]].filter(Boolean);
    if (buyRows.length) {
      const n = buyRows.reduce((acc, b) => acc + b.n, 0);
      buyHit = buyRows.reduce((acc, b) => acc + b.hit_rate * b.n, 0) / n;
    }

    const card = (k, v, s, cls) =>
      `<div class="metric"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;

    const oosPeriod = r.oos || {};
    const cards = [
      card(
        "IC out-of-sample",
        oosPeriod.ic != null ? oosPeriod.ic.toFixed(3) : "—",
        oosPeriod.start ? `${fmt.date(oosPeriod.start)} → ${fmt.date(oosPeriod.end)}` : "sin datos",
        (oosPeriod.ic || 0) >= 0 ? "pos" : "neg"
      ),
      card(
        "IC medio",
        ic.mean != null ? ic.mean.toFixed(3) : "—",
        `${fmt.pct(ic.positive_ratio, 0)} de fechas positivas`
      ),
      card("Acierto compras", fmt.pct(buyHit, 1), "periodos positivos"),
      card("Objetivo antes de stop", fmt.pct(t.hit_rate, 1), `${nf0.format(t.evaluated || 0)} señales`),
    ];
    if (r.quality) {
      cards.push(
        card(
          "IC calidad SEC",
          r.quality.ic != null ? r.quality.ic.toFixed(3) : "—",
          `point-in-time · ${nf0.format(r.quality.coverage)} obs`
        )
      );
    }
    cards.push(
      card(
        "Cartera in-sample",
        fmt.pct(p.total, 1),
        `simulación · QQQ ${fmt.pct(p.bench_total, 1)}`,
        (p.total || 0) >= 0 ? "pos" : "neg"
      ),
      card("Máx. caída in-sample", fmt.pct(-(p.max_drawdown || 0), 1), `QQQ ${fmt.pct(-(p.bench_max_drawdown || 0), 1)}`, "neg")
    );
    $("#btKpis").innerHTML = cards.join("");

    $("#btCoverage").textContent = r.coverage
      ? `${r.coverage.symbols} valores · ${r.coverage.dates} fechas · ${fmt.date(r.coverage.start)} → ${fmt.date(r.coverage.end)}`
      : "";
    const walk = r.walk_forward;
    if (walk && walk.folds && walk.folds.length) {
      $("#btWfPanel").classList.remove("hidden");
      $("#btWfSummary").textContent = `IC actual medio ${fmt.num(walk.current_ic_mean, 3)} · optimizado ${fmt.num(walk.optimized_ic_mean, 3)} · folds positivos ${Math.round((walk.current_positive || 0) * 100)}%`;
      $("#btWf tbody").innerHTML = walk.folds
        .map(
          (fold) => `<tr>
            <td>${fold.fold}</td>
            <td class="small muted">${fmt.date(fold.test_start)} → ${fmt.date(fold.test_end)}</td>
            <td class="num ${(fold.current_ic || 0) >= 0 ? "pos" : "neg"}">${fmt.num(fold.current_ic, 3)}</td>
            <td class="num ${(fold.optimized_ic || 0) >= 0 ? "pos" : "neg"}">${fmt.num(fold.optimized_ic, 3)}</td>
            <td class="num">${fmt.pct(fold.optimized_spread, 2)}</td>
          </tr>`
        )
        .join("");
    } else {
      $("#btWfPanel").classList.add("hidden");
    }

    const cfg = r.config || {};
    if (cfg.max_symbols) $("#btSymbols").value = String(cfg.max_symbols);
    if (cfg.horizon) $("#btHorizon").value = String(cfg.horizon);
    if (cfg.rebalance) $("#btRebalance").value = String(cfg.rebalance);
    if (cfg.years) $("#btYears").value = String(Math.round(cfg.years));
    if (cfg.cost_bps != null) $("#btCost").value = String(cfg.cost_bps);
    if (cfg.top_n) $("#btTopN").value = String(cfg.top_n);
    $("#btQuality").checked = !!cfg.include_quality;
    $("#btMetaResults").textContent = `${r.coverage.symbols} valores · ${cfg.years} años · horizonte ${cfg.horizon} sesiones · rebalanceo ${cfg.rebalance} · ${cfg.include_quality ? "con" : "sin"} calidad SEC · coste ${cfg.cost_bps} pb`;

    const oosRows = [
      ["In-sample", r.is],
      ["Out-of-sample", r.oos],
    ]
      .filter(([, period]) => period)
      .map(
        ([label, period]) => `<tr>
          <td><strong>${label}</strong><div class="muted small">${fmt.date(period.start)} → ${fmt.date(period.end)}</div></td>
          <td class="num ${(period.ic || 0) >= 0 ? "pos" : "neg"}">${period.ic != null ? period.ic.toFixed(3) : "—"}</td>
          <td class="num">${fmt.pct(period.positive_ratio, 0)}</td>
          <td class="num ${(period.spread || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(period.spread, 2)}</td>
          <td class="num ${(period.buy_excess || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(period.buy_excess, 2)}</td>
          <td class="num">${fmt.pct(period.buy_hit, 1)}</td>
        </tr>`
      )
      .join("");
    $("#btOos tbody").innerHTML = oosRows;
    $("#btOosPanel").classList.toggle("hidden", !oosRows);

    if (!state.btChart) state.btChart = new LineChart($("#btChart"));
    state.btChart.resize();
    state.btChart.setData({
      dates: p.dates || [],
      series: [
        { name: "Cartera", color: "#34d399", values: p.equity || [] },
        { name: "QQQ", color: "#94a3b8", values: p.benchmark || [] },
      ],
    });
    requestAnimationFrame(() => state.btChart.resize());

    const order = ["COMPRA FUERTE", "COMPRA", "NEUTRAL", "VENTA", "VENTA FUERTE"];
    $("#btBuckets tbody").innerHTML = order
      .filter((a) => buckets[a])
      .map((a) => {
        const b = buckets[a];
        return `<tr>
          <td>${actionBadge(a)}</td>
          <td class="num">${nf0.format(b.n)}</td>
          <td class="num ${(b.avg_fwd || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(b.avg_fwd, 2)}</td>
          <td class="num ${(b.avg_excess || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(b.avg_excess, 2)}</td>
          <td class="num">${fmt.pct(b.hit_rate, 1)}</td>
        </tr>`;
      })
      .join("");

    const deciles = r.deciles || [];
    const maxAbs = Math.max(0.0001, ...deciles.map((d) => Math.abs(d.avg_fwd || 0)));
    $("#btDeciles").innerHTML = deciles
      .map((d) => {
        const value = d.avg_fwd || 0;
        const width = Math.max(3, (Math.abs(value) / maxAbs) * 100);
        const color = value >= 0 ? "linear-gradient(90deg,#0e7490,#34d399)" : "linear-gradient(90deg,#b91c1c,#f87171)";
        return `<div class="decile-row">
          <span>D${d.decile}</span>
          <div class="decile-bar"><i style="width:${width}%;background:${color}"></i></div>
          <span class="v ${value >= 0 ? "pos" : "neg"}">${fmt.pct(value, 1)}</span>
        </div>`;
      })
      .join("");

    $("#btTargets").innerHTML = [
      card("Objetivo alcanzado", nf0.format(t.target || 0), fmt.pct((t.target || 0) / Math.max(1, t.evaluated || 1), 1), "pos"),
      card("Stop primero", nf0.format(t.stop || 0), fmt.pct((t.stop || 0) / Math.max(1, t.evaluated || 1), 1), "neg"),
      card("Ambigüedad intradía", nf0.format(t.ambiguous || 0), "máximo y mínimo el mismo día"),
      card("Sin tocar", nf0.format(t.none || 0), "ni objetivo ni stop en 63 sesiones"),
    ].join("");
    $("#btTargetNote").textContent =
      "El objetivo se calcula a 3 meses; se comprueba si el precio lo alcanza antes que el stop usando máximos y mínimos diarios.";

    $("#btCalibration").className = "muted small";
    $("#btCalibration").textContent =
      "Pulsa «Calibrar» para probar combinaciones de pesos, ver qué factores aportan y aplicar los mejores.";
    state.btCalibration = null;
  }

  async function calibrateBacktest() {
    if (!state.btJobId) return;
    const btn = $("#btCalibrate");
    btn.disabled = true;
    btn.textContent = "Calibrando…";
    try {
      const cal = await api("/api/backtest/calibrate", { id: state.btJobId, samples: 40 });
      state.btCalibration = cal;
      renderCalibration(cal);
    } catch (e) {
      $("#btCalibration").className = "error-box";
      $("#btCalibration").textContent = e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "Calibrar";
    }
  }

  function renderCalibration(cal) {
    const labels = {
      trend: "Tendencia",
      momentum: "Momento",
      risk: "Riesgo",
      rsi: "RSI",
      liquidity: "Liquidez",
    };
    const box = $("#btCalibration");
    const factors = (cal.factors || []).slice(0, 10);
    const variants = cal.variants || [];

    const factorRows = factors
      .map((f) => {
        const ic = f.ic == null ? 0 : f.ic;
        const cls = ic > 0.005 ? "up" : ic < -0.005 ? "down" : "";
        return `<tr>
          <td>${labels[f.factor] || f.factor}</td>
          <td><span class="factor-badge ${f.side === "invertido" ? "down" : ""}">${f.side}</span></td>
          <td class="num ${ic >= 0 ? "pos" : "neg"}">${ic.toFixed(3)}</td>
          <td class="num ${(f.spread || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(f.spread, 2)}</td>
          <td><span class="factor-badge ${cls}">${cls === "up" ? "aporta" : cls === "down" ? "resta" : "neutro"}</span></td>
        </tr>`;
      })
      .join("");

    const variantRows = variants
      .slice(0, 6)
      .map((v, i) => {
        const chips = Object.entries(v.weights)
          .filter(([, w]) => w >= 0.03)
          .map(([k, w]) => `<span class="wchip">${labels[k] || k} <b>${(w * 100).toFixed(0)}%</b></span>`)
          .join("");
        const inv = v.invert && v.invert.length
          ? `<span class="wchip" title="Factores invertidos">↺ ${v.invert.map((k) => labels[k] || k).join(", ")}</span>`
          : "";
        return `<tr>
          <td>${escapeHtml(v.name)}</td>
          <td class="num ${(v.ic || 0) >= 0 ? "pos" : "neg"}">${(v.ic || 0).toFixed(3)}</td>
          <td class="num ${(v.spread || 0) >= 0 ? "pos" : "neg"}">${fmt.pct(v.spread, 2)}</td>
          <td><div class="bt-weights">${chips}${inv}</div></td>
          <td class="muted small">diagnóstico</td>
        </tr>`;
      })
      .join("");

    const bestIc = variants.length ? variants[0].ic : 0;
    const currentIc = state.btResult && state.btResult.ic ? state.btResult.ic.mean : null;
    const warning =
      bestIc < 0.05
        ? `<div class="calib-note">Mejor IC ${bestIc.toFixed(3)}${
            currentIc != null ? ` frente al actual ${currentIc.toFixed(3)}` : ""
          }, todavía por debajo de 0,05: la señal ordena mal los retornos a este horizonte. Prueba otro horizonte (5 o 63 sesiones) o añade factores fundamentales. Los pesos son in-sample y pueden sobreajustarse.</div>`
        : `<div class="calib-note">Calibración in-sample: los pesos pueden sobreajustarse. Valida la variante elegida en otro periodo antes de usarla de forma sistemática.</div>`;

    box.className = "";
    box.innerHTML = `
      <div class="table-wrap" style="margin-bottom:18px">
        <table class="data-table">
          <thead><tr><th>Factor</th><th>Dirección</th><th class="num">IC</th><th class="num">Spread D10-D1</th><th>Veredicto</th></tr></thead>
          <tbody>${factorRows}</tbody>
        </table>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Variante</th><th class="num">IC</th><th class="num">Spread</th><th>Pesos</th><th></th></tr></thead>
          <tbody>${variantRows}</tbody>
        </table>
      </div>
      ${warning}`;

  }


  /* ------------------------------------------------------------------ */
  function escapeHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, "&quot;");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
