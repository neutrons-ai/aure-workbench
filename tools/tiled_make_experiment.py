#!/usr/bin/env python3
"""Make an experiment's node on Tiled, ``projects/isaac/IPTS-<n>``, by hand.

A test tool for prototyping, not part of nrw: on the facility's server another
process makes each experiment's node, and ``nrw tiled push`` and ``pull``
write only inside one that exists. This makes one, so they can be tried on an
experiment that has none yet.

It writes to the server nrw uses: ``TILED_URL``, which is tiled.ornl.gov when
nothing sets it, read as nrw reads it. It acts as you: with the login
``nrw tiled login`` kept, or ``TILED_API_KEY``. What it makes stays there: it
cannot remove it, and on tiled.ornl.gov the facility's own process may later
want to make the same node.

Without ``--create`` it makes nothing. It says the versions of the Tiled
client here and of the server, who the server takes you to be, the tags of
``projects/isaac``, of an experiment given with ``--like``, and of the
experiment's node when it is there already, and what ``--create`` would make.
``--like`` shows the kind of tags an experiment carries: give the new one the
same kind, with its own number, never another experiment's tag, which would
open it to that experiment's people.

With ``--create`` it makes the node with the ``--tag`` tags, then reads it
back from the server and checks it holds them. At least one tag is required:
a node made with none is its maker's alone, which no one else on the
experiment can see, and ``nrw tiled push`` refuses to write in. The server
decides whether you may make it, and with those tags, and says why when it
refuses; nothing is made then. A server keeps only tags sent as its own
version of Tiled sends them, so the client here must be the server's version.

Run it with the Python nrw is installed in, which has the Tiled client:

    ~/.local/share/uv/tools/nr-workbench/bin/python \\
        tools/tiled_make_experiment.py 12345 --like IPTS-37740
    ~/.local/share/uv/tools/nr-workbench/bin/python \\
        tools/tiled_make_experiment.py 12345 --tag ipts-12345 --create
"""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    """Say what is there, and make the experiment's node with ``--create``.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 when the node is there, was made with its tags,
        or would be made; 1 when it was not made, was made without its tags
        or cannot be read back, or the server could not be asked.
    """
    from nr_workbench.project.experiment_schema import normalize_ipts
    from nr_workbench.tiled.server import (
        EXPERIMENT_PATH,
        TiledSyncError,
        connect,
        experiment_node,
        library,
        server,
        tags_of,
        talking,
        whoami,
    )

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ipts", help="The experiment: IPTS-12345, or 12345.")
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="A tag to give the node; repeat for more. Required with --create.",
    )
    parser.add_argument(
        "--like",
        metavar="IPTS",
        help="Also show this existing experiment's tags.",
    )
    parser.add_argument(
        "--create", action="store_true", help="Make the node. Without it, nothing is."
    )
    args = parser.parse_args(argv)

    ipts = normalize_ipts(args.ipts)
    like = normalize_ipts(args.like) if args.like else None
    for given, named in ((args.ipts, ipts), (args.like, like)):
        if given and named is None:
            print(f"{given} is not an IPTS: write it as IPTS-12345, or 12345.")
            return 1
    assert ipts is not None
    if args.create and not args.tags:
        print(
            "Give at least one --tag: a node made with none is yours alone, "
            "which no one else on the experiment can see. Nothing was made."
        )
        return 1

    path = EXPERIMENT_PATH.format(ipts=ipts)
    parent_path = path.rsplit("/", 1)[0]
    try:
        chosen = server()
        with talking(chosen.url, library()):
            client = connect()
            served = client.context.server_info.library_version
            print(f"Server:    {chosen.url} ({chosen.source}), Tiled {served}")
            print(f"Client:    Tiled {version('tiled')}")
            print(f"You:       {whoami(client)}")
            try:
                parent = client[parent_path]
            except KeyError:
                print(f"{parent_path} is not on the server, or not shared with you.")
                return 1
            print(f"{parent_path}: tags {tags_of(parent)}")
            if like is not None:
                other = experiment_node(client, like)
                said = "not there" if other is None else f"tags {tags_of(other)}"
                print(f"{EXPERIMENT_PATH.format(ipts=like)}: {said}")
            there = experiment_node(client, ipts)
            if there is not None:
                print(f"{path}: there already, tags {tags_of(there)}. Nothing made.")
                return 0
            if not args.create:
                tags = args.tags or "(give --tag)"
                print(f"{path}: not there. --create would make it, with tags {tags}.")
                return 0
            parent.create_container(ipts, access_tags=args.tags)
            # Read back from the server: what the client returns from making
            # it holds the tags it sent, not the ones the server kept.
            made = experiment_node(client, ipts)
            if made is None:
                print(
                    f"{path}: made, but your login cannot read it back, so it does "
                    "not carry the tags it was given. Only an admin of the server "
                    "can remove it."
                )
                return 1
            held = tags_of(made)
            if not set(args.tags) <= set(held):
                print(
                    f"{path}: made WITHOUT the tags it was given ({args.tags}): it "
                    f"has {held or 'none'}, so it is yours alone. The server keeps "
                    "only tags sent as its own version of Tiled sends them: compare "
                    "the two versions above. Only an admin of the server can "
                    "remove it."
                )
                return 1
            print(f"{path}: made, tags {held}.")
    except TiledSyncError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
