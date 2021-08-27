import time
import asyncio
import logging
import os
import re
import httpx

from .Recorder import Recorder

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
    recording_tasks = []
    running = True

    def __init__(self, roomid, code, savefolder, updateInterval=60, history=None, tmpfolder=None):
        self.id = roomid
        self.code = code
        self._savefolder = savefolder
        self._tmpfolder = tmpfolder or savefolder
        self.history = history or [0]*144
        self._baseUpdateInterval = updateInterval

        self._livetitle = None
        self._username = self._getUserName()
        self.onair = False
        self.recorder = None
        self.recordTask = None
        self.isRecording = False

    @property
    def _headers(self):
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Origin': 'https://live.bilibili.com',
            'Referer': f'https://live.bilibili.com/blanc/{self.id}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0'
        }

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

    def _getUserName(self):
        # 获取用户名
        response = httpx.get(
            f"https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={self.id}",
            timeout=10, headers=self._headers
        )
        username = response.json()['data']['info']['uname']
        logger.info(f'{self.code}: Retrieved username {username}')
        return username

    async def _updateStatus(self):
        # 获取房间基本信息及是否开播
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.live.bilibili.com/room/v1/Room/get_info?id={self.id}",
                timeout=10, headers=self._headers
            )
        response = response.json()
        self.onair = response['data']['live_status'] == 1
        if self.onair:
            self._livetitle = response['data']['title']

    async def _getLiveUrl(self):
        # 获取推流链接
        if not self.onair:
            logger.info(f'{self.code} is not on air.')
            return None

        # 推流码率
        async with httpx.AsyncClient() as client:
            rates = await client.get(
                f"https://api.live.bilibili.com/room/v1/Room/playUrl?cid={self.id}&quality=0&platform=web",
                timeout=10, headers=self._headers
            )
        liverates = {rate['qn']: rate['desc']
                     for rate in rates.json()['data']['quality_description']}
        qn = max(liverates)

        # 推流链接
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.live.bilibili.com/room/v1/Room/playUrl?cid={self.id}&quality={qn}&platform=web",
                timeout=10, headers=self._headers
            )
        url = response.json()['data']['durl'][0]['url']
        return url

    async def record(self):
        if not self.onair:
            logger.info(f'{self.code} is not on air.')
            return

        self.isRecording = True
        # url, temppath = await self.preRecording()
        # dsize, starttime, endtime = await self.recording(url, temppath)
        # await self.postRecording(temppath, dsize, starttime, endtime)
        # self.isRecording = False
        # self.recordTask = None

        #! 录前准备
        url = await self._getLiveUrl()
        notifyTask = asyncio.create_task(self.notifyAtBeginning())

        if not os.path.isdir(self._tmpfolder):
            os.mkdir(self._tmpfolder)

        filename = '{id}-{username}-{starttime}-{endtime}-{title}.tmp.flv'.format(
            id=self.id,
            username=self._username,
            starttime=time.strftime('%y%m%d%H%M%S'),
            endtime='{endtime}',
            title=self._livetitle
        )

        # 防止标题和用户名中含有windows路径的非法字符
        filename = re.sub(r'[\<\>\:\"\\\'\\\/\|\?\*]', '', filename)
        temppath = os.path.join(self._tmpfolder, filename)

        #! 录制中
        self.recorder = Recorder(
            url=url,
            savepath=temppath,
            threadid=self.code,
            roomid=self.id
        )
        sttime = time.time()
        try:
            await self.recorder.record()
        except asyncio.CancelledError:
            logger.info(f"{self.code}: task cancelled.")
        else:
            logger.info(f"{self.code}: live terminated.")
        endtime = time.time()

        #! 录制结束
        notifyTask.cancel()
        datasize = self.recorder.downloaded
        del self.recorder
        self.recorder = None
        path = temppath
        logger.info(
            f'{self.code}: recorder stopped, {_dataunitConv(datasize)} downloaded.')

        if datasize < 65536:  # 64KB
            os.remove(path)  # 删除过小的文件
        else:
            await self.notifyAtEnd(endtime-sttime, datasize)
            # note live history
            st = _dividePeriod(sttime)
            end = _dividePeriod(endtime)
            if st > end:
                end += 144
            for i in range(st, end):
                self.history[i % 144] += 1

            if not os.path.isdir(self._savefolder):
                os.mkdir(self._savefolder)

            temppath = path.format(endtime=time.strftime(
                '%H%M%S', time.localtime(endtime)))
            saveto = os.path.join(self._savefolder, os.path.basename(temppath))
            os.rename(path, saveto)
        logger.info(f'{self.code}: postprocessing completed')

    async def notifyAtBeginning(self):
        pass

    async def notifyAtEnd(self, duration, filesize):
        pass

    async def notifyAtBeginningWrapped(self):
        pass

    @classmethod
    def setNotification(cls, barkurl):
        async def pushMessage(title, msg):
            logger.info(
                f"sending message to {barkurl}\ntitle: {title}\ncontent:\n{msg}")
            try:
                async with httpx.AsyncClient() as client:
                    req = await client.post(barkurl, data={
                        "title": title,
                        "body": msg,
                        "group": "recorder"
                    }, timeout=10)
            except:
                logger.exception(
                    "error occurred when sending message.", exc_info=True)
            else:
                logger.info(f"received data: {req.text}")

        async def notifyAtBeginning(self):
            try:
                await asyncio.sleep(5)
                # 5秒后检查是否还在录制
                if self.isRecording:
                    await pushMessage("录播姬", startNotice.format(
                        name=self._username or self.code,
                        id=self.id,
                        title=self._livetitle
                    ))
            except asyncio.CancelledError:
                pass

        async def notifyAtEnd(self, duration, filesize):
            try:
                await pushMessage("录播姬", endNotice.format(
                    name=self._username or self.code,
                    id=self.id,
                    title=self._livetitle,
                    filesize=_dataunitConv(filesize),
                    duration=_formatDuration(duration)
                ))
            except asyncio.CancelledError:
                pass

        cls.notifyAtBeginning = notifyAtBeginning
        cls.notifyAtEnd = notifyAtEnd

    async def report(self):
        interval = 10
        if self.isRecording:
            logger.info(
                f'{self.code}: {_dataunitConv(self.recorder.downloaded)} downloaded.')
        else:
            logger.info(f'{self.code}: updating status')
            try:
                await self._updateStatus()
            except:
                logger.error(
                    f'{self.code}: Exception encountered, retry after 10s.')
            else:
                if self.onair:
                    logger.info(f'{self.code}: creating coroutine.')
                    self.recordTask = asyncio.create_task(self.record())
                else:
                    interval = self.updateInterval
                    logger.info(
                        f'{self.code}: next update is scheduled in {interval:.3f} sec.')
        return interval
