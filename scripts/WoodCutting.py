from scripts.Gathering import GatherBase


class WoodCutting(GatherBase):
    """定時砍木頭"""

    prefix = "wood"
    label = "砍木頭"
    pressGap = (0.15, 0.4)  # 連點的間隔(秒)
