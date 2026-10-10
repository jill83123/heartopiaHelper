from scripts.Gathering import GatherBase


class PlantGathering(GatherBase):
    """定時採集植物"""

    prefix = "plant"
    label = "採集植物"
    pressGap = (0.2, 0.6)
