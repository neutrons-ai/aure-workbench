# Provenance

Run any refl1d script -- including one you wrote by hand years ago -- and it
comes out with a complete, queryable record:

```bash
nrw fit run samples/Cu/models/cu-d2o.py --method dream --samples 5000
nrw ls                        # every fit, newest first, with freshness
nrw whence figures/fig3.svg   # what produced this?
nrw promote <fit_id> --as final --reason "converged; SLD band excludes null"
nrw check                     # CI-able: fails if a result went stale
nrw pack <fit_id>             # a zip a collaborator runs with only refl1d
```

Each fit writes an immutable directory holding the frozen script, every input
file with its sha256, the exact package versions and git state (with a patch if
the tree was dirty), and the bumps output. `nrw whence` traces a figure back to
that record even after it has been copied out of the project, because the
figures a fit writes are stamped with its id when it finishes.

Re-running an identical fit is recorded as a replicate of the first, and a
result whose data has changed underneath it is reported as `STALE` everywhere
it appears.

Fits are curated on the fit pages of `nrw serve` — star, finalize, discard,
restore and delete — or with `nrw fit star`, `unstar`, `discard`, `restore`
and `delete`; **Finalize** on a page is `nrw promote --as final`. A discarded
fit leaves the listings (`nrw ls --all` shows it) but keeps its files until you
delete them, which is refused while another fit reads them, and the record that it
ran is never lost. A sample's final fit is published to the ISAAC Portal from
its page, or with `nrw isaac export` and `nrw isaac push`, and each push is
recorded with the fit. See [experiment.md](experiment.md#curating-fits).

`nrw pack` closes the last gap. A result directory records the *hashes* of its
data, not the data, so it describes a fit nobody else can run. A bundle carries
the measurements themselves at the paths the frozen script expects, plus a
`verify.py` that applies the recorded best-fit parameters and checks
chi-squared against the value it should get. The recipient needs refl1d, bumps
and numpy.
