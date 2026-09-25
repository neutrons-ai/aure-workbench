/* The Experiment page: every run, organized into samples, and apply.
 *
 * Everything shown comes from /api/experiment and nothing here decides
 * anything: the catalog validates edits, apply decides what may be copied.
 *
 * Every value that came from a file or a person is set with textContent. Run
 * titles are typed by an operator at the instrument, and will later come from
 * a remote source; a title containing markup must render as text. The one
 * exception is the sample.md preview, which the server renders with raw HTML
 * disabled -- exactly as the sample page renders sample.md.
 */
"use strict";

(function () {
  const initial = window.NRW_EXPERIMENT;
  const TOKEN = window.NRW_WRITE_TOKEN || "";
  const POLL_MS = 10000;
  const MAX_QUICK = 8;

  const STATE_BADGE = {
    complete: "text-bg-success",
    unconfirmed: "text-bg-warning",
    arriving: "text-bg-info",
    settling: "text-bg-info",
    awaiting: "text-bg-secondary",
    quarantined: "text-bg-danger",
    "clock-skew": "text-bg-danger",
    "not listed": "text-bg-light",
  };
  const MD_STATE = {
    create: ["will be created", "text-bg-info"],
    unchanged: ["in step", "text-bg-success"],
    upgrade: ["will be updated", "text-bg-info"],
    drifted: ["edited by hand", "text-bg-warning"],
    untracked: ["not written by nrw", "text-bg-warning"],
  };
  const SYMBOL = {
    copy: "+", restore: "+", record: "=", "move-out": "−", deferred: "…",
    "not-managed": "?",
  };

  const state = {
    rows: new Map(),
    samples: [],
    payload: initial,
    cursor: initial.cursor,
    catalogVersion: initial.catalog.version,
    filter: "all",
    search: "",
    selected: new Set(),
    editing: null,
    writable: Boolean(initial.writable && TOKEN),
    confirmed: new Set(),
    plan: null,
  };

  function $(id) {
    return document.getElementById(id);
  }

  /* createElement with text and attributes; never innerHTML. */
  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(function ([key, value]) {
      if (value === undefined || value === null || value === false) return;
      if (key === "text") node.textContent = value;
      else if (key === "className") node.className = value;
      else if (key in node && key !== "title") node[key] = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    });
    (children || []).forEach(function (child) {
      if (child !== null && child !== undefined) {
        node.append(typeof child === "string" ? document.createTextNode(child) : child);
      }
    });
    return node;
  }

  async function api(method, path, body) {
    const init = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    if (method !== "GET") init.headers["X-NRW-Token"] = TOKEN;
    const response = await fetch(path, init);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      const error = new Error((payload && payload.error) || "HTTP " + response.status);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function message(text, kind) {
    const box = $("expt-message");
    box.className = "alert py-2 alert-" + (kind || "info");
    box.textContent = text;
    if (kind === "success") {
      setTimeout(function () {
        box.classList.add("d-none");
      }, 4000);
    }
  }

  async function failed(error, what) {
    if (error.status === 409) {
      message(error.message + " The page has been reloaded.", "warning");
      await reload();
    } else {
      message(what + ": " + error.message, "danger");
    }
  }

  /* ---------------------------------------------------------------- */
  /* Loading and polling                                                */
  /* ---------------------------------------------------------------- */

  function load(payload) {
    state.payload = payload;
    state.rows = new Map(payload.runs.map(function (row) {
      return [row.key, row];
    }));
    state.samples = payload.samples;
    state.cursor = payload.cursor;
    state.catalogVersion = payload.catalog.version;
    state.writable = Boolean(payload.writable && TOKEN);
    for (const key of Array.from(state.selected)) {
      if (!state.rows.has(key)) state.selected.delete(key);
    }
    renderAll();
  }

  async function reload() {
    load(await api("GET", "/api/experiment"));
    if (state.editing) openSample(state.editing);
  }

  async function poll() {
    try {
      const changes = await api(
        "GET", "/api/experiment/changes?since=" + encodeURIComponent(state.cursor)
      );
      if (changes.resync || changes.catalog_version !== state.catalogVersion) {
        await reload();
        return;
      }
      changes.runs.forEach(function (row) {
        state.rows.set(row.key, row);
      });
      changes.removed.forEach(function (key) {
        state.rows.delete(key);
        state.selected.delete(key);
      });
      state.cursor = changes.cursor;
      renderScan(changes.scan);
      renderProblems(changes.problems);
      if (changes.runs.length || changes.removed.length) renderRuns();
      firstLook(changes.scan);
    } catch (error) {
      renderScan(null, error.message);
    }
  }

  /* The server lists the data source in the background, so the very first
   * page can arrive before that listing has finished. Ask again every second
   * until it has, rather than showing an empty table for a whole poll. */
  let firstLookTimer = null;
  function firstLook(scan) {
    clearTimeout(firstLookTimer);
    if (scan && scan.scanned_at === null) firstLookTimer = setTimeout(poll, 1000);
  }

  /* ---------------------------------------------------------------- */
  /* Header                                                             */
  /* ---------------------------------------------------------------- */

  function renderHeader() {
    const payload = state.payload;
    $("expt-ipts").textContent = payload.ipts || "(no IPTS in nrw.toml)";
    const source = payload.source || {};
    $("expt-source").textContent =
      (source.kind || "") + "  " + (source.path || source.location || "");
    const banner = $("expt-readonly");
    if (state.writable) {
      banner.classList.add("d-none");
    } else {
      banner.classList.remove("d-none");
      banner.textContent = payload.read_only_reason ||
        "View only. To edit the experiment, open the link `nrw serve` printed " +
        "when it started, then reload this page.";
    }
    renderScan(payload.scan);
  }

  function renderScan(scan, error) {
    const node = $("expt-scan");
    if (error) {
      node.textContent = "cannot reach the server: " + error;
      return;
    }
    if (!scan || scan.scanned_at === null) {
      node.textContent = "first look at the data source in progress…";
      return;
    }
    let text = "checked " + Math.round(scan.age) + " s ago";
    if (scan.stuck) text = "the data source is not answering";
    else if (scan.scanning) text += " · checking now";
    node.textContent = text;
  }

  function renderProblems(problems) {
    const box = $("expt-problems");
    const list = box.querySelector("ul");
    list.replaceChildren();
    (problems || []).forEach(function (problem) {
      list.append(el("li", { text: problem.message }));
    });
    box.classList.toggle("d-none", !(problems || []).length);
  }

  /* ---------------------------------------------------------------- */
  /* Runs                                                               */
  /* ---------------------------------------------------------------- */

  function visibleRows() {
    const needle = state.search.trim().toLowerCase();
    return Array.from(state.rows.values())
      .filter(function (row) {
        if (state.filter === "unassigned" && row.sample) return false;
        if (state.filter === "arriving" &&
            ["arriving", "settling", "awaiting"].indexOf(row.state) === -1) return false;
        if (state.filter === "attention" &&
            ["unconfirmed", "quarantined", "clock-skew", "not listed"].indexOf(row.state) === -1) {
          return false;
        }
        if (!needle) return true;
        return [String(row.run), row.title || "", row.sample || ""].some(function (text) {
          return text.toLowerCase().indexOf(needle) !== -1;
        });
      })
      .sort(function (a, b) {
        return a.run - b.run;
      });
  }

  function segmentsText(row) {
    if (!row.segments.length) return "—";
    const listed = row.segments.join(",");
    return row.n_segments ? listed + " of " + row.n_segments : listed;
  }

  function renderRuns() {
    const tbody = $("expt-runs").querySelector("tbody");
    tbody.replaceChildren();
    const rows = visibleRows();
    rows.forEach(function (row) {
      const box = el("input", {
        type: "checkbox",
        checked: state.selected.has(row.key),
        "aria-label": "select run " + row.run,
      });
      box.addEventListener("change", function () {
        if (box.checked) state.selected.add(row.key);
        else state.selected.delete(row.key);
        renderBulk();
        scheduleQuickLook();
      });
      const badge = el("span", {
        className: "badge " + (STATE_BADGE[row.state] || "text-bg-light"),
        text: row.state,
        title: row.reason,
      });
      const thetas = row.thetas.filter(function (t) {
        return t !== null;
      }).map(function (t) {
        return t.toFixed(2);
      }).join(" / ");
      const tr = el("tr", { className: row.include ? "" : "text-secondary" }, [
        el("td", {}, [box]),
        el("td", { className: "mono", text: String(row.run) }),
        el("td", {}, [badge]),
        el("td", { className: "small expt-title", text: row.title || "", title: row.reason }),
        el("td", { className: "small mono", text: segmentsText(row) }),
        el("td", { className: "small mono", text: thetas || "—" }),
        el("td", { className: "mono", text: row.sample || "—" }),
        el("td", { className: "small", text: row.measurement }),
        el("td", { className: "small", text: row.condition }),
        el("td", { className: "small" }, [row.include ? null : el("span", {
          className: "badge text-bg-secondary", text: "excluded",
        })]),
      ]);
      tbody.append(tr);
    });
    const empty = $("expt-empty");
    empty.classList.toggle("d-none", rows.length > 0);
    empty.textContent = state.rows.size
      ? "No runs match."
      : "No runs yet. They appear here as the reduction writes them.";
    $("expt-select-all").checked =
      rows.length > 0 && rows.every(function (row) {
        return state.selected.has(row.key);
      });
  }

  function renderBulk() {
    const bar = $("expt-bulk");
    const count = state.selected.size;
    bar.classList.toggle("d-none", !(state.writable && count));
    $("expt-selected").textContent = count + " selected";
    const select = $("expt-sample");
    const current = select.value;
    select.replaceChildren(el("option", { value: "", text: "to sample…" }));
    state.samples.filter(function (card) {
      return card.managed;
    }).forEach(function (card) {
      select.append(el("option", { value: card.id, text: card.id }));
    });
    select.append(el("option", { value: "__new__", text: "new sample…" }));
    if (current) select.value = current;
    $("expt-new-sample").classList.toggle("d-none", select.value !== "__new__");
  }

  async function editRuns(fields, what) {
    const changes = Array.from(state.selected).map(function (key) {
      const row = state.rows.get(key);
      return { run: row.run, base_rev: row.rev, fields: fields };
    });
    try {
      const result = await api("PUT", "/api/experiment/runs", { changes: changes });
      result.runs.forEach(function (row) {
        state.rows.set(row.key, row);
      });
      state.samples = result.samples;
      state.catalogVersion = result.catalog_version;
      state.selected.clear();
      renderRuns();
      renderBulk();
      renderSamples();
      message(what + " " + changes.length + " run(s).", "success");
    } catch (error) {
      await failed(error, what);
    }
  }

  function assign() {
    let sample = $("expt-sample").value;
    if (sample === "__new__") sample = $("expt-new-sample").value.trim();
    if (!sample) {
      message("Choose a sample, or type a new sample id.", "warning");
      return;
    }
    const fields = { sample_id: sample };
    const type = $("expt-type").value.trim();
    const condition = $("expt-condition").value.trim();
    if (type) fields.measurement = type;
    if (condition) fields.condition = condition;
    editRuns(fields, "Assigned");
  }

  /* ---------------------------------------------------------------- */
  /* Quick look                                                         */
  /* ---------------------------------------------------------------- */

  let quick = null;
  let quickTimer = null;
  let quickRequest = 0;

  function scheduleQuickLook() {
    clearTimeout(quickTimer);
    quickTimer = setTimeout(quickLook, 300);
  }

  async function quickLook() {
    const runs = Array.from(state.selected).map(function (key) {
      return state.rows.get(key);
    }).filter(function (row) {
      return row && row.segments.length;
    }).slice(0, MAX_QUICK);
    const hint = $("quick-hint");
    if (!runs.length) {
      hint.textContent = "Select runs to plot them.";
      if (quick) quick.setCurves([]);
      return;
    }
    const ticket = ++quickRequest;
    hint.textContent = "reading " + runs.length + " run(s) from the data source…";
    const curves = [];
    const problems = [];
    for (const row of runs) {
      try {
        const result = await api("GET", "/api/experiment/runs/" + row.run + "/curves");
        result.curves.forEach(function (curve) {
          curves.push(curve);
        });
        result.problems.forEach(function (p) {
          problems.push(p.message);
        });
      } catch (error) {
        problems.push("run " + row.run + ": " + error.message);
      }
    }
    if (ticket !== quickRequest) return;
    hint.textContent = problems.length
      ? problems.join("; ")
      : state.selected.size > MAX_QUICK
        ? "showing the first " + MAX_QUICK + " selected runs"
        : "";
    if (typeof NRW === "undefined" || typeof Plotly === "undefined") return;
    if (!quick) quick = NRW.reflPanel("plot-quick", curves, { toggleId: "rq4-toggle" });
    else quick.setCurves(curves);
  }

  /* ---------------------------------------------------------------- */
  /* Samples                                                            */
  /* ---------------------------------------------------------------- */

  function renderSamples() {
    const list = $("expt-samples");
    list.replaceChildren();
    if (!state.samples.length) {
      list.append(el("p", {
        className: "p-3 mb-0 text-secondary",
        text: "No samples yet. Select runs and assign them to a new sample.",
      }));
    }
    state.samples.forEach(function (card) {
      const detail = card.managed
        ? card.runs.length + " run(s)" +
          (card.excluded.length ? ", " + card.excluded.length + " excluded" : "")
        : "on disk, not in the catalog";
      const item = el("button", {
        type: "button",
        className: "list-group-item list-group-item-action" +
          (state.editing === card.id ? " active" : ""),
      }, [
        el("div", { className: "d-flex gap-2" }, [
          el("span", { className: "mono", text: card.id }),
          el("span", { className: "text-truncate", text: card.title || "" }),
        ]),
        el("div", { className: "small opacity-75", text: detail }),
      ]);
      item.addEventListener("click", function () {
        openSample(card.id);
      });
      list.append(item);
    });
  }

  const FIELDS = {
    title: "f-title",
    description: "f-description",
    details: "f-details",
    mounting: "f-mounting",
    measurement_conditions: "f-conditions",
    fits_to_perform: "f-fits",
  };

  function card(id) {
    return state.samples.find(function (c) {
      return c.id === id;
    });
  }

  async function openSample(id) {
    state.editing = id;
    renderSamples();
    const current = card(id);
    $("expt-editor").classList.remove("d-none");
    $("expt-editor-title").textContent = "Sample " + id;
    Object.entries(FIELDS).forEach(function ([name, field]) {
      $(field).value = current && current.managed ? current[name] || "" : "";
      if (name === "mounting" && !$(field).value) $(field).value = "unknown";
      $(field).disabled = !state.writable || !(current && current.managed);
    });
    $("expt-save").disabled = !state.writable || !(current && current.managed);
    $("expt-form-status").textContent =
      current && !current.managed ? "Adopt this sample to edit it here." : "";
    $("expt-adopt-plan").classList.add("d-none");
    await refreshPreview(id);
  }

  async function refreshPreview(id) {
    const badge = $("expt-md-state");
    try {
      const preview = await api(
        "GET", "/api/experiment/samples/" + encodeURIComponent(id) + "/preview"
      );
      if (state.editing !== id) return;
      const [label, cls] = MD_STATE[preview.state] || [preview.state || "", "text-bg-light"];
      badge.className = "badge ms-auto " + cls;
      badge.textContent = "sample.md " + label;
      /* Rendered by the server with raw HTML disabled, as on the sample page. */
      $("expt-preview").innerHTML = preview.html || "";
      const adoptable = state.writable &&
        (preview.state === "drifted" || preview.state === "untracked" || !preview.managed);
      const button = $("expt-adopt");
      button.classList.toggle("d-none", !adoptable);
      button.textContent = preview.managed ? "Pull hand edits" : "Adopt";
    } catch (error) {
      badge.className = "badge ms-auto text-bg-light";
      badge.textContent = "";
      $("expt-preview").textContent = error.message;
    }
  }

  async function saveSample(event) {
    event.preventDefault();
    const id = state.editing;
    const current = card(id);
    if (!id || !current) return;
    const fields = {};
    Object.entries(FIELDS).forEach(function ([name, field]) {
      const value = $(field).value;
      if ((current[name] || "") !== value) fields[name] = value;
    });
    if (!Object.keys(fields).length) {
      $("expt-form-status").textContent = "Nothing changed.";
      return;
    }
    try {
      const result = await api(
        "PUT", "/api/experiment/samples/" + encodeURIComponent(id),
        { base_rev: current.rev || 0, fields: fields }
      );
      state.samples = result.samples;
      state.catalogVersion = result.catalog_version;
      renderSamples();
      $("expt-form-status").textContent = "Saved. Apply writes it into sample.md.";
      await refreshPreview(id);
    } catch (error) {
      if (error.status === 400) $("expt-form-status").textContent = error.message;
      else await failed(error, "Saving");
    }
  }

  async function showAdoptPlan() {
    const id = state.editing;
    const box = $("expt-adopt-plan");
    box.classList.remove("d-none");
    box.replaceChildren(el("p", { className: "text-secondary", text: "Reading sample.md…" }));
    let plan;
    try {
      plan = await api("GET", "/api/experiment/samples/" + encodeURIComponent(id) + "/adopt");
    } catch (error) {
      box.replaceChildren(el("p", { text: error.message }));
      return;
    }
    const items = [];
    plan.runs.forEach(function (run) {
      const described = Object.entries(run).filter(function ([k]) {
        return k !== "run";
      }).map(function ([k, v]) {
        return k + "=" + v;
      }).join(", ");
      items.push(el("li", { text: "run " + run.run + ": " + described }));
    });
    Object.keys(plan.context).forEach(function (name) {
      items.push(el("li", { text: "context: " + name.replace(/_/g, " ") }));
    });
    const leftovers = plan.leftovers.map(function (text) {
      return el("li", { className: "text-warning-emphasis", text: "not kept: " + text });
    });
    const problems = plan.problems.map(function (p) {
      return el("li", { className: "text-danger", text: p.message });
    });
    const keep = el("button", {
      type: "button", className: "btn btn-sm btn-primary", text: "Take into the catalog",
      disabled: plan.problems.length > 0,
    });
    const rewrite = el("button", {
      type: "button", className: "btn btn-sm btn-outline-primary",
      text: "…and rewrite sample.md", disabled: !plan.rewrite_ready,
      title: plan.rewrite_ready ? "sample.md is backed up first"
        : "sample.md has text the catalog cannot hold",
    });
    async function run(rewriteFile) {
      try {
        await api("POST", "/api/experiment/samples/" + encodeURIComponent(id) + "/adopt",
          { rewrite: rewriteFile });
        message("Taken into the catalog" + (rewriteFile ? "; sample.md rewritten." : "."),
          "success");
        await reload();
      } catch (error) {
        await failed(error, "Adopting");
      }
    }
    keep.addEventListener("click", function () {
      run(false);
    });
    rewrite.addEventListener("click", function () {
      run(true);
    });
    /* replaceChildren turns a null argument into the text "null", so the
     * optional paragraph is filtered out rather than passed as null. */
    box.replaceChildren(...[
      el("ul", { className: "mb-2" }, items.concat(leftovers, problems)),
      plan.undocumented.length ? el("p", {
        className: "text-secondary",
        text: "In the data but not in the table (not assigned): " +
          plan.undocumented.join(", "),
      }) : null,
      el("div", { className: "d-flex gap-2" }, [keep, rewrite]),
    ].filter(Boolean));
  }

  /* ---------------------------------------------------------------- */
  /* Apply                                                              */
  /* ---------------------------------------------------------------- */

  async function review() {
    const confirm = Array.from(state.confirmed).join(",");
    try {
      state.plan = await api(
        "GET", "/api/experiment/apply" + (confirm ? "?confirm=" + confirm : "")
      );
      renderPlan();
    } catch (error) {
      await failed(error, "Reviewing");
    }
  }

  function renderPlan(report) {
    const plan = state.plan;
    const box = $("expt-plan");
    box.replaceChildren();
    plan.problems.forEach(function (p) {
      box.append(el("p", { className: "text-danger", text: p.message }));
    });
    if (!plan.samples.length) {
      box.append(el("p", {
        className: "text-secondary mb-0", text: "The catalog assigns no runs to any sample yet.",
      }));
    }
    plan.samples.forEach(function (sample) {
      box.append(el("h3", { className: "h6 mt-2 mb-1" }, [
        el("span", { className: "mono", text: sample.sample }),
        sample.creates ? el("span", { className: "badge text-bg-info ms-2", text: "new" }) : null,
      ]));
      sample.problems.forEach(function (p) {
        box.append(el("p", { className: "text-danger mb-1", text: p.message }));
      });
      const list = el("ul", { className: "list-unstyled mb-1 expt-plan-list" });
      let unchanged = 0;
      const unconfirmed = new Set();
      sample.files.forEach(function (file) {
        if (file.action === "unchanged") {
          unchanged += 1;
          return;
        }
        const row = state.rows.get(file.run + ":steady");
        if (file.action === "deferred" && row && row.state === "unconfirmed") {
          unconfirmed.add(file.run);
        }
        const attention = !(file.action in SYMBOL);
        list.append(el("li", { className: attention ? "text-warning-emphasis" : "" }, [
          el("span", { className: "mono", text: (SYMBOL[file.action] || "!") + " " }),
          el("span", { text: file.action + " " }),
          el("span", { className: "mono", text: file.name }),
          file.detail ? el("span", { className: "text-secondary", text: " — " + file.detail }) : null,
        ]));
      });
      box.append(list);
      if (unchanged) {
        box.append(el("p", {
          className: "text-secondary mb-1",
          text: unchanged + " file(s) already copied and unchanged.",
        }));
      }
      unconfirmed.forEach(function (run) {
        const id = "confirm-" + run;
        const check = el("input", {
          type: "checkbox", className: "form-check-input", id: id,
          checked: state.confirmed.has(run), disabled: !state.writable,
        });
        check.addEventListener("change", function () {
          if (check.checked) state.confirmed.add(run);
          else state.confirmed.delete(run);
          review();
        });
        box.append(el("div", { className: "form-check" }, [
          check,
          el("label", {
            className: "form-check-label", htmlFor: id,
            text: "Use run " + run + " as it is: it settled, but nothing shows the measurement ended.",
          }),
        ]));
      });
      if (sample.sample_md) {
        box.append(el("p", {
          className: ["drifted", "untracked"].indexOf(sample.sample_md) !== -1
            ? "text-warning-emphasis mb-1" : "mb-1",
          text: "sample.md " + sample.sample_md_detail,
        }));
      }
    });
    if (report) {
      box.append(el("p", {
        className: "mt-2 mb-1", text: "Done: " + report.done.length + " file action(s).",
      }));
      report.failed.forEach(function (f) {
        box.append(el("p", { className: "text-danger mb-1", text: f.name + ": " + f.detail }));
      });
    }
    if (state.writable && plan.writes) {
      const button = el("button", {
        type: "button", className: "btn btn-sm btn-primary mt-2", text: "Apply",
      });
      button.addEventListener("click", applyPlan);
      box.append(button);
    } else if (!plan.writes && plan.samples.length) {
      box.append(el("p", { className: "text-secondary mt-2 mb-0", text: "Everything is in step." }));
    }
  }

  async function applyPlan() {
    try {
      const report = await api("POST", "/api/experiment/apply", {
        plan_id: state.plan.plan_id,
        confirmed: Array.from(state.confirmed),
      });
      state.confirmed.clear();
      await reload();
      state.plan = await api("GET", "/api/experiment/apply");
      renderPlan(report);
      message("Applied.", "success");
    } catch (error) {
      if (error.status === 409) {
        message(error.message, "warning");
        await review();
      } else {
        message("Applying: " + error.message, "danger");
      }
    }
  }

  /* ---------------------------------------------------------------- */
  /* Wiring                                                             */
  /* ---------------------------------------------------------------- */

  function renderAll() {
    renderHeader();
    renderProblems(state.payload.problems);
    renderRuns();
    renderBulk();
    renderSamples();
  }

  document.querySelectorAll("#expt-filters button").forEach(function (button) {
    button.addEventListener("click", function () {
      state.filter = button.dataset.filter;
      document.querySelectorAll("#expt-filters button").forEach(function (other) {
        other.classList.toggle("active", other === button);
      });
      renderRuns();
    });
  });
  $("expt-search").addEventListener("input", function (event) {
    state.search = event.target.value;
    renderRuns();
  });
  $("expt-select-all").addEventListener("change", function (event) {
    visibleRows().forEach(function (row) {
      if (event.target.checked) state.selected.add(row.key);
      else state.selected.delete(row.key);
    });
    renderRuns();
    renderBulk();
    scheduleQuickLook();
  });
  $("expt-sample").addEventListener("change", renderBulk);
  $("expt-assign").addEventListener("click", assign);
  $("expt-unassign").addEventListener("click", function () {
    editRuns({ sample_id: null }, "Unassigned");
  });
  $("expt-exclude").addEventListener("click", function () {
    editRuns({ include: false }, "Excluded");
  });
  $("expt-include").addEventListener("click", function () {
    editRuns({ include: true }, "Included");
  });
  $("expt-form").addEventListener("submit", saveSample);
  $("expt-adopt").addEventListener("click", showAdoptPlan);
  $("expt-review").addEventListener("click", review);

  load(initial);
  firstLook(initial.scan);
  setInterval(poll, POLL_MS);
})();
