// Refuse the commands an unattended agent must not run: OpenCode 2's half.
//
// Written by `nrw init`, beside nrw-guard.js, which is the same guard for
// OpenCode 1. The two cannot share a file: OpenCode 1 calls every export of a
// plugin file as a plugin function, and OpenCode 2 loads only a default export
// that is a plain object with an id and a setup. Each version loads its own
// file and warns about the other one in its log; that warning is expected.
//
// Measured on OpenCode 2.0.22, not read off a docs page (the published plugin
// docs still describe the OpenCode 1 form):
//
// * nrw-guard.js is refused at load -- "Plugin must export a default
//   definition with an id and an effect or setup function" -- and only a line
//   in OpenCode's log says so. Without this file nothing is refused.
// * The tool that runs a command is `shell`, no longer `bash`, and its input
//   is still `{ command }`.
// * A hook on the tool's "execute.before" that throws refuses the call before
//   the command runs, and the model is shown the message.
//
// **No refusal logic lives here.** Every decision is `nrw agent guard`, for the
// reasons nrw-guard.js gives.

import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

/** Exit code `nrw agent guard` uses to block a command. */
const BLOCK = 2

/** The tools that run a shell command: `shell` in OpenCode 2, `bash` before. */
const SHELL_TOOLS = new Set(["shell", "bash"])

/** Locate `nrw`: the project-local shim first, as nrw-guard.js explains. */
function nrwCommand(directory) {
  const shim = join(directory, ".nrw", "bin", "nrw")
  return existsSync(shim) ? shim : "nrw"
}

export default {
  id: "nrw-guard",
  setup: async (ctx) => {
    const directory = ctx.location?.directory ?? process.cwd()

    await ctx.tool.hook("execute.before", (event) => {
      if (!SHELL_TOOLS.has(event.tool)) return

      const command = event.input?.command
      if (typeof command !== "string" || command.trim() === "") return

      const verdict = spawnSync(
        nrwCommand(directory),
        ["agent", "guard", "--command", command],
        { encoding: "utf-8" },
      )

      // A guard that cannot run must not block everything: see nrw-guard.js.
      if (verdict.error || verdict.status === null) return

      if (verdict.status === BLOCK) {
        throw new Error((verdict.stderr || "Refused by nr-workbench.").trim())
      }
    })
  },
}
