/* Plotting for the workbench UI.
 *
 * Everything here takes data already shaped by ProjectData and draws it. No
 * fetching of raw files, no arithmetic beyond what a plot needs (R*Q^4, error
 * bands), so that what you see is what the API returned.
 */
"use strict";

const NRW = (function () {
  const HAVE_PLOTLY = typeof Plotly !== "undefined";

  /* Shared layout so the heatmap and the a(t) trace below it line up. The left
   * and right margins must match exactly or the two time axes are offset by a
   * few pixels, which makes the cursor look wrong even when it is right. */
  const MARGIN_L = 64;
  const MARGIN_R = 24;

  const FONT = {
    family:
      'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
    size: 11,
  };

  const CONFIG = {
    displaylogo: false,
    responsive: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
  };

  /* Qualitative palette, colour-blind safe (Okabe-Ito). Steady states are
   * distinguished by colour and angle segments by marker, so a reader can tell
   * "different state" from "different angle" without the legend. */
  const PALETTE = [
    "#0072b2",
    "#d55e00",
    "#009e73",
    "#cc79a7",
    "#56b4e9",
    "#e69f00",
    "#000000",
  ];
  const SYMBOLS = ["circle", "square", "diamond", "triangle-up", "x"];

  function colourFor(index) {
    return PALETTE[index % PALETTE.length];
  }

  /* ------------------------------------------------------------------ */
  /* Panel 1: reflectivity                                               */
  /* ------------------------------------------------------------------ */

  /* Group curves by run so every segment of one measurement shares a colour. */
  function groupKeys(curves) {
    const seen = [];
    curves.forEach(function (c) {
      const key = c.run !== undefined ? String(c.run) : c.label.split("#")[0];
      if (seen.indexOf(key) === -1) seen.push(key);
    });
    return seen;
  }

  function rq4(q, r) {
    return r === null || q === null ? null : r * Math.pow(q, 4);
  }

  function reflTraces(curves, mode) {
    const keys = groupKeys(curves);
    const perKey = {};
    return curves.map(function (curve) {
      const key = curve.run !== undefined ? String(curve.run) : curve.label.split("#")[0];
      const colour = colourFor(keys.indexOf(key));
      perKey[key] = (perKey[key] || 0) + 1;
      const symbol = SYMBOLS[(perKey[key] - 1) % SYMBOLS.length];

      const y =
        mode === "rq4"
          ? curve.q.map(function (q, i) {
              return rq4(q, curve.r[i]);
            })
          : curve.r;
      const dy =
        mode === "rq4"
          ? curve.q.map(function (q, i) {
              return rq4(q, curve.dr ? curve.dr[i] : null);
            })
          : curve.dr;

      return {
        x: curve.q,
        y: y,
        error_y: dy ? { type: "data", array: dy, visible: true, width: 0, thickness: 0.6 } : undefined,
        mode: "markers",
        type: "scattergl",
        name: curve.label,
        legendgroup: key,
        marker: { color: colour, symbol: symbol, size: 4 },
        hovertemplate: curve.label + "<br>Q=%{x:.5f}<br>%{y:.4g}<extra></extra>",
      };
    });
  }

  /* Draw R(Q). Returns a handle that can overlay one extra curve -- used by
   * the time cursor to show the tNR slice against the steady states. */
  function reflPanel(divId, curves, options) {
    if (!HAVE_PLOTLY) return null;
    const opts = options || {};
    let mode = "r";
    let overlay = null;

    function layout() {
      return {
        margin: { l: MARGIN_L, r: MARGIN_R, t: 8, b: 44 },
        font: FONT,
        /* log R against log Q. The Fresnel decay is a power law, so log-log
         * straightens it -- the fringes stay legible across four decades of Q
         * instead of piling up at the left. */
        xaxis: {
          title: { text: "Q (Å⁻¹)" },
          type: "log",
          exponentformat: "power",
          zeroline: false,
        },
        yaxis: {
          title: { text: mode === "rq4" ? "R · Q⁴" : "R" },
          type: "log",
          exponentformat: "power",
        },
        showlegend: true,
        legend: { font: { size: 10 }, orientation: "v", x: 1.005, y: 1 },
        hovermode: "closest",
        uirevision: "refl",
      };
    }

    function traces() {
      const base = reflTraces(curves, mode);
      if (overlay) {
        const y =
          mode === "rq4"
            ? overlay.q.map(function (q, i) {
                return rq4(q, overlay.r[i]);
              })
            : overlay.r;
        base.push({
          x: overlay.q,
          y: y,
          mode: "lines+markers",
          type: "scattergl",
          name: overlay.label,
          marker: { color: "#111", size: 3 },
          line: { color: "#111", width: 1.5 },
          hovertemplate: overlay.label + "<br>Q=%{x:.5f}<br>%{y:.4g}<extra></extra>",
        });
      }
      return base;
    }

    Plotly.newPlot(divId, traces(), layout(), CONFIG);

    if (opts.toggleId) {
      document.getElementById(opts.toggleId).addEventListener("change", function (e) {
        mode = e.target.checked ? "rq4" : "r";
        Plotly.react(divId, traces(), layout(), CONFIG);
      });
    }

    return {
      setOverlay: function (curve) {
        overlay = curve;
        Plotly.react(divId, traces(), layout(), CONFIG);
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Panels 2 and 3: the tNR series and its change amplitude             */
  /* ------------------------------------------------------------------ */

  /* Symmetric colour limits about zero, from a robust high percentile. A few
   * noisy high-Q cells otherwise set the scale and flatten everything else to
   * white. */
  function symmetricLimit(rows) {
    const values = [];
    rows.forEach(function (row) {
      row.forEach(function (v) {
        if (v !== null && isFinite(v)) values.push(Math.abs(v));
      });
    });
    if (!values.length) return 1;
    values.sort(function (a, b) {
      return a - b;
    });
    const limit = values[Math.floor(values.length * 0.98)];
    return limit > 0 ? limit : 1;
  }

  function timeAxis(series, showTitle) {
    const times = series.times;
    const span = times.length ? times[times.length - 1] - times[0] : 1;
    const pad = span * 0.02 || 1;
    return {
      title: showTitle ? { text: "Time (s)" } : undefined,
      range: [times[0] - pad, times[times.length - 1] + pad],
      zeroline: false,
    };
  }

  /* The heatmap is transposed relative to the API: residual arrives as
   * [interval][q], and Plotly wants z[row=y][col=x] with y = Q, x = time. */
  function transpose(rows, nCols) {
    const out = [];
    for (let c = 0; c < nCols; c += 1) {
      const column = new Array(rows.length);
      for (let r = 0; r < rows.length; r += 1) column[r] = rows[r][c];
      out.push(column);
    }
    return out;
  }

  function seriesPanels(heatmapId, amplitudeId, series, amplitude, onCursor) {
    if (!HAVE_PLOTLY) return null;

    const limit = symmetricLimit(series.residual);
    const z = transpose(series.residual, series.q.length);

    const heatLayout = {
      margin: { l: MARGIN_L, r: MARGIN_R, t: 8, b: amplitude ? 8 : 44 },
      font: FONT,
      xaxis: Object.assign(timeAxis(series, !amplitude), {
        showticklabels: !amplitude,
      }),
      yaxis: { title: { text: "Q (Å⁻¹)" } },
      shapes: [],
      uirevision: "series",
    };

    Plotly.newPlot(
      heatmapId,
      [
        {
          type: "heatmap",
          x: series.times,
          y: series.q,
          z: z,
          zmin: -limit,
          zmax: limit,
          colorscale: "RdBu",
          reversescale: true,
          colorbar: {
            title: { text: "ΔR/R", side: "right" },
            thickness: 10,
            len: 0.9,
          },
          hovertemplate: "t=%{x:.0f} s<br>Q=%{y:.5f}<br>ΔR/R=%{z:.4f}<extra></extra>",
        },
      ],
      heatLayout,
      CONFIG
    );

    let amplitudeLayout = null;
    if (amplitude) {
      const shade = [];
      /* Shade the reference and late blocks so the reader can see what a=0 and
       * a=1 were defined against -- the trajectory is meaningless without it. */
      [
        { idx: series.reference.indices, colour: "rgba(0,114,178,0.10)" },
        { idx: series.late.indices, colour: "rgba(213,94,0,0.10)" },
      ].forEach(function (block) {
        if (!block.idx || !block.idx.length) return;
        const first = series.times[block.idx[0]];
        const last = series.times[block.idx[block.idx.length - 1]];
        shade.push({
          type: "rect",
          xref: "x",
          yref: "paper",
          x0: first,
          x1: last,
          y0: 0,
          y1: 1,
          fillcolor: block.colour,
          line: { width: 0 },
          layer: "below",
        });
      });

      amplitudeLayout = {
        margin: { l: MARGIN_L, r: MARGIN_R, t: 8, b: 44 },
        font: FONT,
        xaxis: timeAxis(series, true),
        yaxis: { title: { text: "a(t)" }, zeroline: true },
        shapes: shade,
        showlegend: false,
        uirevision: "series",
      };

      Plotly.newPlot(
        amplitudeId,
        [
          {
            x: amplitude.times,
            y: amplitude.a,
            error_y: {
              type: "data",
              array: amplitude.sigma,
              visible: true,
              width: 0,
              thickness: 1,
            },
            mode: "markers+lines",
            type: "scatter",
            marker: { color: "#0072b2", size: 5 },
            line: { color: "#0072b2", width: 1 },
            text: amplitude.labels,
            hovertemplate:
              "%{text}<br>t=%{x:.0f} s<br>a=%{y:.4f}<extra></extra>",
          },
        ],
        amplitudeLayout,
        CONFIG
      );
    }

    /* --- the time cursor ------------------------------------------- */

    let cursorTime = null;

    function cursorShape(t) {
      return {
        type: "line",
        xref: "x",
        yref: "paper",
        x0: t,
        x1: t,
        y0: 0,
        y1: 1,
        line: { color: "#111", width: 1.5, dash: "dot" },
      };
    }

    function nearestIndex(t) {
      let best = 0;
      let bestDistance = Infinity;
      series.times.forEach(function (value, i) {
        const distance = Math.abs(value - t);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = i;
        }
      });
      return best;
    }

    function setCursor(t) {
      const index = nearestIndex(t);
      const snapped = series.times[index];
      if (snapped === cursorTime) return;
      cursorTime = snapped;

      Plotly.relayout(heatmapId, { shapes: [cursorShape(snapped)] });
      if (amplitude) {
        Plotly.relayout(amplitudeId, {
          shapes: (amplitudeLayout.shapes || []).concat([cursorShape(snapped)]),
        });
      }
      if (onCursor) onCursor(index, snapped);
    }

    /* Click sets the cursor; holding the button and moving drags it. Plotly's
     * own drag mode is zoom, so the drag is only active while the pointer is
     * down over the plot -- zoom still works with a modifier or the modebar. */
    function attach(divId) {
      const div = document.getElementById(divId);
      if (!div) return;
      let dragging = false;

      div.on("plotly_click", function (event) {
        if (event.points && event.points.length) setCursor(event.points[0].x);
      });
      div.addEventListener("mousedown", function () {
        dragging = true;
      });
      window.addEventListener("mouseup", function () {
        dragging = false;
      });
      div.on("plotly_hover", function (event) {
        if (dragging && event.points && event.points.length) {
          setCursor(event.points[0].x);
        }
      });
    }

    attach(heatmapId);
    if (amplitude) attach(amplitudeId);

    return {
      setCursor: setCursor,
      step: function (delta) {
        const current = cursorTime === null ? series.times[0] : cursorTime;
        const index = Math.min(
          Math.max(nearestIndex(current) + delta, 0),
          series.times.length - 1
        );
        setCursor(series.times[index]);
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Fit detail                                                          */
  /* ------------------------------------------------------------------ */

  /* Data and model per experiment, with a normalised-residual strip below.
   * The residual is what shows a systematic misfit that a log-R plot hides. */
  function fitPanel(divId, residualId, curves) {
    if (!HAVE_PLOTLY) return;

    const dataTraces = [];
    const residualTraces = [];
    curves.forEach(function (curve, i) {
      const colour = colourFor(i);
      dataTraces.push({
        x: curve.q,
        y: curve.r,
        error_y: curve.dr
          ? { type: "data", array: curve.dr, visible: true, width: 0, thickness: 0.6 }
          : undefined,
        mode: "markers",
        type: "scattergl",
        name: curve.label,
        legendgroup: curve.label,
        marker: { color: colour, size: 3.5 },
        hovertemplate: curve.label + "<br>Q=%{x:.5f}<br>R=%{y:.4g}<extra></extra>",
      });
      if (curve.theory) {
        dataTraces.push({
          x: curve.q,
          y: curve.theory,
          mode: "lines",
          type: "scattergl",
          name: curve.label + " fit",
          legendgroup: curve.label,
          showlegend: false,
          line: { color: colour, width: 1.4 },
          hoverinfo: "skip",
        });
        residualTraces.push({
          x: curve.q,
          y: curve.q.map(function (_, k) {
            const dr = curve.dr ? curve.dr[k] : null;
            if (!dr || curve.r[k] === null || curve.theory[k] === null) return null;
            return (curve.r[k] - curve.theory[k]) / dr;
          }),
          mode: "markers",
          type: "scattergl",
          name: curve.label,
          legendgroup: curve.label,
          showlegend: false,
          marker: { color: colour, size: 3 },
          hovertemplate: curve.label + "<br>Q=%{x:.5f}<br>%{y:.2f}σ<extra></extra>",
        });
      }
    });

    Plotly.newPlot(
      divId,
      dataTraces,
      {
        margin: { l: MARGIN_L, r: MARGIN_R, t: 8, b: 8 },
        font: FONT,
        xaxis: { type: "log", showticklabels: false, zeroline: false },
        yaxis: { title: { text: "R" }, type: "log", exponentformat: "power" },
        legend: { font: { size: 10 }, x: 1.005, y: 1 },
        hovermode: "closest",
        uirevision: "fit",
      },
      CONFIG
    );

    Plotly.newPlot(
      residualId,
      residualTraces,
      {
        margin: { l: MARGIN_L, r: MARGIN_R, t: 8, b: 44 },
        font: FONT,
        /* Matches the panel above so the two line up point for point. */
        xaxis: {
          title: { text: "Q (Å⁻¹)" },
          type: "log",
          exponentformat: "power",
          zeroline: false,
        },
        yaxis: { title: { text: "(R−fit)/σ" }, zeroline: true },
        showlegend: false,
        uirevision: "fit",
      },
      CONFIG
    );
  }

  /* Draw SLD profiles.
   *
   * z is referenced to the SUBSTRATE SURFACE by default. refl1d puts z = 0 at
   * the top of the stack, so two models whose total thickness differs are
   * drawn offset from each other and the buried layers never line up -- a 3 A
   * change in copper shifts the titanium and the substrate with it. Anchoring
   * the one interface that cannot move puts every profile on a common footing,
   * and only the layer that actually changed moves.
   *
   * The credible band is drawn for at most a couple of profiles. Twenty-one
   * filled regions is unreadable, and the question a band answers -- how well
   * is this structure determined -- is asked of one curve at a time. */
  function sldPanel(divId, profiles, options) {
    if (!HAVE_PLOTLY) return;
    const opts = options || {};
    const aligned = opts.raw !== true;
    const bands = opts.bands || {};

    function shifted(profile) {
      const d = aligned ? profile.substrate_offset || 0 : 0;
      return profile.z.map(function (z) { return z - d; });
    }

    const traces = [];
    profiles.forEach(function (profile, i) {
      const colour = colourFor(i);
      const band = bands[profile.label];
      if (band) {
        const zs = shifted(profile);
        traces.push({
          x: zs.concat(zs.slice().reverse()),
          y: band.hi.concat(band.lo.slice().reverse()),
          fill: "toself",
          fillcolor: colour + "26",
          line: { width: 0 },
          type: "scatter",
          mode: "lines",
          hoverinfo: "skip",
          showlegend: false,
        });
      }
      traces.push({
        x: shifted(profile),
        y: profile.rho,
        mode: "lines",
        type: "scattergl",
        name: profile.label,
        line: { color: colour, width: band ? 1.8 : 1.2 },
        hovertemplate: profile.label + "<br>z=%{x:.1f} Å<br>ρ=%{y:.4g}<extra></extra>",
      });
    });
    Plotly.newPlot(
      divId,
      traces,
      {
        margin: { l: MARGIN_L, r: MARGIN_R, t: 8, b: 44 },
        font: FONT,
        xaxis: {
          title: {
            text: aligned ? "z from the substrate surface (Å)" : "z (Å)",
          },
        },
        yaxis: { title: { text: "SLD (10⁻⁶ Å⁻²)" } },
        legend: { font: { size: 10 }, x: 1.005, y: 1 },
        uirevision: "sld",
      },
      CONFIG
    );
  }

  return {
    reflPanel: reflPanel,
    seriesPanels: seriesPanels,
    fitPanel: fitPanel,
    sldPanel: sldPanel,
    havePlotly: HAVE_PLOTLY,
  };
})();

/* Layer parameters through time, for a fit with a series.
 *
 * The band is a credible interval from the posterior, so it is drawn as a
 * filled region rather than error bars: the neighbouring points are strongly
 * correlated -- they are computed from the same two endpoints -- and per-point
 * bars would suggest an independence that is not there.
 */
NRW.trajectoryPanel = function (divId, traces, options) {
  if (typeof Plotly === "undefined" || !traces.length) return;
  const opts = options || {};

  const shown = opts.all ? traces : traces.filter(function (t) { return t.varies; });
  if (!shown.length) return;

  const rows = shown.length;
  const data = [];
  const layout = {
    margin: { l: 74, r: 24, t: 8, b: 44 },
    font: { family: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif', size: 11 },
    showlegend: false,
    hovermode: "x unified",
    grid: { rows: rows, columns: 1, pattern: "independent", roworder: "top to bottom" },
    height: Math.max(180, rows * 150),
  };

  shown.forEach(function (t, i) {
    const axis = i === 0 ? "" : String(i + 1);
    const colour = ["#0072b2", "#d55e00", "#009e73", "#cc79a7", "#56b4e9", "#e69f00"][i % 6];

    if (t.lo && t.hi) {
      data.push({
        x: t.times.concat(t.times.slice().reverse()),
        y: t.hi.concat(t.lo.slice().reverse()),
        fill: "toself",
        fillcolor: colour.replace("#", "rgba(").length ? colour + "33" : colour,
        line: { width: 0 },
        type: "scatter",
        mode: "lines",
        hoverinfo: "skip",
        xaxis: "x" + axis,
        yaxis: "y" + axis,
      });
    }
    /* The posterior median, drawn faintly under the reported value.
     *
     * The reported value is the maximum-likelihood point: one parameter vector
     * the model was actually evaluated at, and the one the plotted curves and
     * the quoted chi-squared come from. The marginal median is not a parameter
     * vector at all -- each parameter's median taken independently can be a
     * stack no sample contains and that fits worse than either.
     *
     * So the best fit is what is reported. The median is shown because the gap
     * between them is diagnostic: 1.2 sigma on a real fit here, which says the
     * posterior is skewed or something is railing against a bound. */
    if (t.median) {
      data.push({
        x: t.times,
        y: t.median,
        mode: "lines",
        type: "scatter",
        name: t.path + " (median)",
        line: { color: colour, width: 3, dash: "solid" },
        opacity: 0.28,
        hovertemplate: "posterior median %{y:.4g}<extra></extra>",
        xaxis: "x" + axis,
        yaxis: "y" + axis,
      });
    }
    data.push({
      x: t.times,
      y: t.values,
      mode: "lines+markers",
      type: "scatter",
      name: t.path,
      line: { color: colour, width: 1.6 },
      marker: { color: colour, size: 4 },
      hovertemplate: t.path + " = %{y:.4g} (best fit)<extra></extra>",
      xaxis: "x" + axis,
      yaxis: "y" + axis,
    });

    layout["yaxis" + axis] = {
      title: { text: t.path + (t.constrained ? "" : " *"), font: { size: 10 } },
      zeroline: false,
    };
    layout["xaxis" + axis] = {
      title: i === rows - 1 ? { text: "Time (s)" } : undefined,
      showticklabels: i === rows - 1,
      zeroline: false,
    };
  });

  Plotly.newPlot(divId, data, layout, {
    displaylogo: false,
    responsive: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
  });
};
