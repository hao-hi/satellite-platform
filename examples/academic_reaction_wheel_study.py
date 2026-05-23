"""Thin CLI wrapper for the reusable reaction-wheel study."""

from satmodel.studies.reaction_wheel_study import main as _cli_main
from satmodel.studies.reaction_wheel_study import run_reaction_wheel_study


def main(output_dir="results/reaction_wheel_study", *, duration=20.0, dt=0.02, make_plots=True):
    """Run the reusable study from the examples folder."""

    return run_reaction_wheel_study(output_dir, duration=duration, dt=dt, make_plots=make_plots)


if __name__ == "__main__":
    _cli_main()
