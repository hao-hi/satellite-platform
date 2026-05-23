"""反作用轮论文式实验的薄命令行包装。

真正的实验逻辑位于 `satmodel.studies.reaction_wheel_study`，这里保留为 examples 入口。
"""

from satmodel.studies.reaction_wheel_study import main as _cli_main
from satmodel.studies.reaction_wheel_study import run_reaction_wheel_study


def main(output_dir="results/reaction_wheel_study", *, duration=20.0, dt=0.02, make_plots=True):
    """从 examples 目录调用可复用研究函数。"""

    # 输出格式保持为 CSV、Markdown 和可选 PNG 图，便于直接写进实验报告。
    return run_reaction_wheel_study(output_dir, duration=duration, dt=dt, make_plots=make_plots)


if __name__ == "__main__":
    _cli_main()
