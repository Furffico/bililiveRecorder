import logging
import os
import re
import signal
import threading
import time
from configparser import ConfigParser
from datetime import datetime
from queue import Queue
import pickle

import requests

from flv_checker import Flv


def dataunitConv(size):  # size in bytes
    if not size:
        return '0'
    n = size
    magnitude = -1
    units = ['bytes', 'KB', 'MB', 'GB', 'TB']
    while n:
        n >>= 10
        magnitude += 1
    if magnitude:
        return '{n:.2f} {unit}'.format(n=size/(1 << magnitude*10), unit=units[magnitude])
    else:
        return f'{size} bytes'


class LiveRoom():
    def __init__(self, roomid, code, savefolder=None, updateInterval=60):
        self.id = roomid
        self.onair = False
        self.recordThread = None
        self._headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Origin': 'https://live.bilibili.com',
            'Referer': 'https://live.bilibili.com/blanc/{}'.format(self.id),
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0'
        }
        self._lastUpdate = datetime(2000, 1, 1, 10, 0, 0)
        self._savefolder = savefolder or 'common'
        self._baseUpdateInterval = updateInterval
        global OrgHistory
        if code not in OrgHistory:
            OrgHistory[code] = [0]*144
        self.history = OrgHistory[code]
        self._roomInfo = {}
        self.code = code
        self._username = None

    def _getUserName(self):
        # 获取用户名
        response = requests.get(
            "https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={}".format(
                self.id),
            timeout=10, headers=self._headers
        ).json()
        self._username = response['data']['info']['uname']
        logger.info(f'room{self.id} 获得直播间对应主播的用户名：{self._username}')

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
        self._lastUpdate = datetime.now()

    def _getLiveUrl(self):
        # 获取推流链接
        if not self.onair:
            logger.info(f'room{self.id} 当前没有在直播')
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
        realqn = response['data']['current_qn']
        logger.info("room%i: 申请清晰度 %s的链接，得到清晰度 %d的链接" % (self.id, qn, realqn))
        return url

    def startRecording(self):
        if not self.onair:
            logger.info(f'room{self.id} 当前没有在直播')
            return None
        if not self._username:
            self._getUserName()
        url = self._getLiveUrl()
        savepath = os.path.join(config['BASIC']['saveroot'], self._savefolder)
        if not os.path.exists(savepath) or not os.path.isdir(savepath):
            os.mkdir(savepath)
        filename = '{room_id}-{username}-{time}-{endtime}-{title}'.format(
            **self._roomInfo, username=self._username, time=datetime.now().strftime('%y%m%d%H%M%S'), endtime='{endtime}',)
        # 防止标题和用户名中含有windows路径的非法字符
        filename = re.sub(r'[\<\>\:\"\\\'\\\/\|\?\*\.]', '', filename)+'.flv'
        self.recordThread = Recorder(
            self.id, self._roomInfo, url, filename, savepath, self.code)
        self.recordThread.start()

    def report(self):
        if self.recordThread:
            if self.recordThread.isRecording():
                delta = datetime.now()-self._lastUpdate
                if delta.seconds > 60 or delta.days > 0:
                    logger.info('room{}: {} downloaded.'.format(
                        self.id, dataunitConv(self.recordThread.downloaded)))
                    self._lastUpdate = datetime.now()
            else:
                self.recordThread = None  # 如果录制已停止则不再监控recorder
        else:
            delta = datetime.now()-self._lastUpdate
            if delta.seconds > self.updateInterval or delta.days > 0:
                logger.info(f'room{self.id}: updating status.')
                self._lastUpdate = datetime.now()
                self._updateStatus()
                logger.info(f'room{self.id}: status updated.')
                if self.onair:
                    logger.info(f'room{self.id}: start recording.')
                    self.startRecording()

    @property
    def updateInterval(self):
        if config['BASIC'].getboolean('overrideschedule', False):
            return self._baseUpdateInterval
        t = dividePeriod(time.time())
        interval = 600*(self._baseUpdateInterval /
                        600)**(self.history[t]/max(1, *self.history))
        # print(interval)
        return interval


class Monitor:
    def __init__(self, rooms):
        self.rooms = rooms
        self.running = True

    def run(self):
        logger.info('monitor thread running')
        stopped = []
        while self.running:
            for room in self.rooms:
                try:
                    room.report()
                except Exception as e:
                    logger.exception(f'room{room.id}: exception occurred')
            time.sleep(0.1)
        logger.info('monitor thread stopped')

    def shutdown(self, signalnum, frame):
        self.running = False
        logger.info('Program terminating')
        FlvCheckThread.onexit()
        Recorder.onexit()

        logger.info('Storing history')
        global OrgHistory
        with open(config['BASIC']['history'], 'wb') as f:
            pickle.dump(OrgHistory, f)
        l = list(FlvCheckThread.getQueue())
        logger.info('未完成的时间戳调整任务：\n' +
                    '\n'.join((f"    {i} -> {j}" for i, j in l)))
        with open('./queue.pkl', 'wb') as f:
            pickle.dump(l, f)
        logger.info('Program terminated')


class Recorder(threading.Thread):
    runningThreads = {}

    def __init__(self, roomid, roomInfo, url, filename, savedir, code):
        super().__init__()
        self.roomid = roomid
        self.downloaded = 0
        self._url = url
        self._filename = filename
        self._savedir = savedir
        self._downloading = False
        self.code = code

    def run(self):
        logger.info(f'recorder{self.roomid}: start running recording thread')
        Recorder.runningThreads[(self.roomid, self._filename)] = self
        try:
            self._record()
        except Exception as e:
            logger.exception(f'recorder{self.roomid}: exception occurred')
        del Recorder.runningThreads[(self.roomid, self._filename)]
        logger.info(f'recorder{self.roomid}: recording thread terminated')

    @classmethod
    def onexit(cls):
        rt = list(cls.runningThreads.values())
        for i in rt:
            i.stopRecording()
        for i in rt:
            i.join()

    def _record(self):
        self._downloading = True

        temppath = os.path.join(config['BASIC']['temppath'], self._filename)
        starttime = datetime.now()
        with open(temppath, "wb") as file:
            response = requests.get(
                self._url, stream=True,
                headers={
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
                    'Origin': 'https://live.bilibili.com',
                    'Referer': 'https://live.bilibili.com/{}'.format(self.roomid),
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0',
                },
                timeout=120)
            try:
                for data in response.iter_content(chunk_size=1024*1024):
                    if not self._downloading:
                        break
                    if data:
                        file.write(data)
                        self.downloaded += len(data)
            except Exception as e:
                logger.exception(f'recorder{self.roomid}: exception occurred')
            finally:
                logger.info(f'recorder{self.roomid}: 停止录制')
                response.close()
                self._downloading = False

            st = dividePeriod(starttime.timestamp())
            end = dividePeriod(time.time())
            if st > end:
                end += 144
            for i in range(st, end+1):
                OrgHistory[self.code][i % 144] += 1

            saveto = os.path.join(self._savedir, self._filename.format(
                endtime=datetime.now().strftime('%H%M%S')))
            logger.info(f'recorder{self.roomid}: 添加任务至时间戳校准队列')
            FlvCheckThread.addTask(temppath, saveto)

    def isRecording(self):
        return self._downloading

    def stopRecording(self):
        logger.info(f'recorder{self.roomid}: Exiting...')
        self._downloading = False


class FlvCheckThread(threading.Thread):
    q = Queue()
    threads = []
    blocked = False

    def __init__(self):
        super().__init__()
        self.threads.append(self)
        self.flv = None

    def run(self):
        if self.blocked:
            return
        logger.info(f'时间戳校准进程已启动')
        while not self.blocked and not self.q.empty():
            temppath, saveto = self.q.get()
            self.flv = Flv(temppath, saveto)
            try:
                self.flv.check()
            except Exception as e:
                logger.info(f'Error occurred while processing {temppath}: {e}')
            else:
                if self.flv.keepRunning:
                    os.remove(temppath)
                    self.q.task_done()
                else:
                    os.remove(saveto)
                    self.q.put((temppath, saveto))
        logger.info(f'时间戳校准进程结束')

    @classmethod
    def addTask(cls, temppath, saveto):
        cls.q.put((temppath, saveto))
        if not cls.blocked:
            for th in cls.threads:
                if not th.is_alive():
                    th.start()
                    break

    @classmethod
    def onexit(cls):
        cls.blocked = True
        for th in cls.threads:
            if th.flv:
                th.flv.keepRunning = False
        for th in cls.threads:
            if th.is_alive():
                th.join()

    @classmethod
    def getQueue(cls):
        while not cls.q.empty():
            yield cls.q.get()


def dividePeriod(dt):
    return int(dt) % 86400//600


def readconfig(path='config.ini'):
    global config, OrgHistory
    config = ConfigParser()
    config.read(path)

    # 读取开播历史
    hispath = config['BASIC'].get('history', None)
    if hispath and os.path.isfile(hispath):
        with open(hispath, 'rb') as f:
            OrgHistory = pickle.load(f)
    else:
        OrgHistory = {}

    # 创建时间戳校准进程
    for _ in range(config['BASIC'].get('flvcheckercount', 1)):
        FlvCheckThread()

    # 读取未完成的时间戳校准
    if os.path.isfile('./queue.pkl'):
        with open('./queue.pkl', 'rb') as f:
            unfinished = pickle.load(f)
            for temppath, saveto in unfinished:
                if os.path.isfile(temppath):
                    logger.info(
                        f'将之前未完成的时间戳校准加入队列：\n    {temppath} -> {saveto}')
                    FlvCheckThread.addTask(temppath, saveto)

    # 读取房间配置
    r = []
    for key in config.sections():
        if key == 'BASIC':
            continue
        item = config[key]
        if item.getboolean('activated', True):
            r.append(LiveRoom(item.getint('roomid'), key, item.get(
                'savefolder', key), item.getint('updateinterval', 120))
            )
    return r


def setlogger(logpath='warnings.log', level=logging.WARNING):
    global logger
    with open(logpath, 'a') as f:
        f.write('\n\n\n')
    logger = logging.getLogger('recorder')
    logger.setLevel(level)
    handler1 = logging.StreamHandler()
    handler2 = logging.FileHandler(filename=logpath)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler1.setFormatter(formatter)
    handler2.setFormatter(formatter)
    handler1.setLevel(level)
    handler2.setLevel(level)
    logger.addHandler(handler1)
    logger.addHandler(handler2)
    logger.info('Program started')


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    setlogger('info.log', logging.INFO)
    # setlogger()
    r = readconfig('config.ini')
    if not r:
        logger.warning('NO activated room found in config.ini')
        quit()

    monitor = Monitor(r)
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()