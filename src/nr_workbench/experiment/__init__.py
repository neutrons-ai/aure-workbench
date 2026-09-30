"""The experiment: every run of a beamtime, organized into samples.

A project is one experiment -- one IPTS, one beamtime. This package holds what
the experimenter decides about its runs (which sample each belongs to, under
what condition, whether to use it) and the context that goes with each sample.
That decision is stored in ``experiment/*.parquet`` and is the source of truth
for each managed sample's ``sample.md``; an explicit *apply* projects it into
the ordinary ``samples/<id>/`` layout, so nothing downstream has to know this
package exists.

Three questions are answered behind three small interfaces, so that each can
be replaced without touching the others:

``DataSource`` (:mod:`~nr_workbench.experiment.sources`)
    Where the reduced data comes from. A folder on the data mount today;
    Tiled later.

``RunFeed`` (:mod:`~nr_workbench.experiment.feeds`)
    How we learn that a run exists. New files appearing in that folder today;
    the SNS web monitor or Tiled later -- which can announce a run before its
    reduction exists, so the feed is deliberately not part of the source.

``CatalogStore`` (:mod:`~nr_workbench.experiment.store`)
    Where the organization is kept. Parquet in the project today; possibly a
    facility API later.

Nothing here imports pyarrow, Flask or aure at module scope: ``nrw --help``
must stay instant, and the web layer imports this package.
"""
