import logging
import os
import re
import signal
import threading
import time
from configparser import ConfigParser
import datetime
from queue import Queue
import pickle

import requests

from flv_checker import Flv


class LiveRoom():
    def __init__(self, roomid, code, savefolder=None, updateInterval=60):
        global OrgHistory

        self.id = roomid
        self._roomInfo = {}
        self._username = None
        self.code = code
        self.onair = False

        self.recordThread = None
        self._lastUpdate = datetime.datetime(2000, 1, 1, 10, 0, 0)
        self._baseUpdateInterval = updateInterval
        self._savefolder = savefolder or 'common'

        if code not in OrgHistory:
            OrgHistory[code] = [0]*144
        self.history = OrgHistory[code]
        
    @property
    def _headers(self):
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Origin': 'https://live.bilibili.com',
            'Referer': 'https://live.bilibili.com/blanc/{}'.format(self.id),
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
        logger.info(f'room-{self.code}: Retrieved username {self._username}')

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
        self._lastUpdate = datetime.datetime.now()

    def _getLiveUrl(self):
        # 获取推流链接
        if not self.onair:
            logger.info(f'room-{self.code} is not on air.')
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
        return url

    def startRecording(self):
        if not self.onair:
            logger.info(f'room-{self.code} is not on air.')
            return None
        if not self._username:
            self._getUserName()
        url = self._getLiveUrl()
        savepath = os.path.join(SAVEDIR, self._savefolder)
        if not os.path.exists(savepath) or not os.path.isdir(savepath):
            os.mkdir(savepath)
        filename = '{room_id}-{username}-{time}-{endtime}-{title}'.format(
            **self._roomInfo, username=self._username, time=datetime.datetime.now().strftime('%y%m%d%H%M%S'), endtime='{endtime}',)
        # 防止标题和用户名中含有windows路径的非法字符
        filename = re.sub(r'[\<\>\:\"\\\'\\\/\|\?\*\.]', '', filename)+'.flv'
        self.recordThread = Recorder(
            self.id, self._roomInfo, url, filename, savepath, self.code)
        self.recordThread.start()

    def report(self):
        if self.recordThread:
            if self.recordThread.isRecording():
                delta = datetime.datetime.now()-self._lastUpdate
                if delta.seconds > 60 or delta.days > 0:
                    logger.info('room-{}: {} downloaded.'.format(
                        self.code, dataunitConv(self.recordThread.downloaded)))
                    self._lastUpdate = datetime.datetime.now()
            else:
                del self.recordThread
                self.recordThread = None
        else:
            delta = datetime.datetime.now()-self._lastUpdate
            interval = self.updateInterval
            if delta.seconds > interval or delta.days > 0:
                logger.info(
                    f'room-{self.code}: updating status with interval {interval:.3f}s.')
                try:
                    self._updateStatus()
                except requests.exceptions:
                    logger.error(
                        f'room-{self.code}: Requests\' exception encountered, retry after 60s.')
                    self._lastUpdate += datetime.timedelta(seconds=60)
                else:
                    self._lastUpdate = datetime.datetime.now()
                    logger.info(f'room-{self.code}: status updated.')
                    if self.onair:
                        logger.info(f'room-{self.code}: start recording.')
                        self.startRecording()

    @property
    def updateInterval(self):
        t = dividePeriod(time.time())
        return 300*(self._baseUpdateInterval /
                300)**(self.history[t]/max(1, *self.history))


class Recorder(threading.Thread):
    runningThreads = {}

    def __init__(self, roomid, roomInfo, url, filename, savedir, code):
        super().__init__()
        self.roomid = roomid
        self._url = url
        self.code = code
        self._filename = filename
        self._savedir = savedir

        self._downloading = False
        self.downloaded = 0
        

    def run(self):
        logger.info(f'recorder-{self.code}: start recording thread')
        Recorder.runningThreads[(self.roomid, self._filename)] = self
        try:
            self._record()
        except Exception as e:
            logger.exception(f'recorder-{self.code}: exception occurred')
        del Recorder.runningThreads[(self.roomid, self._filename)]
        logger.info(f'recorder-{self.code}: recording thread terminated')

    @classmethod
    def onexit(cls):
        rt = list(cls.runningThreads.values())
        for i in rt:
            i.stopRecording()
        for i in rt:
            i.join()

    def _record(self):
        self._downloading = True

        temppath = os.path.join(TEMPDIR, self._filename)
        starttime = datetime.datetime.now()
        with open(temppath, "wb") as file:
            response = requests.get(
                self._url, stream=True,
                headers={
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
                    'Origin': 'https://live.bilibili.com',
                    'Referer': f'https://live.bilibili.com/{self.roomid}',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0',
                },
                timeout=300)
            try:
                for data in response.iter_content(chunk_size=1048576):
                    if not self._downloading:
                        break
                    if data:
                        file.write(data)
                        self.downloaded += len(data)
            except Exception as e:
                logger.exception(f'recorder-{self.code}: exception occurred')
            finally:
                logger.info(f'recorder-{self.code}: stop recording')
                response.close()
                self._downloading = False

        if self.downloaded < 65536:  # 64KB
            os.remove(temppath) # 删除过小的文件
        else:
            # note live history
            st = dividePeriod(starttime.timestamp())
            end = dividePeriod(time.time())
            if st > end:
                end += 144
            for i in range(st, end):
                OrgHistory[self.code][i % 144] += 1

            saveto = os.path.join(self._savedir, self._filename.format(
                endtime=datetime.datetime.now().strftime('%H%M%S')))
            logger.info(f'recorder-{self.code}: enqueue FlvCheck task.')
            FlvCheckThread.addTask(temppath, saveto)

    def isRecording(self):
        return self._downloading

    def stopRecording(self):
        logger.info(f'recorder-{self.code}: Exiting...')
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
        logger.info(f'FlvCheckThread started.')
        while not self.blocked:
            if self.q.empty():
                time.sleep(1)
                continue
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
        logger.info(f'FlvCheckThread terminated.')

    @classmethod
    def addTask(cls, temppath, saveto):
        cls.q.put((temppath, saveto))

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
        with open(os.path.join(HISTORYPATH,'history.pkl'), 'wb') as f:
            pickle.dump(OrgHistory, f)
        l = list(FlvCheckThread.getQueue())
        if l:
            logger.info('Remaining FlvCheck tasks:\n' +
                        '\n'.join((f"    {i} -> {j}" for i, j in l)))
        with open(os.path.join(HISTORYPATH,'queue.pkl'), 'wb') as f:
            pickle.dump(l, f)
        logger.info('Program terminated')


#! supporting functions ========================

def dividePeriod(dt): # 将timestamp转换为时段的编号
    return int(dt) % 86400//600

def dataunitConv(size: int):  # size in bytes
    if not size:
        return '0'
    n = int(size)
    magnitude = -1
    units = ['bytes', 'KB', 'MB', 'GB', 'TB']
    while n:
        n >>= 10
        magnitude += 1
    return '{:.2f} {}'.format(size/(1 << magnitude*10), units[magnitude])


def readconfig(path='config.ini'):
    global config, OrgHistory,TEMPDIR,SAVEDIR,HISTORYPATH
    config = ConfigParser()
    config.read(path)

    HISTORYPATH=os.getenv('RECORDER_HISTORYDIR') or config['BASIC'].get('history', './')
    TEMPDIR=os.getenv('RECORDER_TEMPDIR') or config['BASIC']['temppath']
    SAVEDIR=os.getenv('RECORDER_SAVEDIR') or config['BASIC']['saveroot']
    if not os.path.isdir(HISTORYPATH):
        os.mkdir(HISTORYPATH)
    if not os.path.isdir(TEMPDIR):
        os.mkdir(TEMPDIR)
    if not os.path.isdir(SAVEDIR):
        os.mkdir(SAVEDIR)

    # 读取开播历史
    hispath = os.path.join(HISTORYPATH,'history.pkl')
    if hispath and os.path.isfile(hispath):
        with open(hispath, 'rb') as f:
            OrgHistory = pickle.load(f)
    else:
        OrgHistory = {}

    # 创建时间戳校准进程
    for _ in range(config['BASIC'].get('flvcheckercount', 1)):
        a = FlvCheckThread()
        a.start()

    # 读取未完成的时间戳校准
    queuepath=os.path.join(HISTORYPATH,'queue.pkl')
    if os.path.isfile(queuepath):
        with open(queuepath, 'rb') as f:
            unfinished = pickle.load(f)
        for temppath, saveto in unfinished:
            if os.path.isfile(temppath):
                logger.info(
                    f'Enqueue unfinished FlvCheck task:\n    {temppath} -> {saveto}')
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


def setlogger(level=logging.INFO,filepath=None,filelevel=logging.WARNING):
    global logger
    logger = logging.getLogger('recorder')
    logger.setLevel(level)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    handler1 = logging.StreamHandler()
    handler1.setFormatter(formatter)
    handler1.setLevel(level)
    logger.addHandler(handler1)

    if filepath:
        with open(filepath, 'a') as f:
            f.write('\n\n\n')
        handler2 = logging.FileHandler(filename=filepath)
        handler2.setFormatter(formatter)
        handler2.setLevel(filelevel)
        logger.addHandler(handler2)
    
    logger.info('Program started')


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    setlogger(filepath='./warning.log')
    r = readconfig('config.ini')
    if not r:
        logger.warning('NO activated room found in config.ini, program terminated')
        quit()

    monitor = Monitor(r)
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()