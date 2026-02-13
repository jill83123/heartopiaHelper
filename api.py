import datetime
from tools.tools import (
    selectRegion,
    selectPoint,
    readConfig,
    writeConfig,
)
from scripts.Cooking import Cooking
from scripts.SnowCarving import SnowCarving


class Api:
    def __init__(self):
        self.config = readConfig()

        # cook
        self.cookingTask = None
        self.bubbleRegionCoord = None
        self.startCookBtnCoord = None

        # snow
        self.snowCarvingTask = None
        self.snowDetectRegionCoord = None

        # logs
        self.cookingLogIndex = 0
        self.cookingLogList = []
        self.snowLogIndex = 0
        self.snowLogList = []

    def setLog(self, msg, source):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        formattedMsg = f"[{now}] {msg}"
        print(formattedMsg)
        if source == "cooking":
            self.cookingLogIndex += 1
            self.cookingLogList.append({"index": self.cookingLogIndex, "msg": formattedMsg})
        elif source == "snowCarving":
            self.snowLogIndex += 1
            self.snowLogList.append({"index": self.snowLogIndex, "msg": formattedMsg})

    def apiGetNextLogs(self, currentIndex, source):
        if source == "cooking":
            for log in self.cookingLogList:
                if log["index"] > currentIndex:
                    return log
        elif source == "snowCarving":
            for log in self.snowLogList:
                if log["index"] > currentIndex:
                    return log
        return False

    def apiSelectRegion(self, name):
        coords = selectRegion()

        if not coords:
            return None

        setattr(self, name, coords)

        return {
            "status": "success",
            "selection": {
                "x": coords[0],
                "y": coords[1],
                "w": coords[2],
                "h": coords[3],
            },
        }

    def apiSelectPoint(self):
        point = selectPoint()
        if not point:
            return None
        self.startCookBtnCoord = point

        return {
            "status": "success",
            "selection": {
                "x": point[0],
                "y": point[1],
            },
        }

    def apiReadSettings(self):
        self.config = readConfig()
        return self.config

    def apiWriteSetting(self, key, value):
        writeConfig(key, value)
        self.apiReadSettings()
        return True

    def startCooking(self):
        def cookingLog(msg):
            self.setLog(msg, "cooking")

        self.cookingTask = Cooking(
            setLog=cookingLog,
            config=self.config,
            bubbleRegionCoord=self.bubbleRegionCoord,
            startCookBtnCoord=self.startCookBtnCoord,
        )
        self.cookingTask.start()

    def startSnowCarving(self):
        def snowLog(msg):
            self.setLog(msg, "snowCarving")

        self.snowCarvingTask = SnowCarving(
            setLog=snowLog,
            config=self.config,
            snowDetectRegionCoord=self.snowDetectRegionCoord,
        )
        self.snowCarvingTask.start()

    def stop(self):
        if self.cookingTask:
            self.cookingTask.stop()
            self.cookingTask = None

        if self.snowCarvingTask:
            self.snowCarvingTask.stop()
            self.snowCarvingTask = None

    def apiGetStatus(self):
        status = {"cooking": False, "snowCarving": False}

        if self.cookingTask and getattr(self.cookingTask, "isStart", False):
            status["cooking"] = True

        if self.snowCarvingTask and getattr(self.snowCarvingTask, "isStart", False):
            status["snowCarving"] = True

        return status
