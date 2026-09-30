/* The Experiment page's models and fits.
 *
 * The Models panel shows the specs of the sample open in the editor, writes a
 * new one from its data (as `nrw model new` does), and starts a fit of one.
 * The Fit panel follows the job: what it prints, Cancel, and the fit it
 * recorded. One job runs at a time, for the whole project, so the Fit panel
 * shows it whichever sample is open.
 *
 * experiment.js says which sample is open with an "nrw:sample-opened" event;
 * the two files share nothing else. Every value is set as text.
 */
"use strict";

(function () {
  const page = window.NRWPage;
  const $ = page.$;
  const el = page.el;
  const TOKEN = window.NRW_WRITE_TOKEN || "";
  const api = page.client(TOKEN);
  const POLL_MS = 1000;
  // The most of a job's output kept on the page; the whole of it is in
  // .nrw/jobs/<id>.log.
  const LOG_KEEP = 400000;

  let shown = null;  // the sample whose models are shown
  let fitting = null;  // the model the fit form is for
  // The project's nrw.toml [fit], as the last listing gave it.
  let fitDefaults = { method: "dream", settings: {}, problem: null };
  // offset null: not read yet, so the first read is the log's last part.
  const job = { id: null, offset: null, running: false, timer: null, ended: null };

  function modelsPath(id) {
    return "/api/experiment/samples/" + encodeURIComponent(id) + "/models";
  }

  /* Why a new model cannot be written yet, or "" when it can. */
  function blocked(payload) {
    if (!TOKEN || !payload.writable) return "This page is view-only.";
    if (!payload.exists) {
      return "Apply first: it creates samples/" + payload.sample +
        "/ and copies in the data a spec is built from.";
    }
    if (!payload.has_data) {
      return "samples/" + payload.sample + "/data/ holds no data yet: apply the " +
        "runs assigned to this sample first.";
    }
    return "";
  }

  function render(payload) {
    if (payload.fit) fitDefaults = payload.fit;
    $("expt-models").classList.remove("d-none");
    $("expt-models-title").textContent = "Models of " + payload.sample;
    const list = $("expt-models-list");
    list.replaceChildren();
    if (!payload.models.length) {
      list.append(el("p", { className: "text-secondary mb-1", text: "No models yet." }));
    }
    const canFit = Boolean(TOKEN && payload.writable);
    payload.models.forEach(function (model) {
      const fit = el("button", {
        type: "button",
        className: "btn btn-sm btn-outline-primary py-0 ms-auto expt-model-fit",
        text: "Fit…",
        "aria-label": "fit " + model.name,
        disabled: !canFit,
      });
      fit.addEventListener("click", function () {
        openFit(model.name);
      });
      list.append(el("div", { className: "d-flex gap-2 align-items-baseline mb-1 expt-model" }, [
        el("span", { className: "mono", text: model.name }),
        el("span", { className: "text-secondary", text: model.spec }),
        model.script
          ? el("span", { className: "badge text-bg-light", text: "script generated" })
          : null,
        fit,
      ]));
    });
    const why = blocked(payload);
    if (why) list.append(el("p", { className: "text-secondary mb-0 mt-1", text: why }));
    $("expt-model-name").disabled = Boolean(why);
    $("expt-model-create").disabled = Boolean(why);
    // AuRE fits one measurement: which, when there is a choice.
    const runs = $("expt-model-run");
    const chosen = runs.value;
    runs.replaceChildren();
    (payload.runs || []).forEach(function (run) {
      runs.append(el("option", { value: String(run), text: "run " + run }));
    });
    if (chosen && (payload.runs || []).map(String).includes(chosen)) runs.value = chosen;
    runs.classList.toggle("d-none", (payload.runs || []).length < 2);
    const quick = $("expt-model-quick");
    quick.disabled = Boolean(why) || !payload.aure;
    quick.title = why || (payload.aure ? "" : "AuRE is not installed where nrw serve runs.");
  }

  async function load(id) {
    try {
      const payload = await api("GET", modelsPath(id));
      if (shown === id) render(payload);
    } catch (error) {
      if (shown === id) $("expt-model-status").textContent = error.message;
    }
  }

  document.addEventListener("nrw:sample-opened", function (event) {
    const id = event.detail.id;
    if (id !== shown) {
      // What was said about another sample is not about this one.
      $("expt-model-status").textContent = "";
      $("expt-model-output").classList.add("d-none");
      $("expt-fit-form").classList.add("d-none");
      fitting = null;
      shown = id;
    }
    load(id);
  });

  $("expt-model-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    const id = shown;
    const name = $("expt-model-name").value.trim();
    if (!id) return;
    if (!name) {
      $("expt-model-status").textContent = "Give the model a name.";
      return;
    }
    $("expt-model-create").disabled = true;
    $("expt-model-status").textContent = "Writing " + name + ".yaml from the data…";
    try {
      const payload = await api("POST", modelsPath(id), { name: name });
      if (shown !== id) return;
      render(payload);
      $("expt-model-name").value = "";
      $("expt-model-status").textContent =
        "Wrote samples/" + id + "/models/" + name + ".yaml.";
      const out = $("expt-model-output");
      out.textContent = payload.output || "";
      out.classList.toggle("d-none", !payload.output);
    } catch (error) {
      $("expt-model-status").textContent = error.message;
      $("expt-model-create").disabled = false;
    }
  });

  $("expt-model-quick").addEventListener("click", async function () {
    const id = shown;
    const name = $("expt-model-name").value.trim();
    if (!id) return;
    if (!name) {
      $("expt-model-status").textContent = "Give the model a name.";
      return;
    }
    const body = { name: name };
    const runs = $("expt-model-run");
    if (!runs.classList.contains("d-none") && runs.value) body.run = Number(runs.value);
    $("expt-model-quick").disabled = true;
    try {
      const started = await api("POST", modelsPath(id) + "/quick-fit", body);
      $("expt-model-name").value = "";
      $("expt-model-status").textContent =
        "AuRE is proposing a stack for " + name + "; the Fit panel follows it.";
      follow(started.job);
    } catch (error) {
      $("expt-model-status").textContent = error.message;
    } finally {
      load(id);  // the buttons as the sample now stands
    }
  });

  // -- starting a fit --------------------------------------------------------

  function openFit(name) {
    fitting = name;
    $("expt-fit-title").textContent = "Fit " + name;
    // The project's default fitter, said so beside its name.
    const select = $("expt-fit-method");
    Array.from(select.options).forEach(function (option) {
      if (!option.dataset.label) option.dataset.label = option.textContent;
      option.textContent = option.dataset.label +
        (option.value === fitDefaults.method ? " (the project's default)" : "");
    });
    select.value = fitDefaults.method;
    const problem = $("expt-fit-problem");
    problem.textContent = fitDefaults.problem ? "nrw.toml: " + fitDefaults.problem : "";
    problem.classList.toggle("d-none", !fitDefaults.problem);
    showMethodSettings();
    $("expt-fit-form").classList.remove("d-none");
    select.focus();
  }

  /* The boxes the chosen fitter takes, each empty box showing what nrw.toml
   * would give it. */
  function showMethodSettings() {
    const method = $("expt-fit-method").value;
    const dream = method === "dream";
    $("expt-fit-samples").classList.toggle("d-none", !dream);
    $("expt-fit-burn").classList.toggle("d-none", !dream);
    const configured = (fitDefaults.settings || {})[method] || {};
    [["expt-fit-steps", "steps"], ["expt-fit-samples", "samples"], ["expt-fit-burn", "burn"]]
      .forEach(function ([id, key]) {
        $(id).placeholder = key in configured
          ? key + " (nrw.toml: " + configured[key] + ")"
          : key;
      });
  }

  $("expt-fit-method").addEventListener("change", showMethodSettings);
  $("expt-fit-close").addEventListener("click", function () {
    $("expt-fit-form").classList.add("d-none");
    fitting = null;
  });

  /* A number box's value, or undefined when it is left empty. */
  function whole(id) {
    const text = $(id).value.trim();
    return text === "" ? undefined : Number(text);
  }

  $("expt-fit-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!shown || !fitting) return;
    const method = $("expt-fit-method").value;
    const body = { method: method, steps: whole("expt-fit-steps") };
    if (method === "dream") {
      body.samples = whole("expt-fit-samples");
      body.burn = whole("expt-fit-burn");
    }
    const note = $("expt-fit-note").value.trim();
    if (note) body.note = note;
    if ($("expt-fit-force").checked) body.force = true;
    $("expt-fit-run").disabled = true;
    try {
      const started = await api(
        "POST", modelsPath(shown) + "/" + encodeURIComponent(fitting) + "/fit", body
      );
      $("expt-fit-form").classList.add("d-none");
      $("expt-fit-note").value = "";
      $("expt-fit-force").checked = false;
      follow(started.job);
    } catch (error) {
      $("expt-model-status").textContent = error.message;
    } finally {
      $("expt-fit-run").disabled = false;
    }
  });

  // -- following the job -----------------------------------------------------

  const ENDINGS = {
    ok: "Finished.",
    failed: "Failed: the last lines of its output say why.",
    cancelled: "Cancelled. What the fit had written is left as it was; `nrw check` " +
      "lists it as an interrupted run.",
    detached: "nrw serve was stopped while this ran, so how it ended is not " +
      "known here. A fit that finished is in Fits.",
  };

  function follow(current) {
    if (!current) return;
    if (current.id !== job.id) {
      job.id = current.id;
      job.offset = null;
      job.ended = null;
      $("expt-job-log").textContent = "";
      $("expt-job-result").replaceChildren();
    }
    $("expt-job-panel").classList.remove("d-none");
    showJob(current);
    schedule(0);
  }

  function showJob(current) {
    job.running = current.status === "running";
    $("expt-job-title").textContent =
      current.label.charAt(0).toUpperCase() + current.label.slice(1) +
      " (" + current.sample + ")";
    $("expt-job-state").textContent = job.running
      ? "step " + current.step + " of " + current.steps.length
      : current.status;
    $("expt-job-cancel").classList.toggle("d-none", !job.running || !TOKEN);
    if (job.running || job.ended === current.status) return;
    job.ended = current.status;
    const result = $("expt-job-result");
    result.replaceChildren(ENDINGS[current.status] || current.status);
    if (current.fit_id) {
      result.append(" Recorded as ", el("a", {
        href: "/f/" + encodeURIComponent(current.fit_id),
        className: "mono",
        text: current.fit_id,
      }), ".");
    }
    if (shown === current.sample) load(shown);  // its script is generated now
  }

  function append(text) {
    if (!text) return;
    const log = $("expt-job-log");
    const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
    let all = log.textContent + text;
    if (all.length > LOG_KEEP) all = all.slice(all.length - LOG_KEEP);
    log.textContent = all;
    if (atEnd) log.scrollTop = log.scrollHeight;
  }

  function schedule(delay) {
    clearTimeout(job.timer);
    job.timer = setTimeout(poll, delay);
  }

  async function poll() {
    try {
      const payload = await api(
        "GET",
        "/api/experiment/jobs/current" + (job.offset === null ? "" : "?offset=" + job.offset)
      );
      if (!payload.job || payload.job.id !== job.id) {
        follow(payload.job);
        return;
      }
      append(payload.log);
      job.offset = payload.offset;
      showJob(payload.job);
      // On while it runs, and until an ended job's output has all been read.
      if (job.running || payload.log) schedule(payload.log ? 0 : POLL_MS);
    } catch (error) {
      $("expt-job-result").textContent = "Following the job: " + error.message;
      schedule(POLL_MS * 5);
    }
  }

  $("expt-job-cancel").addEventListener("click", async function () {
    if (!job.id) return;
    $("expt-job-cancel").disabled = true;
    try {
      await api("POST", "/api/experiment/jobs/" + encodeURIComponent(job.id) + "/cancel", {});
      schedule(0);
    } catch (error) {
      $("expt-job-result").textContent = error.message;
    } finally {
      $("expt-job-cancel").disabled = false;
    }
  });

  // A job started before this page was opened -- or before nrw serve was.
  api("GET", "/api/experiment/jobs/current").then(function (payload) {
    if (payload.job) follow(payload.job);
  }).catch(function () {});
})();
