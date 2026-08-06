# AI workbench for neutron reflectometry analysis

## Goal
Develop a project template to be used to analyze reflectometry data. I envisage a python package that
the user installs in a new venv. The user would then call the CLI with `nr-workbench init`, with would
create the directory structure for their data. The user would then copy the data to be analyzed on the right folders,
and interact with Claude Code within VS Code to generate fitting scripts, reports, etc...


## The Problem
The repo in ~/git/experiments-2025 is an example of the current situation. It attempts to organize data, models, final results, context explaining the measurements,
in an attempts to organize things in one place. The problem is that it become a disorganized dump. Multiple version of the fitting scripts are present, and we don't
know which one is the final one, or which one produced the final results.

We need a way to capture provenance for our results. We also need things to be a little more automated. For instance, writing the scripts for the time-resolved (tNR) data is still very complex,
especially because we need to add functional constraints for this data, and it needs to be co-refined with steady-state data sets.

## Requirements

1- Viewing the whole landscape - UI
We need a simple web UI to visualize the measurements on a given sample. This may include a number of steady-state measurements, and tNR series.
When fits are available, they should also be presented, along with the corresponding SLD curves.

2- Model writer
We can re-use agent skills and utilities from the AuRE project (~/git/aure). AuRE is an agent that takes in context to fit reflectivity data. It has a way to parse a sample description into a structure and into an object that can be submitted for fitting. In our present case, we may also want to produce the fitting (refl1d) script. It's not necessary to launch AuRE, since it doesn't deal with tNR data. But it may contain useful tools.

3- Data assessment tools
Look in ~/git/experiments-2025/docs/tnr-amplitude.md. This sort of assessment should be turned into skills. In general, we want `nr-workbench init` to create a skills directory that a code agent can rely on, and it should have all the information/skills from AuRE and the instructions from the experiments-2025 repo. You can also look at the skills in the ~/git/nr-analyzer repo, which is a shared project with other facilities. nr-analyzer is meant to be a general subset of our work here. The goal of our current project is to have a one-stop-shop for OUR beamline, not all beamlines.
