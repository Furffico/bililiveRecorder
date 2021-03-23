import logging
import os
import re
import signal
import threading
import time
from configparser import ConfigParser
from datetime import datetime

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
    def __init__(self, roomid, savefolder=None, updateInterval=60):
        self.id = roomid
        self.onair = False
        self.recordThread = None
        self._headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Origin': 'https://live.bilibili.com',
            'Referer': 'https://live.bilibili.com/blanc/{}'.format(self.id),
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0',
        }
        self._lastUpdate = datetime(2000, 1, 1, 10, 0, 0)
        self._savefolder = savefolder or 'common'
        self._baseUpdateInterval = updateInterval
        self._roomInfo={}
        self._username=None
    
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
            **self._roomInfo,username=self._username,time=datetime.now().strftime('%y%m%d%H%M%S'), endtime='{endtime}',)
        # 防止标题和用户名中含有windows路径的非法字符
        filename = re.sub(r'[\<\>\:\"\\\'\\\/\|\?\*\.]', '', filename)+'.flv'
        self.recordThread = Recorder(self.id, self._roomInfo, url, filename, savepath)
        self.recordThread.start()

    def report(self):
        if self.recordThread:
            if self.recordThread.isRecording():
                delta = datetime.now()-self._lastUpdate
                if delta.seconds>60 or delta.days > 0:
                    logger.info('room{}: {} downloaded.'.format(self.id,dataunitConv(self.recordThread.downloaded)))
                    self._lastUpdate=datetime.now()
            else:
                self.recordThread = None  # 如果录制已停止则不再监控recorder
        else:
            delta = datetime.now()-self._lastUpdate
            if delta.seconds > self.updateInterval or delta.days > 0:
                logger.info(f'room{self.id}: updating status.')
                self._updateStatus()
                logger.info(f'room{self.id}: status updated.')
                if self.onair:
                    logger.info(f'room{self.id}: start recording.')
                    self.startRecording()

    @property
    def updateInterval(self):
        if config['BASIC'].getboolean('overrideschedule',False):
            return self._baseUpdateInterval
        now = datetime.now()
        if now.weekday() <= 4:  # 周一到周五
            if 2 <= now.hour <= 19:
                return self._baseUpdateInterval*4
        else:  # 周六周日
            if 4 <= now.hour <= 14:
                return self._baseUpdateInterval*2
        return self._baseUpdateInterval


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
            time.sleep(0.05)
        logger.info('monitor thread stopped')

    def shutdown(self, signalnum, frame):
        self.running = False
        logger.info('Program terminating')
        rt=list(Recorder.runningThreads.values())
        for i in rt:
            i.stopRecording()
        logger.info('Waiting for timestamp adjustments to complete')
        for i in rt:
            i.join()
        logger.info('Program terminated')


class Recorder(threading.Thread):
    runningThreads={}
    
    def __init__(self, roomid, roomInfo, url, filename, savedir):
        super().__init__()
        self.roomid = roomid
        self.downloaded = 0
        self._url = url
        self._filename = filename
        self._savedir = savedir
        self._downloading = False
    
    def run(self):
        logger.info(f'recorder{self.roomid}: start running recording thread')
        self._addself()
        try:
            self._record()
        except Exception as e:
            logger.exception(f'recorder{self.roomid}: exception occurred')
        self._removeself()
        logger.info(f'recorder{self.roomid}: recording thread terminated')
    
    def _addself(self):
        Recorder.runningThreads[(self.roomid,self._filename)]=self
    
    def _removeself(self):
        del Recorder.runningThreads[(self.roomid,self._filename)]
    
    def _record(self):
        self._downloading = True

        temppath = os.path.join(config['BASIC']['temppath'], self._filename)
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

            saveto = os.path.join(self._savedir, self._filename.format(
                endtime=datetime.now().strftime('%H%M%S')))
            logger.info(f'recorder{self.roomid}: 正在校准时间戳')
            flv = Flv(temppath, saveto, False)
            flv.check()
            os.remove(temppath)

    def isRecording(self):
        return self._downloading

    def stopRecording(self):
        logger.info(f'recorder{self.roomid}: Exiting...')
        self._downloading = False


def readconfig(path='config.ini'):
    global config
    config = ConfigParser()
    config.read(path)

    r = []
    for key in config.sections():
        if key == 'BASIC':
            continue
        item = config[key]
        if item.getboolean('activated', True):
            r.append(LiveRoom(item.getint('roomid'), item.get(
                'savefolder', key), item.getint('updateinterval', 120)))
    return r


def setlogger(logpath='warnings.log',level=logging.WARNING):
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
    setlogger()
    r = readconfig('config.ini')
    if not r:
        logger.warning('NO activated room found in config.ini')
        quit()

    monitor = Monitor(r)
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()
    monitor.shutdown(None, None)
    