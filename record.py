import requests
import os
from datetime import datetime
import threading
import time
from flv_checker import Flv
from configparser import ConfigParser
import signal


def dataunitConv(size):  # n in bytes
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
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Origin': 'https://live.bilibili.com',
            'Referer': 'https://live.bilibili.com/blanc/{}'.format(self.id),
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0',
        }
        self.recordThread = None
        self.lastUpdate = datetime(2000, 1, 1, 10, 0, 0)
        self.savefolder = savefolder or 'common'
        self.updateInterval = updateInterval

    def updateStatus(self):
        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info?id={}".format(
                self.id),
            timeout=10, headers=self.headers
        ).json()

        self.roomInfo = {
            key: response['data'][key]
            for key in ['room_id', 'live_status', 'title', 'description', 'uid']
        }
        self.onair = self.roomInfo['live_status'] == 1

        self.roomInfo['update_time'] = datetime.now()
        self.lastUpdate = self.roomInfo['update_time']

    def getLiveUrl(self):
        # self.updateStatus()
        if not self.onair:
            self.log('当前没有在直播')
            return None

        response = requests.get(
            "https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={}".format(
                self.id),
            timeout=10, headers=self.headers
        ).json()
        self.roomInfo['user_name'] = response['data']['info']['uname']

        rates = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/playUrl?cid={}&quality=0&platform=web".format(
                self.roomInfo['room_id']),
            timeout=10, headers=self.headers
        ).json()['data']['quality_description']
        self.roomInfo['live_rates'] = {
            rate['qn']: rate['desc'] for rate in rates}
        qn = max(self.roomInfo['live_rates'])

        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/playUrl?cid={}&quality={}&platform=web".format(
                self.roomInfo['room_id'], qn),
            timeout=10, headers=self.headers
        ).json()
        url = response['data']['durl'][0]['url']
        realqn = response['data']['current_qn']
        self.log("申请清晰度 %s的链接，得到清晰度 %d的链接" % (qn, realqn))
        return url

    def recordthis(self):
        if self.onair:
            url = self.getLiveUrl()
            savepath = os.path.join(
                config['BASIC']['saveroot'], self.savefolder)
            if not (os.path.exists(savepath) and os.path.isdir(savepath)):
                os.mkdir(savepath)
            filename = '{room_id}{user_name}-{time}-{endtime}-{title}.flv'.format(
                **self.roomInfo, time=datetime.now().strftime('%y%m%d%H%M%S'), endtime='{endtime}')
            self.recordThread = Recorder(
                self.id, self.roomInfo, url, filename, savepath)
        else:
            return None
        self.recordThread.start()

    def log(self, *texts):
        print('[{}][id:{}]'.format(datetime.now(), self.id), *texts)

    def report(self):
        if self.recordThread:
            if self.recordThread.isRecording():
                self.log('{} downloaded.'.format(
                    dataunitConv(self.recordThread.downloaded)))
            else:
                self.recordThread = None
        else:
            now = datetime.now()
            if (now-self.lastUpdate).seconds > self.updateInterval:
                self.log('updating status.')
                self.updateStatus()
                self.log('status updated.')
                if self.onair:
                    self.recordthis()
                    self.log('start recording.')


class Monitor:
    def __init__(self, rooms):
        self.rooms = rooms
        self.running = True

    def run(self):
        print('monitor thread running')
        stopped = []
        while self.running:
            start = datetime.now()
            for room in self.rooms:
                room.report()
            while self.running and (datetime.now()-start).seconds < 10:
                time.sleep(0.05)
        print('monitor thread stopped')

    def shutdown(self, signalnum, frame):
        self.running = False
        for i in self.rooms:
            if i.recordThread:
                i.recordThread.stopRecording()
        for i in self.rooms:
            if i.recordThread:
                i.recordThread.join()


class Recorder(threading.Thread):
    def __init__(self, roomid, roomInfo, url, filename, savedir):
        super().__init__()
        self.roomid = roomid
        self._downloading = False
        self.downloaded = 0
        self.live_url = url
        self.roomInfo = roomInfo
        self.filename = filename
        self.savedir = savedir

    def run(self):
        self.log('start running recording thread')
        try:
            self.record()
        except e:
            self.log('exception occurred:', e)
        self.log('recording thread terminated')

    def record(self):
        self._downloading = True

        temppath = os.path.join(config['BASIC']['temppath'], self.filename)
        with open(temppath, "wb") as file:
            response = requests.get(
                self.live_url, stream=True,
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
            except e:
                pass
            finally:
                self.log('停止录制')
                response.close()
                self._downloading = False

            saveto = os.path.join(self.savedir, self.filename.format(
                endtime=datetime.now().strftime('%H%M%S')))
            self.log("正在校准时间戳")
            flv = Flv(temppath, saveto, False)
            flv.check()
            os.remove(temppath)

    def isRecording(self):
        return self._downloading

    def stopRecording(self):
        self.log('Exiting...')
        self._downloading = False

    def log(self, *texts):
        print('[{}][id:{}][recorder]'.format(
            datetime.now(), self.roomid), *texts)


def readconfig(path='config.ini'):
    global config
    config = ConfigParser()
    config.read(path)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    readconfig('config.ini')
    r = []
    for key in config.sections():
        if key == 'BASIC':
            continue
        item = config[key]
        if item.getboolean('activated', True):
            r.append(LiveRoom(int(item.get('roomid')),
                              item.get('savefolder', key)))
    monitor = Monitor(r)
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()
    monitor.shutdown(None, None)
