import time
import requests
import logging
import os
import re

from .Recorder import Recorder
from .FlvCheckThread import FlvCheckThread

logger = logging.getLogger('monitor')

startNotice = """\
直播间 {name}({id}) 录制开始。
  🎈 直播标题：{title}"""

endNotice = """\
直播间 {name}({id}) 录制结束。
  🎈 直播标题：{title}
  💾 文件大小：{filesize}
  ⏱ 录制时长：{duration}"""


def _dividePeriod(dt):  # 将timestamp转换为时段的编号
    return int(dt) % 86400//600


def _dataunitConv(size: int):  # size in bytes
    # 自动单位转换
    if size <= 0:
        return '0'
    n = int(size)
    magnitude = -1
    units = ['bytes', 'KB', 'MB', 'GB', 'TB']
    while n:
        n >>= 10
        magnitude += 1
    return '{:.3f} {}'.format(size/(1 << magnitude*10), units[magnitude])


def _formatDuration(duration: float):
    duration = int(duration)
    seconds = duration % 60
    minutes = duration//60 % 60
    hours = duration//3600
    return \
        f'{hours}hr {minutes:02d}min {seconds:02d}sec' if hours else \
        f'{minutes}min {seconds:02d}sec' if minutes else \
        f'{seconds}sec'


class LiveRoom():
    overrideDynamicInterval = False

    def __init__(self, roomid, code, savefolder, updateInterval=60, history=None, tmpfolder=None):

        self.id = roomid
        self.code = code
        self._savefolder = savefolder
        self._tmpfolder = tmpfolder or savefolder
        self.history = history or [0]*144
        self._baseUpdateInterval = updateInterval

        self._roomInfo = {}
        self._username = None
        self.onair = False
        self.recordThread = None

    @property
    def _headers(self):
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Origin': 'https://live.bilibili.com',
            'Referer': f'https://live.bilibili.com/blanc/{self.id}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0'
        }

    def _getUserName(self):
        # 获取用户名
        response = requests.get(
            "https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={}".format(
                self.id),
            timeout=10, headers=self._headers
        ).json()
        self._username = response['data']['info']['uname']
        logger.info(f'{self.code}: Retrieved username {self._username}')

    def _updateStatus(self):
        # 获取房间基本信息及是否开播
        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info?id={}".format(
                self.id),
            timeout=10, headers=self._headers
        ).json()
        self._roomInfo = {
            key: response['data'][key]
            for key in ['room_id', 'live_status', 'title', 'description', 'uid']
        }
        self.onair = self._roomInfo['live_status'] == 1

    def _getLiveUrl(self):
        # 获取推流链接
        if not self.onair:
            logger.info(f'{self.code} is not on air.')
            return None

        # 推流码率
        rates = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/playUrl?cid={}&quality=0&platform=web".format(
                self._roomInfo['room_id']),
            timeout=10, headers=self._headers
        ).json()['data']['quality_description']
        self._roomInfo['live_rates'] = {
            rate['qn']: rate['desc'] for rate in rates}
        qn = max(self._roomInfo['live_rates'])

        # 推流链接
        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/playUrl?cid={}&quality={}&platform=web".format(
                self._roomInfo['room_id'], qn),
            timeout=10, headers=self._headers
        ).json()
        url = response['data']['durl'][0]['url']
        return url

    def startRecording(self):
        if not self.onair:
            logger.info(f'{self.code} is not on air.')
            return None
        if not self._username:
            self._getUserName()
        url = self._getLiveUrl()
        if not os.path.isdir(self._tmpfolder):
            os.mkdir(self._tmpfolder)
        filename = '{room_id}-{username}-{time}-{endtime}-{title}'.format(
            **self._roomInfo,
            username=self._username,
            time=time.strftime('%y%m%d%H%M%S'),
            endtime='{endtime}'
        )

        # 防止标题和用户名中含有windows路径的非法字符
        filename = re.sub(r'[\<\>\:\"\\\'\\\/\|\?\*\.]', '', filename)+'.flv'
        self.notifyAtBeginning()
        self.recordThread = Recorder(
            url=url,
            savepath=os.path.join(self._tmpfolder, filename),
            threadid=self.code,
            room=self
        )
        self.recordThread.start()

    def report(self) -> float:
        # 返回值为现在距下一次检查的时间
        if self.recordThread:
            if self.recordThread.isRecording():
                logger.info('{}: {} downloaded.'.format(
                    self.code, _dataunitConv(self.recordThread.downloaded)))
                return 10  # report status after 10 sec
            else:
                del self.recordThread
                self.recordThread = None
                return 5 # 防止因网络问题导致断流
        else:
            interval = self.updateInterval
            logger.info(
                f'{self.code}: updating status with interval {interval:.3f}s.')
            try:
                self._updateStatus()
            except requests.exceptions:
                logger.error(
                    f'{self.code}: Requests\' exception encountered, retry after 60s.')
                return 60
            else:
                logger.info(f'{self.code}: status updated.')
                if self.onair:
                    logger.info(f'{self.code}: start recording.')
                    self.startRecording()
                    return 10
                else:
                    return interval

    @property
    def updateInterval(self):
        if self.overrideDynamicInterval:
            return self._baseUpdateInterval
        else:
            if sum(self.history) < 72:  # 历史数据太少
                return self._baseUpdateInterval
            else:
                t = _dividePeriod(time.time())
                return 300*(self._baseUpdateInterval / 300)**(self.history[t]/max(self.history))

    def recordingFinished(self, path, datasize, sttime, endtime):
        if datasize < 65536:  # 64KB
            os.remove(path)  # 删除过小的文件
        else:
            # note live history
            st = _dividePeriod(sttime)
            end = _dividePeriod(endtime)
            if st > end:
                end += 144
            for i in range(st, end):
                self.history[i % 144] += 1

            if not os.path.isdir(self._savefolder):
                os.mkdir(self._savefolder)
            filename = os.path.basename(path).format(
                endtime=time.strftime('%H%M%S', time.localtime(endtime)))
            temppath = os.path.join(self._tmpfolder, filename)
            saveto = os.path.join(self._savefolder, filename)
            if self._tmpfolder == self._savefolder:
                temppath = temppath[:-4]+".tmp.flv"

            os.rename(path, temppath)

            self.notifyAtEnd(endtime-sttime, datasize)

            logger.info(f'{self.code}: enqueue FlvCheck task.')
            FlvCheckThread.addTask(temppath, saveto)
            

    def notifyAtBeginning(self):
        pass

    def notifyAtEnd(self, duration, filesize):
        pass

    @classmethod
    def setNotification(cls, barkurl):
        def pushMessage(title, msg):
            logger.info(
                f"sending message to {barkurl}:\n title:{title} \n{msg}")
            try:
                req = requests.post(barkurl, data={
                    "title": title,
                    "body": msg,
                    "group": "recorder"
                })
            except:
                logger.warning("error occurred when sending message.")
            else:
                logger.info(f"received data: {req.text}")

        def notifyAtBeginning(self):
            pushMessage("录播姬", startNotice.format(
                name=self._username or self.code,
                id=self.id,
                title=self._roomInfo['title']
            ))

        def notifyAtEnd(self, duration, filesize):
            pushMessage("录播姬", endNotice.format(
                name=self._username or self.code,
                id=self.id,
                title=self._roomInfo["title"],
                filesize=_dataunitConv(filesize),
                duration=_formatDuration(duration)
            ))

        cls.notifyAtBeginning = notifyAtBeginning
        cls.notifyAtEnd = notifyAtEnd
