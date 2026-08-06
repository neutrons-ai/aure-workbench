# Getting started: a full Cu/THF sequence, start to finish

This walks through a complete analysis on real REF_L data — two steady-state
measurements either side of an electrochemistry sequence, co-refined with the
time-resolved run measured *during* it.

| | Run | What it is |
|---|---|---|
| **ocv1** | 218386 | OCV before the EIS sequence, 3 angle segments |
| **tnr** | 218389 | 15 slices measured during the sequence, 0.6° |
| **ocv2** | 218393 | OCV after the sequence, 3 angle segments |

Every command and every number below was produced by running it. Timings are
from a laptop; the whole thing takes about five minutes, of which fifteen
seconds is the fit.

> **Prerequisite.** `pip install -e .` from the nr-workbench checkout. Run
> `nrw doctor` afterwards — it reports every dependency plus the resolved AuRE
> commit, which matters because AuRE's version metadata always says `0.1.0`.

---

## 1. Make the project

```bash
mkdir -p ~/beamtime && cd ~/beamtime
nrw init cu-thf-expt11 --beamtime jen-apr2025 --ipts IPTS-34347
cd cu-thf-expt11
```

```
Scaffolded .../cu-thf-expt11
  create   59 file(s)
```

`nrw init` is safe to run in a directory that already has files in it, and safe
to run twice — it never overwrites a file you have edited. Those files are the
layout, the editor config, and 15 reflectometry skills with dispatcher agents
for both Claude Code and Copilot, so an assistant opened in this folder already
knows REF_L conventions.

Check the environment:

```bash
nrw doctor
```

```
  ✓ refl1d        1.0.1
  ✓ bumps         1.0.4
  ✓ aure          0.1.0 @ 3021fee37294
  ✓ instrument    SNS REF_L
  ! samples       none yet; run `nrw sample new <ID>`
  ✓ skills        15 installed: analysis-provenance, neutron-reflectometry, ...
```

## 2. Make the sample and bring the data in

```bash
nrw sample new Sample6 --title "Cu/Pt in d8-THF, 1 M LiBF4 (expt 11)"
```

Copy the reduced ASCII in. Raw NeXus is gitignored; reduced data is small and
belongs in the repository with the analysis.

```bash
SRC=~/git/experiments-2025/jen-apr2025/data

cp $SRC/steady/REFL_218386_{1,2,3}_*_partial.txt samples/Sample6/data/steady/
cp $SRC/steady/REFL_218393_{1,2,3}_*_partial.txt samples/Sample6/data/steady/

mkdir -p samples/Sample6/data/tnr/218389
cp $SRC/tNR/reduced/r218389_*.txt            samples/Sample6/data/tnr/218389/
cp $SRC/tNR/reduced/r218389_eis_reduction.json samples/Sample6/data/tnr/218389/
```

**Copy the whole reduced directory, not just the slices you plan to fit.** The
sequence has 15 `eis` intervals and 101 short `hold` intervals. You will only
co-refine the `eis` ones, but the assessment coadds the leading holds into its
reference block, and 17 coadded holds are a much better baseline than 3 eis
slices: median `dR/R` drops from 0.050 to 0.035, and the peak significance of
the change rises from 26.5σ to 35.1σ. Leaving them out costs you resolution
you already paid beam time for.

> **Already have a beamtime directory?** `nrw import` reads the four layouts
> that exist in `experiments-2025` and maps them into this one. It **symlinks**
> data rather than copying it, so there is one copy of every byte and the
> original tree keeps working, and it writes nothing until you say `--write`:
>
> ```bash
> nrw import ~/git/experiments-2025/jen-oct2025            # plan only
> nrw import ~/git/experiments-2025/jen-oct2025 --write    # do it
> ```
>
> Outputs of previous fits are deliberately left behind — 310 files in
> apr2025, 527 in june2026. A `results/` directory here carries a manifest with
> input hashes, the environment and the exact command; a `.dat` from a 2025
> bumps run has none of that, and putting it where `nrw whence` promises an
> answer would fake provenance nobody recorded. Re-run the script through
> `nrw fit run` and you get the real thing in minutes.

Now register what actually arrived:

```bash
nrw sample scan Sample6
```

```
Sample6
  steady  218386  3 segment(s)
  steady  218393  3 segment(s)
  series  218389  15 slice(s) [labelled]
  ! data on disk for [218386, 218389, 218393], not mentioned in sample.md
  wrote samples/Sample6/sample.yaml
```

`sample.yaml` is the machine register — the scan maintains it. `sample.md` is
yours: what the sample is, what was done to it, what you expect. The tool never
edits it, and the `!` line is it telling you the two disagree. Write the runs up
in `sample.md` and the warning goes away.

**`sample.yaml` is also how you co-refine a subset.** It is a normal file, and
`nrw model new` builds from it rather than from the disk. A beamtime directory
routinely holds alignment scans, aborted runs and other conditions that belong
to the sample without belonging to *this* model — delete those entries from the
register and the model leaves them out:

```bash
nrw sample scan Sample6          # refresh the register from disk
$EDITOR samples/Sample6/sample.yaml   # keep only what this model is about
nrw model new Sample6 --name subset
```

```
  note  using samples/Sample6/sample.yaml, which does not list run(s) 218400
        that are on disk.
        If that is deliberate, nothing to do. If the register is stale, run
        `nrw sample scan Sample6`.
```

It reports the difference rather than resolving it: a stale register and a
curated one look identical on disk, and silently re-adding the run would undo
a deliberate choice.

## 3. Check the data before modelling it

This step exists because both of the things it finds are invisible on a log-R
plot, and a fit will happily absorb either one into a layer thickness.

```bash
nrw data check Sample6
```

```
  ! samples/Sample6/data/steady/REFL_218386_1_218386_partial.txt
      R > 1 detected (max=1.188): data may not be normalized
  ! samples/Sample6/data/steady/REFL_218386_3_218388_partial.txt
      Negative R values detected
  ...
  4 of 6 file(s) have issues.
```

Both are normal for per-angle REF_L data: `R > 1` on the low-angle segment is a
normalisation the fit is expected to absorb, and negative R at the top of the
high-angle segment is the noise floor. Worth seeing, not worth stopping for.

Then the one that matters:

```bash
nrw data overlap Sample6
```

```
  run 218386
    #1 / #2    +1.80%  ( 1.4 sigma)   chi2  1.51   n=47   Q 0.0282-0.0354
  ! #2 / #3   +27.58%  (14.8 sigma)   chi2  1.70   n=30   Q 0.0819-0.0946

  run 218393
    #1 / #2    -0.17%  ( 0.1 sigma)   chi2  1.67   n=47   Q 0.0282-0.0354
    #2 / #3    -4.06%  ( 2.4 sigma)   chi2  0.88   n=29   Q 0.0819-0.0941
```

Where two angle settings overlap in Q they are measuring the same sample, so
they have to agree. Run 218386's 3.5° segment is **27.6% off, at 14.8σ** —
that is a normalisation error, not the sample.

This is worth dwelling on, because it is the reason this check exists. The
hand-written script this analysis replaces has, buried at line 243:

```python
# The third run has a different intensity, so we don't share that
# parameter with the first two runs.
```

Someone found this by eye and worked around it. `nrw data overlap` turns it
into a number with an error bar, before a model is written rather than after
one misbehaves. Section 5 shows how the spec says so explicitly.

If the reduction template is beside the data, the check also prints which
direct beam normalised each segment:

```
      direct beams: 218386<-218274  218387<-218275  218388<-218338
```

Segments 1 and 2 were divided by adjacent direct-beam runs; segment 3 by one
measured much later. Different angles legitimately use different direct beams,
so that is where to look first rather than a diagnosis on its own — but it is
the obvious suspect.

One more check worth knowing, on a single curve:

```bash
nrw data features samples/Sample6/data/steady/REFL_218386_3_218388_partial.txt
```

```
    fringes counted: 12
      total thickness  406 +/- 577.5   (medium)   <- uncertainty exceeds the value; not a constraint
      roughness        10.43   (low)
      layer count      3   (low)
```

Note what it says about the thickness. The number alone looks like something
you could seed a model with; the uncertainty says it constrains nothing. Over a
single angle segment there is not enough Q range to do better, which is itself
worth knowing before you trust a fringe-counting estimate.

## 4. Ask what the time-resolved run actually did

Before choosing how to model the change, find out whether there *is* one, and
what kind.

```bash
nrw tnr assess samples/Sample6/data/tnr/218389 \
    --label r218389 --out samples/Sample6/assessments/r218389
```

```
Loaded 116 intervals from samples/Sample6/data/tnr/218389
  eis: 15  hold: 101
  time range: 0.0 - 4241.0 s
  reference:  leading contiguous 'hold' block: 17 intervals, t=0.0-508.0 s
  median dR_ref/R_ref: 0.0350

  1. variogram: rising, knee near 578 s
  2. amplitude: a in [-0.1263, 1.1294], max |a/sigma| = 35.079
     chi2_res median 1.0357 (one template suffices)
     trajectory: monotonic
  3. template: oscillatory -> thickness change, order 598 A
  4. chi2 over the reference block: 1.0136   (should be ~1)

  verdict: changing; a single reaction coordinate describes it; a(t) is
  monotonic, so a linear-in-time constraint fits; the template oscillates
  in Q, so free a thickness
```

Read it in that order, because each line qualifies the next:

1. **variogram** — is anything changing at all? It needs no reference block, so
   it cannot be fooled by a bad choice of one. Rising means yes.
2. **amplitude** — how much, and how significantly. `a` is defined as 0 at the
   reference block and 1 at the late block. `chi2_res ≈ 1` is the important
   number: it says a *single* Q-shape describes the whole change, so one
   reaction coordinate is enough and you are not averaging two processes.
3. **template** — what kind of change. Oscillatory in Q means a **thickness**
   change; a one-sign template would mean an **SLD contrast** change instead.
4. **chi2 over the reference block ≈ 1** — a sanity check on the error bars. If
   this were 5, the uncertainties in the reduced files would be wrong and every
   significance above would be inflated.

The verdict is deliberately phrased as model advice: *monotonic* → use
`linear_in_time`, *oscillatory* → free a thickness. That is the loop from
assessment to spec, closed.

## 5. Write the model

Scaffold from what the scan found:

```bash
nrw model new Sample6 --name cu-thf-218389
```

That writes a valid spec with a placeholder stack and — because it read the
same data — the two states, the series, and a `linear_in_time` constraint
already wired between them. Replace the stack with the real one.

**The angles come from the files, not from the usual settings.** Every reduced
REF_L file carries a `# Meta:` JSON header recording its incident angle in
radians, and `nrw model new` reads it: 0.4500, 1.2010, 3.5003 for run 218386
rather than the nominal 0.45/1.2/3.5. It matters because theta sets the
resolution through `dT = dq/q · tan(θ)`, so a wrong angle is absorbed into
roughness instead of raising.

Time-resolved slices carry no header at all — but the same run is *also*
reduced as a summed dataset into `data/steady`, and that file does. So the
series angle is read from there: **0.5997°** for run 218389, not the 0.6 anyone
would assume.

That summed dataset is deliberately left out of the fit. It is the sum of the
very slices the series contributes, so including both would put the same
neutrons in twice. `nrw model new` says so when it skips it.

### Filling in the stack

What `nrw model new` writes is *facts*: which runs exist, which files belong to
them, each segment's measured angle. What it cannot know is the **stack** —
what the sample is made of, in what order, roughly how thick. That is in your
head and, if you wrote it down, in `sample.md`.

Three ways to close that gap. All three produce the same file; pick whichever
matches what you have to hand.

**1. An LLM endpoint, if you have one configured.**

```bash
nrw model new Sample6 --name cu-thf-218389 --from-notes
```

It reads `sample.md`, picks the skills your notes call for, sends the
skeleton and the measured critical edge, and merges back a proposed stack:

```
  asking openai/gpt-4o-mini (5 skill(s))...
  model notes: Oxide thickness is a guess; sample.md gives no value.
Wrote samples/Sample6/models/cu-thf-218389.yaml
```

The written file says who proposed it:

```yaml
# The stack below was PROPOSED by openai/gpt-4o-mini
# from sample.md and the project's skills. It is a starting point, not a
# measurement -- check every layer and range before fitting. States,
# series and angles were read from the data files and were not proposed.
```

**A model may only propose `description`, `materials`, `stack`, `parameters`
and `constraints`.** Anything it returns for `states`, `series`, `thetas`,
`data_dir` or `run` is discarded — those were read from the files and are
already exact. That filter is in the code, not just in the prompt: a wrong
angle would broaden every fringe and the fit would quietly absorb it into
roughness.

Configure it in `.env` — `nrw init` ships a `.env.example` listing every
variable, and `.env` itself is gitignored, which matters because a beamtime
directory gets shared, archived and sometimes published.

```bash
cp .env.example .env      # then fill in one of the provider blocks
```

Settings are read from the shell first, then `.env`, then `~/.nrw`, then
`~/.aure` — so a machine already set up for AuRE works here with no extra
configuration. `nrw doctor` reports which of those it actually read, and shows
the key as a shape rather than a value:

```
  ✓ config        ~/.aure
  ✓ llm           local/gpt-5.4 @ https://.../openai/v1
  ✓ llm settings  LLM_API_KEY=set (32 chars, ...fa0b), LLM_MODEL=gpt-5.4, ...
```

**2. The coding assistant already open on the project.**

This is the common case in VS Code — Claude Code or Copilot can read every file
in the repo, so it needs an instruction rather than an endpoint:

```bash
nrw model new Sample6 --name cu-thf-218389 --print-prompt
```

That writes the skeleton and prints something you can paste straight in:

```
Fill in the model spec at samples/Sample6/models/cu-thf-218389.yaml for sample Sample6.

1. Read these skills first and follow them:
   - skills/reflectometry/nrw-model-spec/SKILL.md
   - skills/reflectometry/neutron-reflectometry/SKILL.md
   - skills/reflectometry/refl-bl4b-instrument/SKILL.md
   - skills/reflectometry/thin-layer-degeneracy/SKILL.md

2. Read samples/Sample6/sample.md for what the sample is and what was done to it.

3. Edit ONLY these parts of the spec:
     description, materials, stack, parameters

   Leave `states`, `series`, `thetas`, `data_dir`, `run` and `reduced_dir`
   exactly as they are. ...
```

It names the skills your notes actually call for, and tells you when a relevant
one is bundled but not installed:

```
  These bundled skills match your notes but are not installed here.
  Install them first so the assistant has them:
      nrw skills sync
      - metal-oxide-interfaces
      - solvent-contrast-matching
```

**3. By hand**, which is what the rest of this section shows. Worth doing once
even if you plan to use the other two, because reviewing a proposed stack is
much easier when you know what a good one looks like.

Whichever you use, the check is the same — and it is the reason none of this
is risky:

```bash
nrw model validate <spec>
nrw model preview  <spec> --build
```

A proposed stack that does not validate, or builds to a nonsense χ², is caught
in a second. Nothing is trusted because of where it came from.

The finished `samples/Sample6/models/cu-thf-218389.yaml`, in full:

```yaml
schema: nrw-model/1
name: cu-thf-218389
sample: Sample6

materials:
  THF:  {rho: 6.2}
  CuOx: {rho: 5.0, irho: 0.0}
  Cu:   {rho: 6.3}
  Ti:   {rho: -3.0}
  Si:   {rho: 2.07}

stack:                                      # ambient -> substrate
  - {name: THF,  material: THF,  thickness: 0,   roughness: 25}
  - {name: CuOx, material: CuOx, thickness: 60,  roughness: 20}
  - {name: Cu,   material: Cu,   thickness: 500, roughness: 5}
  - {name: Ti,   material: Ti,   thickness: 40,  roughness: 10}
  - {name: Si,   material: Si}

probe: {resolution: angular_only, dq_is_fwhm: true}

states:
  # Angles read from each file's `# Meta:` header -- not the nominal settings.
  - {name: ocv1, run: 218386, segments: auto, thetas: [0.45, 1.201, 3.5003],
     data_dir: samples/Sample6/data/steady}
  - {name: ocv2, run: 218393, segments: auto, thetas: [0.4499, 1.2009, 3.5002],
     data_dir: samples/Sample6/data/steady}

series:
  - name: tnr
    run: 218389
    reduced_dir: samples/Sample6/data/tnr/218389
    theta: 0.5997          # from run 218389's summed dataset in data/steady
    time_from: reduction_json
    select:
      labels: ["*_eis_*"]        # assess on all 116, co-refine the 15

parameters:
  # structure shared across the whole problem
  - {path: Cu.rho,   range: [5.0, 7.0],   per: model}
  - {path: CuOx.rho, range: [1.0, 6.5],   per: model}
  - {path: Ti.rho,   range: [-4.0, -1.0], per: model}
  - {path: THF.rho,  range: [5.5, 6.4],   per: model}

  # structure, one value per steady state
  - {path: Cu.thickness,   range: [400, 600], per: state, in: [ocv1, ocv2]}
  - {path: CuOx.thickness, range: [10, 120],  per: state, in: [ocv1, ocv2]}
  - {path: Ti.thickness,   range: [25, 60],   per: state, in: [ocv1, ocv2]}
  - {path: Cu.roughness,   range: [1, 30],    per: state, in: [ocv1, ocv2]}
  - {path: CuOx.roughness, range: [3, 33],    per: state, in: [ocv1, ocv2]}
  - {path: THF.roughness,  range: [5, 40],    per: state, in: [ocv1, ocv2]}

  # nuisance: one intensity per state, shared across its angle segments...
  - {path: probe.intensity, value: 1.0, pm: 0.1, per: state}
  # alignment error, from the notes. Range is AuRE's default.
  - {path: probe.theta_offset, range: [-0.02, 0.02], per: state, in: [ocv1, ocv2]}
  # ...except ocv1's 3.5 deg segment -- the 27.6% that `nrw data overlap`
  # found. Measurement-level targeting outranks the state-level line above.
  - {path: probe.intensity, range: [0.5, 1.1], per: measurement, in: [ocv1#2],
     name: Intensity_386_3}
  - {path: probe.theta_offset, range: [-0.01, 0.01], per: state, in: [tnr]}

constraints:
  - series: tnr
    form: linear_in_time
    from: ocv1
    to: ocv2
    paths: [Cu.thickness, CuOx.thickness, Ti.thickness,
            Cu.roughness, CuOx.roughness, THF.roughness]

fit: {method: amoeba, steps: 2000}
```

Four things in there are doing most of the work.

**Angle segments alias automatically.** `segments: auto` gives each state three
Experiments that share one set of structural parameters. You never write the
tying. The hand-written script this replaces is 364 lines, **52 of which** are
assignments of the form
`exp_list_ocv1[1].sample["THF"].interface = exp_list_ocv1[0].sample["THF"].interface`.
That is the single most common place to introduce a silent error: miss one and
two segments quietly fit independent values for a parameter that is physically
one number.

**`per:` is the whole parameter-identity system.** `per: model` is one value
for the entire problem, `per: state` is one per state, `per: measurement` is one
per slice. That is all there is to it.

**The 27.6% is declared, not absorbed.** `in: [ocv1#2]` targets one measurement
— the third segment of ocv1 — and gives it its own intensity with a wide range.
Measurement-level targeting outranks the state-level line above it. The
alternative is letting a copper thickness drift to compensate.

**The constraint adds no free parameters.** `linear_in_time` interpolates each
listed path from its ocv1 value to its ocv2 value using the real timestamps,
so the 15 tNR slices are described entirely by the two steady states. Time,
not index: the intervals are unequally spaced, so the two genuinely differ.

**Anchored endpoints, or fitted ones.** `from: ocv1, to: ocv2` is why the
constraint is free: it borrows parameters the steady-state data already
constrains. Write `free` for either endpoint to fit it instead:

```yaml
    from: ocv1        # anchored: the OCV measurement just before the run
    to: free          # fitted: where the sample actually finished
```

| | Cost | Use when |
|---|---|---|
| `ocv1` → `ocv2` | nothing | the states bracket the series and you trust them |
| `ocv1` → `free` | 1 per path | you know where it started; the end is the result |
| `free` → `free` | 2 per path | there is no bracketing state at all |

On this model that is 21 free parameters anchored, 27 with a fitted end, 33
with both. The steady states are longer counts over a wider Q range than any
single slice, so anchoring imports a much better constraint and is the right
default. Free an endpoint when there is no bracketing measurement, when
something happened between the steady measurement and the run, or when where
the sample finished is the number you are after rather than an assumption.

A free endpoint borrows its range from the path's `parameters` declaration — a
Cu thickness plausible for the steady states is plausible here too. Add
`endpoint_range: [min, max]` when there is no such declaration.

Check it before generating anything:

```bash
nrw model validate samples/Sample6/models/cu-thf-218389.yaml
nrw model preview  samples/Sample6/models/cu-thf-218389.yaml --build
```

```
  21 experiment(s) in 3 group(s)
    ocv1           3 measurement(s)
    ocv2           3 measurement(s)
    tnr           15 measurement(s), t = 507.962..4241.03 s

  21 free parameter(s), 90 constrained
    ...
    probe.intensity@ocv1#2           1   [0.5, 1.1]
    probe.theta_offset@tnr           0   [-0.01, 0.01]

  built ok: chisq 101.632, 5380 data point(s)
```

`--build` constructs the real refl1d problem in-process and reports the starting
χ². **21 free parameters describing 21 experiments and 5380 points** — that
ratio is the point of the whole exercise.

## 6. Generate and fit

```bash
nrw model generate samples/Sample6/models/cu-thf-218389.yaml
```

```
Wrote samples/Sample6/models/cu-thf-218389.py
      samples/Sample6/models/cu-thf-218389.md  (what it assumes)
```

Two files. The `.md` explains the model in English — what is being fitted, what
is held equal to what, what each instrument parameter absorbs, and every
assumption the fit makes without saying so:

> **`probe.sample_broadening`** — extra angular divergence beyond the
> calculated resolution, from sample curvature or mosaic. It damps the fringes,
> so leaving it fixed when it is real makes every interface look rougher than
> it is.
>
> **Layers below the resolution limit.** `CuOx` (60 Å). Reflectivity constrains
> a thin layer mainly through the product of contrast and thickness, so its SLD
> and thickness are individually poorly determined even when their product is not.

It is **derived from the spec, not written**, so it cannot describe a different
model than the one that will run — and `nrw check` reports it stale if the spec
moves on without it. That is the file to hand a collaborator, or to read
yourself in six months.

The `.py` is a standalone 778-line refl1d script. It runs under plain
refl1d/bumps with nr-workbench uninstalled, uses no absolute paths, and is
meant to be committed and read. It ends with runtime assertions that every
parameter which should be shared actually *is* the same object — the aliasing
mistake made structurally impossible rather than warned about.

```bash
nrw fit run samples/Sample6/models/cu-thf-218389.py \
    --method amoeba --steps 2000 --seed 1 \
    --note "first co-refinement of the full sequence"
```

```
  fit_id   20260805-191540Z-732f4286
  chisq    1.83
  free     21 parameter(s)
  output   samples/Sample6/results/20260805-191540Z-732f4286
```

**χ² 101.6 → 1.83, in fifteen seconds.**

For a production run use `--method dream --samples 100000 --burn 10000`, which
also gives you parameter uncertainties. Amoeba finds a minimum; it does not
tell you how wide it is.

## 7. Look at it

```bash
nrw serve
```

```
  cu-thf-expt11  ~/beamtime/cu-thf-expt11
  1 sample(s), 1 fit(s)

  http://127.0.0.1:8765/
  http://127.0.0.1:8765/api/overview   the same data as JSON
```

`/s/Sample6` is the sample landscape: every steady-state segment as R(Q), the
tNR run as a time–Q map of fractional change, and a(t) beneath it on the **same
time axis**. Click or drag on either lower panel to move the time cursor and
that slice's R(Q) overlays onto the top panel, so you can see what the sample
looked like at the moment the change was happening.

`/f/<fit_id>` is the fit: data and model per experiment with a residual strip,
all 21 SLD profiles, the parameter table, and a provenance panel.

Every page has a JSON twin under `/api/…`, which is what an assistant should
read rather than scraping HTML.

## 8. Make the result citable

```bash
nrw promote 20260805-191540Z-732f4286 --as final \
    --reason "chi2 1.83; segment normalisation handled explicitly"
nrw check
```

```
Checked 1 fit(s).
  no problems found
```

The result directory is immutable. It holds a frozen byte-identical copy of the
spec and the script, the sha256 of all 22 input files, the resolved versions of
python/refl1d/bumps/numpy *and the AuRE commit*, the git SHA, and the exact
command line. `NOTES.md` is the only file in there you should edit.

Ask any artifact where it came from:

```bash
nrw whence samples/Sample6/results/20260805-191540Z-732f4286/fit/cu-thf-218389.par
```

```
  -> fit 20260805-191540Z-732f4286
  sample     Sample6
  model      cu-thf-218389  (script)
  script     model.py  sha256 dbb85fa736a2
  fit        amoeba, 2000 steps, seed 1
  chisq      1.83   free 21
  versions   python 3.14.5rc1, aure 0.1.0, bumps 1.0.4, ... refl1d 1.0.1
  aure       3021fee37294
  inputs     22 file(s), fresh
```

It works on more than fit outputs. Ask it about a *data* file and it lists every
fit that consumed it; ask about a figure you saved into the fit's `figures/`
directory and it identifies the fit even if that figure has since been copied
out of the project and emailed, because figures placed there are stamped with
their `fit_id` at write time (PNG `tEXt`, SVG `<desc>`).

`nrw check` is the guard: it verifies every input hash still matches, that no
generated script has been hand-edited, that none is stale against its spec, and
that every promoted pointer resolves. Run it before you call anything final, and
in CI.

---

## What to do next

**Try a different model.** Copy the spec, change it, fit it, and compare:

```bash
nrw diff <fit_a> <fit_b>
```

It tells you whether a χ² difference came from the model, the data, or just the
optimizer settings — three things that look identical in a table of numbers.

**If you need to hand-edit the script**, do it properly:

```bash
nrw model fork samples/Sample6/models/cu-thf-218389.py
```

That hands you ownership of the file and keeps full provenance. Editing a
generated script in place is the one thing that breaks the chain, and `nrw
check` will tell you off for it. Running an ordinary hand-written script through
`nrw fit run` is supported and always will be — provenance does not require the
generator.

Once you own a script, the `refl1d-script-review` skill is worth reading. Its
headline is the aliasing pitfall: co-refinement works by making Experiments
share one `Parameter` *object*, and reassigning one of them afterwards silently
unties it. The fit still converges, χ² even improves, and the answer is wrong.
Generated scripts end with identity assertions that make this impossible; a
fork keeps them, and a hand-written script should gain them.

**Ask the assistant.** With this project open in Claude Code or Copilot, the
skills installed by `nrw init` are already loaded. `docs/ground_truths.md` is
where you and it should record anything non-obvious you learn — it is the
project's memory between sessions.
