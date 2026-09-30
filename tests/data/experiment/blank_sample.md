# Sample1

## Description

<!-- One or two sentences: what the sample is, what it is in, what you expect.
     e.g. "~30 nm ionomer deposited on 20 nm copper with a Cr adhesion layer in D2O" -->

## Details

<!-- Composition, electrolyte, environment, anything that constrains the model.
     e.g. "20 nm Cu, ionomer (piperION), 0.1 M KHCO3, sparged with CO2, D2O, pH 6.8" -->

## Measurements

<!-- One line per run. Keep the run number first so `nrw sample scan` can match
     it against the files on disk.

| Run    | Type   | Condition            |
|--------|--------|----------------------|
| 230594 | full Q | air                  |
| 230597 | full Q | OCV                  |
| 230600 | tNR    | -0.5 mA/cm2          |
-->

| Run | Type | Condition |
|-----|------|-----------|
|     |      |           |

## Measurement conditions

<!-- Anything about the *measurement* rather than the sample. These become
     nuisance parameters in the model, and each one left out pushes its error
     into a layer thickness or a roughness instead.

     Write it in plain words -- `nrw model new --from-notes` and the coding
     assistant both read this section and add the right parameter.

     - alignment: was the sample well aligned? A doubt here becomes
       `probe.theta_offset`, which shifts the whole curve in Q.
     - flatness: is the substrate curved, bent, or clamped under strain? That
       becomes `probe.sample_broadening`, which damps the fringes. Left out,
       every interface comes back rougher than it is.
     - **was the sample moved between measurements?** This decides the scope
       of the two above. Mounted once and measured throughout -- an in-situ
       cell, OCV / tNR / OCV -- means one alignment for the whole experiment
       (`per: model`). Remounted or realigned between measurements means one
       per state. Say which; it is not guessable from the data.
     - background: anything unusual about the high-Q statistics becomes
       `probe.background`.
     - normalisation: did any angle segment need a different direct beam?
       `nrw data overlap` measures this; note what you already know.

     e.g. "Mounted once and left in the cell for the whole sequence, so one
           alignment throughout."
          "Sample bowed slightly after mounting -- expect some broadening."
          "Realigned between 218386 and 218393, so the two differ in theta."
          "0.45 deg segment looks 20% high against the 1.2 deg one." -->

## Fits to perform

<!-- What you want out of this sample, in words. The agent turns these into
     model specs under models/. Say which measurements co-refine together and
     what you expect to change between them.

     For a time-resolved run, say HOW you expect it to change -- that becomes
     the constraint form. `nrw model forms` lists them; the usual answers:

       "changes steadily between the two OCV states"   -> linear_in_time
       "slow start, then a transition, then levels off" -> logistic
       "relaxes towards equilibrium"                    -> exponential
       "no assumption, fit every slice"                 -> free

     And say WHICH quantity you expect to change -- a thickness, an SLD, a
     roughness. That becomes the constraint's `paths`.

     You do not have to guess: `nrw tnr assess` reads it off the data and says
     so in its verdict, e.g. "a(t) is monotonic, so a linear-in-time constraint
     fits; the template oscillates in Q, so free a thickness". Run that first
     and the answer is already written down.

     e.g. "Co-refine 218386 and 218393 with the 218389 series. The oxide should
           thicken steadily through the EIS sequence -- linear in time on
           CuOx.thickness." -->
