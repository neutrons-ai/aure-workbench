"""``python -m nr_workbench``: the ``nrw`` command.

The web server runs nrw's own commands as child processes -- a fit takes
minutes of CPU and must be cancellable -- and names them this way rather than
guessing where the ``nrw`` script was installed: ``sys.executable -m
nr_workbench`` is the interpreter the server itself runs in.
"""

from nr_workbench.cli import main

if __name__ == "__main__":
    main(prog_name="nrw")
