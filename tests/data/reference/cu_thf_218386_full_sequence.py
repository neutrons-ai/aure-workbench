# REFERENCE FIXTURE -- DO NOT EDIT except for the two changes noted here.
#
# The 343-line hand-written co-refinement that nrw-model/1 has to reproduce,
# kept here so tests/test_model_gate.py can compare against it directly.
#
# Two deliberate departures from the original:
#
#   1. The two data-directory constants point at this fixture's own data.
#   2. `dL` is 0 rather than delta_wl_over_wl(wl) * q. BL-4B has standardised
#      on the angular-only resolution convention, and the moderator variant was
#      dimensionally wrong (multiplied by q, not wl). Normalising BOTH sides is
#      what keeps the gate a test of the model structure -- the co-refinement,
#      the parameter sharing, the constraints -- rather than of a convention the
#      project has dropped. delta_wl_over_wl is left defined but unused so the
#      diff against the original stays small and legible.
#
import os

import numpy as np
from refl1d.names import *
from refl1d.probe import make_probe


def delta_wl_over_wl(wl):
    """
    The moderator emission time equation gives a time in microsecond.
    """
    dtof = 0.0148 * wl * wl * wl - 0.5233 * wl * wl + 6.4797 * wl + 231.99
    dtof[wl > 2] = (
        392.31 * wl**6
        - 3169.3 * wl**5
        + 10445 * wl**4
        - 17872 * wl * wl * wl
        + 16509 * wl * wl
        - 7448.4 * wl
        + 1280.5
    )
    dwl = 3.9560 * dtof / (1000000 * 15.282 * wl)
    return dwl


def get_probe_parts(data_file, theta):
    q, data, errors, dq = np.loadtxt(data_file).T
    wl = 4 * np.pi * np.sin(np.pi / 180 * theta) / q
    dT = dq / q * np.tan(np.pi / 180 * theta) * 180 / np.pi
    dL = 0 * q  # NORMALISED: see the banner
    return data, errors, wl, dL, dT


def create_probe(data_file, theta):
    q, data, errors, dq = np.loadtxt(data_file).T
    wl = 4 * np.pi * np.sin(np.pi / 180 * theta) / q
    dT = dq / q * np.tan(np.pi / 180 * theta) * 180 / np.pi
    dL = 0 * q  # NORMALISED: see the banner

    # The following is how refl1d computes dQ
    # dQ = (4 * np.pi / wl) * np.sqrt((np.sin(np.pi/180*theta) * dL / wl) ** 2 + (np.cos(np.pi/180*theta) * dT * np.pi/180) ** 2)

    # dT and dL are FWHM
    probe = make_probe(
        T=theta,
        dT=dT,
        L=wl,
        dL=dL,
        data=(data, errors),
        radiation="neutron",
        resolution="uniform",
    )
    return probe


def create_sample(sld_thf=5.5):
    THF = SLD("THF", rho=sld_thf)
    Si = SLD("Si", rho=2.07)
    Ti = SLD("Ti", rho=-3)
    Cu = SLD("Cu", rho=6.3)
    CuOx = SLD(name="CuOx", rho=5, irho=0.0)

    sample = THF(0, 25) | CuOx(60, 20) | Cu(500, 5) | Ti(40, 10) | Si

    return sample


def linear_constraints(experiment_list, start_expt, end_expt):
    # The first experiment in the list is the reference
    # experiment_list[0].probe.theta_offset.range(-0.005, 0.005)
    # offset = Parameter(name="theta offset", value=0.0054)
    # experiment_list[0].probe.theta_offset = offset

    experiment_list[0].probe.theta_offset.range(-0.01, 0.01)
    experiment_list[0].probe.intensity = Parameter(value=1.0, name="Intensity_tNR")
    experiment_list[0].probe.intensity.pm(0.1)

    # The Q values for tNR are low enough that the background is negligible.
    # experiment_list[0].probe.background = Parameter(value=1e-6, name="Background_tNR")
    # experiment_list[0].probe.background.range(0, 1e-5)
    experiment_list[0].sample["Cu"].thickness = Parameter(
        name="Cu thickness tNR 0",
        value=experiment_list[0].sample["Cu"].thickness.value,
    )
    experiment_list[0].sample["Cu"].thickness.pm(100)
    # The THF and Cu SLDs are shared across all experiments
    experiment_list[0].sample["THF"].material.rho = start_expt.sample[
        "THF"
    ].material.rho
    experiment_list[0].sample["Cu"].material.rho = start_expt.sample["Cu"].material.rho

    for i, experiment2 in enumerate(experiment_list[1:], start=1):
        experiment2.probe.theta_offset = experiment_list[0].probe.theta_offset
        experiment2.probe.intensity = experiment_list[0].probe.intensity
        experiment2.probe.background = experiment_list[0].probe.background

        experiment2.sample["Cu"].thickness = Parameter(
            name=f"Cu thickness tNR {i}",
            value=experiment2.sample["Cu"].thickness.value,
        )
        experiment2.sample["Cu"].thickness.pm(100)

        # The THF and Cu SLDs are shared across all experiments
        experiment2.sample["THF"].material.rho = (
            experiment_list[0].sample["THF"].material.rho
        )
        experiment2.sample["Cu"].material.rho = (
            experiment_list[0].sample["Cu"].material.rho
        )

    # Linear constraints on the interfaces and thicknesses across the experiments
    interfaces = ["THF", "CuOx", "Cu", "Ti"]

    n_curves = len(experiment_list)
    for item in interfaces:
        p_start = start_expt.sample[item].interface
        p_end = end_expt.sample[item].interface

        for i, experiment2 in enumerate(experiment_list):
            # Set the interface as a linear function of the index
            interface_value = p_start + (p_end - p_start) * i / (n_curves - 1)
            experiment2.sample[item].interface = interface_value

    # Linear constraints on the thicknesses across the experiments
    thicknesses = ["CuOx", "Ti"]  # Cu is free
    for item in thicknesses:
        p_start = start_expt.sample[item].thickness
        p_end = end_expt.sample[item].thickness

        for i, experiment2 in enumerate(experiment_list):
            # Set the thickness as a linear function of the index
            thickness_value = p_start + (p_end - p_start) * i / (n_curves - 1)
            # thickness_value = p_start + p_slope * i + p_acc * i**2
            experiment2.sample[item].thickness = thickness_value

    # Linear constraints on the rhos across the experiments
    rhos = ["CuOx", "Ti"]  # THF and Cu are fixed.
    for item in rhos:
        p_start = start_expt.sample[item].material.rho
        p_end = end_expt.sample[item].material.rho

        for i, experiment2 in enumerate(experiment_list):
            # Set the rho as a linear function of the index
            rho_value = p_start + (p_end - p_start) * i / (n_curves - 1)
            experiment2.sample[item].material.rho = rho_value


# Data directory ######################################################################
script_dir = os.path.dirname(os.path.abspath(__file__))
steady_dir = os.path.join(script_dir, "steady") + os.sep
tNR_dir = os.path.join(script_dir, "tnr") + os.sep

# OCV 1 ###############################################################################
# The angles for the three runs are 0.45, 1.2, and 3.5 degrees
sld_thf = 6.2
run = 218386

data_files = [
    [os.path.join(steady_dir, "REFL_%s_1_%s_partial.txt" % (run, run)), 0.45],
    [os.path.join(steady_dir, "REFL_%s_2_%s_partial.txt" % (run, run + 1)), 1.2],
    [os.path.join(steady_dir, "REFL_%s_3_%s_partial.txt" % (run, run + 2)), 3.5],
]

exp_list_ocv1 = []
for i in range(len(data_files)):
    probe = create_probe(data_files[i][0], data_files[i][1])
    sample = create_sample(sld_thf=sld_thf)
    exp = Experiment(sample=sample, probe=probe)
    exp_list_ocv1.append(exp)

# Set up the fit parameters for OCV 1
# This is the first of the three runs, but the parameters are shared across all three runs.
exp_list_ocv1[0].sample["THF"].material.rho.range(4, 7)
exp_list_ocv1[0].sample["THF"].interface.range(3, 35)
exp_list_ocv1[0].sample["Ti"].thickness.range(25.0, 60.0)
exp_list_ocv1[0].sample["Ti"].material.rho.range(-4.0, 0)
exp_list_ocv1[0].sample["Ti"].interface.range(1.0, 22.0)

exp_list_ocv1[0].sample["Cu"].material.rho.range(5, 7)
exp_list_ocv1[0].sample["Cu"].interface.range(1, 25)
exp_list_ocv1[0].sample["Cu"].thickness.range(400, 600)

exp_list_ocv1[0].sample["CuOx"].thickness.range(10.0, 125.0)
exp_list_ocv1[0].sample["CuOx"].material.rho.range(1.0, 6.5)
exp_list_ocv1[0].sample["CuOx"].interface.range(3.0, 33.0)
exp_list_ocv1[0].probe.intensity = Parameter(value=1, name="Intensity_386_1")
exp_list_ocv1[0].probe.intensity.pm(0.1)
# exp_list_ocv1[0].probe.background = Parameter(value=0, name="Background_386_1")
# exp_list_ocv1[0].probe.background.range(0, 1e-5)

exp_list_ocv1[1].sample["THF"].material.rho = (
    exp_list_ocv1[0].sample["THF"].material.rho
)
exp_list_ocv1[1].sample["THF"].interface = exp_list_ocv1[0].sample["THF"].interface
exp_list_ocv1[1].sample["Ti"].thickness = exp_list_ocv1[0].sample["Ti"].thickness
exp_list_ocv1[1].sample["Ti"].material.rho = exp_list_ocv1[0].sample["Ti"].material.rho
exp_list_ocv1[1].sample["Ti"].interface = exp_list_ocv1[0].sample["Ti"].interface
exp_list_ocv1[1].sample["Cu"].material.rho = exp_list_ocv1[0].sample["Cu"].material.rho
exp_list_ocv1[1].sample["Cu"].interface = exp_list_ocv1[0].sample["Cu"].interface
exp_list_ocv1[1].sample["Cu"].thickness = exp_list_ocv1[0].sample["Cu"].thickness
exp_list_ocv1[1].sample["CuOx"].thickness = exp_list_ocv1[0].sample["CuOx"].thickness
exp_list_ocv1[1].sample["CuOx"].material.rho = (
    exp_list_ocv1[0].sample["CuOx"].material.rho
)
exp_list_ocv1[1].sample["CuOx"].interface = exp_list_ocv1[0].sample["CuOx"].interface
exp_list_ocv1[1].probe.intensity = exp_list_ocv1[0].probe.intensity
exp_list_ocv1[1].probe.background = exp_list_ocv1[0].probe.background

exp_list_ocv1[2].sample["THF"].material.rho = (
    exp_list_ocv1[0].sample["THF"].material.rho
)
exp_list_ocv1[2].sample["THF"].interface = exp_list_ocv1[0].sample["THF"].interface
exp_list_ocv1[2].sample["Ti"].thickness = exp_list_ocv1[0].sample["Ti"].thickness
exp_list_ocv1[2].sample["Ti"].material.rho = exp_list_ocv1[0].sample["Ti"].material.rho
exp_list_ocv1[2].sample["Ti"].interface = exp_list_ocv1[0].sample["Ti"].interface
exp_list_ocv1[2].sample["Cu"].material.rho = exp_list_ocv1[0].sample["Cu"].material.rho
exp_list_ocv1[2].sample["Cu"].interface = exp_list_ocv1[0].sample["Cu"].interface
exp_list_ocv1[2].sample["Cu"].thickness = exp_list_ocv1[0].sample["Cu"].thickness
exp_list_ocv1[2].sample["CuOx"].thickness = exp_list_ocv1[0].sample["CuOx"].thickness
exp_list_ocv1[2].sample["CuOx"].material.rho = (
    exp_list_ocv1[0].sample["CuOx"].material.rho
)
exp_list_ocv1[2].sample["CuOx"].interface = exp_list_ocv1[0].sample["CuOx"].interface

# The third run has a different intensity, so we don't share that parameter with the first two runs.
exp_list_ocv1[2].probe.intensity = Parameter(value=1, name="Intensity_386_3")
exp_list_ocv1[2].probe.intensity.range(0.5, 1.1)
exp_list_ocv1[2].probe.background = exp_list_ocv1[0].probe.background

# OCV 2 ###############################################################################
run = 218393

data_files = [
    [os.path.join(steady_dir, "REFL_%s_1_%s_partial.txt" % (run, run)), 0.45],
    [os.path.join(steady_dir, "REFL_%s_2_%s_partial.txt" % (run, run + 1)), 1.2],
    [os.path.join(steady_dir, "REFL_%s_3_%s_partial.txt" % (run, run + 2)), 3.5],
]

exp_list_ocv2 = []
for i in range(len(data_files)):
    probe = create_probe(data_files[i][0], data_files[i][1])
    sample = create_sample(sld_thf=sld_thf)
    exp = Experiment(sample=sample, probe=probe)
    exp_list_ocv2.append(exp)

# Set up the fit parameters for OCV 2
# This is the first of the three runs, but the parameters are shared across all three runs.
exp_list_ocv2[0].sample["THF"].material.rho = (
    exp_list_ocv1[0].sample["THF"].material.rho
)
exp_list_ocv2[0].sample["THF"].interface.range(3, 35)

free_Ti = True
if free_Ti:
    exp_list_ocv2[0].sample["Ti"].thickness.range(25.0, 60.0)
    exp_list_ocv2[0].sample["Ti"].material.rho.range(-4.0, 0)
    exp_list_ocv2[0].sample["Ti"].interface.range(1.0, 22.0)
else:
    exp_list_ocv2[0].sample["Ti"].thickness = exp_list_ocv1[0].sample["Ti"].thickness
    exp_list_ocv2[0].sample["Ti"].material.rho = (
        exp_list_ocv1[0].sample["Ti"].material.rho
    )
    exp_list_ocv2[0].sample["Ti"].interface = exp_list_ocv1[0].sample["Ti"].interface

exp_list_ocv2[0].sample["Cu"].material.rho = exp_list_ocv1[0].sample["Cu"].material.rho
exp_list_ocv2[0].sample["Cu"].thickness.range(400, 600)
if free_Ti:
    exp_list_ocv2[0].sample["Cu"].interface.range(1, 25)
else:
    exp_list_ocv2[0].sample["Cu"].interface = exp_list_ocv1[0].sample["Cu"].interface

exp_list_ocv2[0].sample["CuOx"].thickness.range(10.0, 125.0)
exp_list_ocv2[0].sample["CuOx"].material.rho.range(1.0, 6.5)
exp_list_ocv2[0].sample["CuOx"].interface.range(3.0, 33.0)
exp_list_ocv2[0].probe.intensity = Parameter(value=1, name="Intensity_393_1")
exp_list_ocv2[0].probe.intensity.pm(0.1)
# exp_list_ocv2[0].probe.background = Parameter(value=0, name="Background_393_1")
# exp_list_ocv2[0].probe.background.range(0, 1e-5)

exp_list_ocv2[1].sample["THF"].material.rho = (
    exp_list_ocv2[0].sample["THF"].material.rho
)
exp_list_ocv2[1].sample["THF"].interface = exp_list_ocv2[0].sample["THF"].interface
exp_list_ocv2[1].sample["Ti"].thickness = exp_list_ocv2[0].sample["Ti"].thickness
exp_list_ocv2[1].sample["Ti"].material.rho = exp_list_ocv2[0].sample["Ti"].material.rho
exp_list_ocv2[1].sample["Ti"].interface = exp_list_ocv2[0].sample["Ti"].interface
exp_list_ocv2[1].sample["Cu"].material.rho = exp_list_ocv2[0].sample["Cu"].material.rho
exp_list_ocv2[1].sample["Cu"].interface = exp_list_ocv2[0].sample["Cu"].interface
exp_list_ocv2[1].sample["Cu"].thickness = exp_list_ocv2[0].sample["Cu"].thickness
exp_list_ocv2[1].sample["CuOx"].thickness = exp_list_ocv2[0].sample["CuOx"].thickness
exp_list_ocv2[1].sample["CuOx"].material.rho = (
    exp_list_ocv2[0].sample["CuOx"].material.rho
)
exp_list_ocv2[1].sample["CuOx"].interface = exp_list_ocv2[0].sample["CuOx"].interface
exp_list_ocv2[1].probe.intensity = exp_list_ocv2[0].probe.intensity
exp_list_ocv2[1].probe.background = exp_list_ocv2[0].probe.background

exp_list_ocv2[2].sample["THF"].material.rho = (
    exp_list_ocv2[0].sample["THF"].material.rho
)
exp_list_ocv2[2].sample["THF"].interface = exp_list_ocv2[0].sample["THF"].interface
exp_list_ocv2[2].sample["Ti"].thickness = exp_list_ocv2[0].sample["Ti"].thickness
exp_list_ocv2[2].sample["Ti"].material.rho = exp_list_ocv2[0].sample["Ti"].material.rho
exp_list_ocv2[2].sample["Ti"].interface = exp_list_ocv2[0].sample["Ti"].interface
exp_list_ocv2[2].sample["Cu"].material.rho = exp_list_ocv2[0].sample["Cu"].material.rho
exp_list_ocv2[2].sample["Cu"].interface = exp_list_ocv2[0].sample["Cu"].interface
exp_list_ocv2[2].sample["Cu"].thickness = exp_list_ocv2[0].sample["Cu"].thickness
exp_list_ocv2[2].sample["CuOx"].thickness = exp_list_ocv2[0].sample["CuOx"].thickness
exp_list_ocv2[2].sample["CuOx"].material.rho = (
    exp_list_ocv2[0].sample["CuOx"].material.rho
)
exp_list_ocv2[2].sample["CuOx"].interface = exp_list_ocv2[0].sample["CuOx"].interface
exp_list_ocv2[2].probe.intensity = exp_list_ocv2[0].probe.intensity
exp_list_ocv2[2].probe.background = exp_list_ocv2[0].probe.background

# tNR ###############################################################################
# Angle is 0.6 degrees
run = 218389

INCLUDE_TNR = True

exp_list_ocv3 = []
if INCLUDE_TNR:
    # For a quick test to see that everything aligns, we can fit early and late tNR data.
    data_files = [
        [os.path.join(tNR_dir, f"r{run}_hold_initial_1.txt"), 0.6],
        [os.path.join(tNR_dir, f"r{run}_hold_gap_14_5.txt"), 0.6],
    ]

    # tNR during EIS
    data_files = [
        [os.path.join(tNR_dir, f"r{run}_sequence_{i}_eis_{i}.txt"), 0.6]
        for i in range(1, 16)
    ]

    for i in range(len(data_files)):
        probe = create_probe(data_files[i][0], data_files[i][1])
        sample = create_sample(sld_thf=sld_thf)
        exp = Experiment(sample=sample, probe=probe)
        exp_list_ocv3.append(exp)

    linear_constraints(exp_list_ocv3, exp_list_ocv1[0], exp_list_ocv2[0])


# DON'T FORGET TO UPDATE THE DATA DIRECTORIES ABOVE
problem = FitProblem(exp_list_ocv1 + exp_list_ocv2 + exp_list_ocv3)
