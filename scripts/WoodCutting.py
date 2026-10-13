from scripts.Gathering import GatherBase


class WoodCutting(GatherBase):
    """定時砍木頭"""

    prefix = "wood"
    label = "採集（有工具）"
    pressGap = (0.9, 1.2)  # 連點的間隔(秒)。砍一下的動作要做完才吃得到下一次按鍵，太短會被遊戲略過；實測按鍵精靈 1000 毫秒穩定，在附近隨機
