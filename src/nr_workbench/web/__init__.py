"""The read-only web view of a workbench project.

:mod:`~nr_workbench.web.project` holds the data access and imports no web
framework; :mod:`~nr_workbench.web.app` is a thin Flask layer over it. Nothing
here is imported by the CLI until ``nrw serve`` runs, so Flask stays off the
``nrw --help`` path.
"""
