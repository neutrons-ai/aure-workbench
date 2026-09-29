/* The Experiment page's Models panel: the specs of the sample open in the
 * editor, and a new one written from its data, as `nrw model new` writes it.
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
  let shown = null;

  function path(id) {
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
    $("expt-models").classList.remove("d-none");
    $("expt-models-title").textContent = "Models of " + payload.sample;
    const list = $("expt-models-list");
    list.replaceChildren();
    if (!payload.models.length) {
      list.append(el("p", { className: "text-secondary mb-1", text: "No models yet." }));
    }
    payload.models.forEach(function (model) {
      list.append(el("div", { className: "d-flex gap-2 align-items-baseline expt-model" }, [
        el("span", { className: "mono", text: model.name }),
        el("span", { className: "text-secondary", text: model.spec }),
        model.script
          ? el("span", { className: "badge text-bg-light", text: "script generated" })
          : null,
      ]));
    });
    const why = blocked(payload);
    if (why) list.append(el("p", { className: "text-secondary mb-0 mt-1", text: why }));
    $("expt-model-name").disabled = Boolean(why);
    $("expt-model-create").disabled = Boolean(why);
  }

  async function load(id) {
    try {
      const payload = await api("GET", path(id));
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
      const payload = await api("POST", path(id), { name: name });
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
})();
