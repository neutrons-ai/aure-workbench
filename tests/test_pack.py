"""`nrw pack`: a fit a collaborator can run without nr-workbench.

The load-bearing test here is `test_a_bundle_reproduces_its_fit_without_the_workbench`.
Everything else checks the shape of the bundle; that one checks the claim.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.provenance.hashing import sha256_file

from .test_lifecycle import write_partials


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with one sample carrying real steady-state data."""
    root = tmp_path / "proj"
    result = CliRunner().invoke(main, ["init", str(root), "--sample", "S1"])
    assert result.exit_code == 0, result.output
    write_partials(root / "samples" / "S1" / "data" / "steady", 100001)
    return root


def run(root: Path, monkeypatch, *args: str):
    """Invoke the CLI inside a project."""
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


@pytest.fixture
def fitted(project: Path, monkeypatch) -> tuple[Path, str]:
    """A project with one real fit, and that fit's id."""
    pytest.importorskip("refl1d")
    assert run(project, monkeypatch, "model", "new", "S1", "--name", "m").exit_code == 0
    assert (
        run(
            project, monkeypatch, "model", "generate", "samples/S1/models/m.yaml"
        ).exit_code
        == 0
    )
    result = run(
        project,
        monkeypatch,
        "fit",
        "run",
        "samples/S1/models/m.py",
        "--method",
        "amoeba",
        "--steps",
        "20",
        "--seed",
        "1",
        "--parallel",
        "1",
        "--note",
        "the one to send",
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(run(project, monkeypatch, "ls", "--json").stdout)
    return project, rows[0]["fit_id"]


# --------------------------------------------------------------------------
# The claim
# --------------------------------------------------------------------------


def test_a_bundle_reproduces_its_fit_without_the_workbench(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """The whole point, tested the only way that proves it: unpack the bundle
    somewhere with no project, block every workbench import, and run it.

    Blocking the imports matters. Running `verify.py` in the development
    virtualenv would pass even if the script secretly needed nr-workbench,
    which is exactly the failure a collaborator would hit and we would not.
    """
    root, fit_id = fitted
    archive = tmp_path / "send-me.zip"
    result = run(root, monkeypatch, "pack", fit_id, "--out", str(archive))
    assert result.exit_code == 0, result.output

    elsewhere = tmp_path / "collaborator"
    elsewhere.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(elsewhere)
    bundle = elsewhere / "send-me"
    assert (bundle / "verify.py").is_file()

    blocker = (
        "import sys\n"
        "class Blocked:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in {'nr_workbench', 'aure', 'flask', 'click'}:\n"
        "            raise ImportError('BLOCKED: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocked())\n"
        "import runpy; sys.argv = ['verify.py']\n"
        "runpy.run_path('verify.py', run_name='__main__')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", blocker],
        cwd=bundle,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "REPRODUCED" in completed.stdout, completed.stdout


def test_verify_applies_the_fitted_parameters_not_the_starting_ones(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """A model script constructs its problem at the *starting* values, so
    chi-squared straight after loading measures the initial guess. Without the
    `.par` file a bundle rebuilds the model but not the answer -- and reports
    a mismatch against its own recorded result.
    """
    root, fit_id = fitted
    bundle = tmp_path / "b"
    assert (
        run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle)).exit_code
        == 0
    )

    par = next((bundle / "original-results" / "fit").glob("*.par"))
    starting = _verify(bundle, after=lambda: par.unlink())

    assert "only the model was checked" in starting.stdout
    starting_chisq = float(starting.stdout.split("starting values")[1].split()[0])
    recorded = json.loads((bundle / "MANIFEST.json").read_text())["expected"]["chisq"]
    assert starting_chisq != pytest.approx(recorded, rel=1e-6), (
        "if these agreed the test could not tell the two apart"
    )


def _verify(bundle: Path, after=None) -> subprocess.CompletedProcess[str]:
    """Run a bundle's verify.py, optionally mutating the bundle first."""
    if after is not None:
        after()
    return subprocess.run(
        [sys.executable, "verify.py"],
        cwd=bundle,
        capture_output=True,
        text=True,
        check=False,
    )


def test_verify_notices_a_model_whose_parameters_no_longer_match(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """Comparing chi-squared between two models with different parameters is
    meaningless, and might accidentally agree. Check the sets first."""
    root, fit_id = fitted
    bundle = tmp_path / "b"
    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    par = next((bundle / "original-results" / "fit").glob("*.par"))
    par.write_text(
        par.read_text(encoding="utf-8") + "a parameter that never existed 1.0\n",
        encoding="utf-8",
    )

    completed = _verify(bundle)

    assert completed.returncode == 1
    assert "in the record, not in the model" in completed.stdout


# --------------------------------------------------------------------------
# What is in the bundle
# --------------------------------------------------------------------------


def test_the_bundled_script_is_byte_identical_to_the_one_that_ran(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """Rewriting the script to suit the bundle would break the one thing the
    manifest can check: that this is the script whose hash is recorded."""
    root, fit_id = fitted
    bundle = tmp_path / "b"
    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    payload = json.loads((bundle / "MANIFEST.json").read_text())
    recorded = payload["provenance"]["identity"]["script_sha256"]

    assert sha256_file(bundle / payload["script"]) == recorded


def test_every_recorded_input_is_present_with_its_recorded_hash(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """A result directory records hashes, not data. The bundle carries the
    data, and it has to be the data those hashes describe."""
    root, fit_id = fitted
    bundle = tmp_path / "b"
    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    payload = json.loads((bundle / "MANIFEST.json").read_text())
    assert payload["inputs"], "a fit with no recorded inputs proves nothing"
    for item in payload["inputs"]:
        copied = bundle / item["path"]
        assert copied.is_file(), item["path"]
        assert sha256_file(copied) == item["sha256"], item["path"]


def test_the_bundle_pins_only_what_the_script_needs(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """The recorded environment is the whole development virtualenv. Asking a
    collaborator to install flask to run a reflectometry model is noise."""
    root, fit_id = fitted
    bundle = tmp_path / "b"
    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    pinned = (bundle / "requirements.txt").read_text(encoding="utf-8")

    assert "refl1d==" in pinned
    assert "bumps==" in pinned
    assert "nr-workbench" not in pinned
    assert "flask" not in pinned.lower()
    # ...but the exact environment is still there for anyone who needs it.
    assert (bundle / "original-results" / "env" / "requirements.txt").is_file()


def test_the_chain_is_omitted_but_said_so(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """Silent truncation reads as "everything is here" when it is not."""
    root, fit_id = fitted
    chain = root / "samples" / "S1" / "results" / fit_id / "fit" / "m-point.mc.gz"
    chain.write_bytes(b"pretend chain")
    bundle = tmp_path / "b"

    result = run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    assert not (bundle / "original-results" / "fit" / "m-point.mc.gz").exists()
    assert "m-point.mc.gz" in (bundle / "README.md").read_text(encoding="utf-8")
    payload = json.loads((bundle / "MANIFEST.json").read_text())
    assert payload["omitted"]["chain_files"] == ["m-point.mc.gz"]
    assert "--with-chain" in result.output


def test_with_chain_includes_it(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    root, fit_id = fitted
    chain = root / "samples" / "S1" / "results" / fit_id / "fit" / "m-point.mc.gz"
    chain.write_bytes(b"pretend chain")
    bundle = tmp_path / "b"

    run(
        root, monkeypatch, "pack", fit_id, "--dir", "--with-chain", "--out", str(bundle)
    )

    assert (bundle / "original-results" / "fit" / "m-point.mc.gz").is_file()


def test_the_readme_states_the_result_to_expect(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    root, fit_id = fitted
    bundle = tmp_path / "b"
    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    readme = (bundle / "README.md").read_text(encoding="utf-8")

    assert fit_id in readme
    assert "the one to send" in readme, "the note is what makes it recognisable"
    assert "chi-squared" in readme
    assert "python verify.py" in readme


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_packing_a_fit_whose_data_has_drifted_is_refused(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """A bundle asserts that its data produced its result. Shipping a file
    that has changed since the fit would make that a false claim, quietly."""
    root, fit_id = fitted
    data = next((root / "samples" / "S1" / "data" / "steady").glob("*.txt"))
    data.write_text(data.read_text(encoding="utf-8") + "0.3 1e-7 1e-8 3e-4\n")

    result = run(root, monkeypatch, "pack", fit_id, "--out", str(tmp_path / "b.zip"))

    assert result.exit_code != 0
    assert "no longer match" in result.output
    assert not (tmp_path / "b.zip").exists(), "and nothing is left behind"


def test_a_forced_bundle_records_that_it_was_forced(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """--force is a legitimate escape hatch, but the recipient must be able to
    see it was used -- they cannot check a hash they were never given."""
    root, fit_id = fitted
    data = next((root / "samples" / "S1" / "data" / "steady").glob("*.txt"))
    data.write_text(data.read_text(encoding="utf-8") + "0.3 1e-7 1e-8 3e-4\n")
    bundle = tmp_path / "b"

    result = run(
        root, monkeypatch, "pack", fit_id, "--dir", "--force", "--out", str(bundle)
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((bundle / "MANIFEST.json").read_text())
    assert payload["input_drift_at_pack_time"], "the drift is named"
    assert "forced" in (bundle / "README.md").read_text(encoding="utf-8")


def test_packing_over_an_existing_bundle_is_refused(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    root, fit_id = fitted
    target = tmp_path / "b.zip"
    assert run(root, monkeypatch, "pack", fit_id, "--out", str(target)).exit_code == 0
    before = target.read_bytes()

    result = run(root, monkeypatch, "pack", fit_id, "--out", str(target))

    assert result.exit_code != 0
    assert target.read_bytes() == before, "the existing bundle is untouched"


def test_an_unknown_fit_is_a_clear_error(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "pack", "nope")

    assert result.exit_code != 0
    assert "No fit matching" in result.output


def test_a_missing_input_leaves_nothing_behind(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """A half-written bundle is worse than none: it looks complete."""
    root, fit_id = fitted
    data = next((root / "samples" / "S1" / "data" / "steady").glob("*.txt"))
    data.unlink()
    bundle = tmp_path / "b"

    result = run(
        root, monkeypatch, "pack", fit_id, "--dir", "--force", "--out", str(bundle)
    )

    assert result.exit_code != 0
    assert not bundle.exists()


def test_the_generated_verify_script_is_valid_python(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """It is built by string formatting, one escaping mistake away from a
    syntax error that only shows up on the collaborator's machine."""
    root, fit_id = fitted
    bundle = tmp_path / "b"
    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    source = (bundle / "verify.py").read_text(encoding="utf-8")

    compile(source, "verify.py", "exec")


def test_the_zip_unpacks_into_one_directory(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """Nobody wants a zip bomb of loose files in their Downloads folder."""
    root, fit_id = fitted
    archive = tmp_path / "send-me.zip"
    run(root, monkeypatch, "pack", fit_id, "--out", str(archive))

    with zipfile.ZipFile(archive) as zipped:
        tops = {name.split("/")[0] for name in zipped.namelist()}

    assert tops == {"send-me"}


def test_the_staging_directory_is_cleaned_up(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    root, fit_id = fitted
    archive = tmp_path / "send-me.zip"

    run(root, monkeypatch, "pack", fit_id, "--out", str(archive))

    assert archive.is_file()
    assert not (tmp_path / "send-me").exists()


def test_a_bundle_directory_can_be_zipped_by_hand_later(
    fitted: tuple[Path, str], monkeypatch, tmp_path: Path
) -> None:
    """--dir exists so a large bundle can be moved with rsync instead."""
    root, fit_id = fitted
    bundle = tmp_path / "b"

    run(root, monkeypatch, "pack", fit_id, "--dir", "--out", str(bundle))

    assert bundle.is_dir()
    assert not bundle.with_suffix(".zip").exists()
    shutil.make_archive(str(tmp_path / "by-hand"), "zip", root_dir=bundle)
    assert (tmp_path / "by-hand.zip").is_file()
