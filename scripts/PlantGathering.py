from scripts.Gathering import GatherBase


class PlantGathering(GatherBase):
    """定時採集植物"""

    prefix = "plant"
    label = "採集（無工具）"
    pressGap = (0.15, 0.4)  # 連點的間隔(秒)
