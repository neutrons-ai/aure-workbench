/* The Settings page's Language model: which one New model and AuRE use.
 *
 * Saved in the project's .env, not nrw.toml: settings.js and this file share
 * nothing but the page. The server says what applies and where each part comes
 * from; this page shows that, sends back the choice with the revision of .env
 * it was shown, and never sees a key. Every value is set as text.
 */
"use strict";

(function () {
  const N = window.NRWPage;
  const $ = N.$;
  const el = N.el;
  const TOKEN = window.NRW_WRITE_TOKEN || "";
  const api = N.client(TOKEN);
  const PATH = "/api/experiment/settings/llm";
  let shown = null;

  function writable() {
    return Boolean(shown && shown.writable && TOKEN);
  }

  /* "claude_code, the CLI's default model, from .env" -- what applies, said. */
  function describe(part) {
    if (!part.provider) {
      return part.key
        ? "no provider is named; a key is set, so AuRE guesses one from it"
        : "nothing is set up";
    }
    const claude = part.provider === "claude_code";
    let text = claude ? "Claude, through the Claude Code CLI" : part.provider;
    if (part.model) text += ", model " + part.model;
    else if (claude) text += ", the CLI's default model";
    text += " (from " + part.provider_from;
    if (part.model_from && part.model_from !== part.provider_from) {
      text += "; the model from " + part.model_from;
    }
    return text + ")";
  }

  /* Why Claude could not answer a job from here, or "" when it could. Said,
   * not enforced: Claude Code may be installed after the choice is saved. */
  function claudeProblem() {
    if (!shown.aure) return "AuRE is not installed where nrw serve runs.";
    if (!shown.claude_code.supported) {
      return "The installed AuRE predates this provider: update it (pip install -e .).";
    }
    if (!shown.claude_code.cli) {
      return "claude is not on nrw serve's PATH. Install Claude Code, or set " +
        "AURE_CLAUDE_BIN to the binary.";
    }
    return "";
  }

  function render() {
    $("llm-now").textContent =
      "New model and quick fits use: " + describe(shown.effective) + ".";
    const environment = $("llm-environment");
    environment.textContent = shown.environment.length
      ? "nrw serve was started with " + shown.environment.join(" and ") +
        " set in its environment, which wins over this project's .env: a " +
        "choice saved here reaches no job until nrw serve is started without it."
      : "";
    environment.classList.toggle("d-none", !shown.environment.length);

    const problem = claudeProblem();
    $("llm-claude-problem").textContent = problem;
    $("llm-claude-problem").classList.toggle("d-none", !problem);

    $("llm-outside-detail").textContent = describe(shown.outside) + ".";
    const other = shown.choice === "other";
    $("llm-other-row").classList.toggle("d-none", !other);
    $("llm-other-detail").textContent = other
      ? "LLM_PROVIDER=" + shown.project.LLM_PROVIDER + ", written by hand. " +
        "Choosing another replaces it."
      : "";

    $("llm-claude").checked = shown.choice === "claude_code";
    $("llm-other").checked = other;
    $("llm-outside").checked = shown.choice === "outside";
    $("llm-model").value = shown.choice === "claude_code" ? shown.project.LLM_MODEL || "" : "";

    const off = !writable();
    ["llm-claude", "llm-outside", "llm-model", "llm-save", "llm-check"].forEach(function (id) {
      $(id).disabled = off;
    });
    // Offered only while nothing is chosen for it: it is what .env says now.
    $("llm-other").disabled = true;
    if (off) {
      $("llm-status").textContent = shown.read_only_reason ||
        "View only: open the link nrw serve printed to change this.";
    }
  }

  async function load() {
    try {
      shown = await api("GET", PATH);
      render();
    } catch (error) {
      $("llm-now").textContent = "What is set could not be read: " + error.message;
    }
  }

  $("llm-model").addEventListener("input", function () {
    $("llm-claude").checked = true;
  });

  $("llm-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!shown) return;
    const choice = document.querySelector('input[name="llm-choice"]:checked');
    const status = $("llm-status");
    if (!choice || choice.value === "other") {
      status.textContent = "Nothing has changed.";
      return;
    }
    const body = { revision: shown.revision, provider: null };
    if (choice.value === "claude_code") {
      body.provider = "claude_code";
      body.model = $("llm-model").value.trim();
    }
    $("llm-save").disabled = true;
    status.textContent = "Saving…";
    try {
      const saved = await api("PUT", PATH, body);
      shown = saved.llm;
      render();
      status.textContent = saved.changes.length
        ? "Saved in .env: " + saved.changes.join("; ") + "."
        : "Nothing needed changing.";
    } catch (error) {
      status.textContent = error.message;
      if ((error.payload || {}).kind === "EnvFileConflict") await load();
    } finally {
      $("llm-save").disabled = !writable();
    }
  });

  $("llm-check").addEventListener("click", async function () {
    const status = $("llm-status");
    const result = $("llm-result");
    result.replaceChildren();
    status.textContent = "Asking the model saved here…";
    $("llm-check").disabled = true;
    try {
      const probe = (await api("POST", PATH + "/check", {})).probe;
      status.textContent = "";
      const kinds = { ok: "text-success", warn: "text-warning-emphasis" };
      const parts = [el("div", {
        className: (kinds[probe.status] || "text-danger") + " llm-probe",
        text: (probe.status === "ok" ? "It answered" : probe.detail) +
          (probe.seconds ? ", in " + probe.seconds + " s" : "") + ".",
      })];
      if (probe.model) parts.push(el("div", { className: "mono", text: probe.model }));
      if (probe.reply && probe.status !== "ok") {
        parts.push(el("div", { className: "text-secondary", text: "It said: " + probe.reply }));
      }
      result.replaceChildren(...parts);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      $("llm-check").disabled = !writable();
    }
  });

  load();
})();
