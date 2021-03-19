import requests
import os
from datetime import datetime
import threading
from time import sleep

class LiveRoom():
    def __init__(self,roomid):
        self.id=roomid
        self.onair=False
        self.headers={
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Origin': 'https://live.bilibili.com',
            'Referer': 'https://live.bilibili.com/blanc/{}'.format(self.id),
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0',
        }
        self.recordThread=None
        self.lastUpdate=datetime(2000,1,1,10,0,0,)
    
    def updateStatus(self):
        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info?id={}".format(self.id),
            timeout=10, headers=self.headers
        ).json()

        self.roomInfo = {
            key:response['data'][key] 
            for key in ['room_id','live_status','title','description','uid']
        }
        self.onair=self.roomInfo['live_status']==1

        self.roomInfo['update_time']=datetime.now()
        self.lastUpdate=self.roomInfo['update_time']

        if self.onair:
            self.recordthis()
    
    def getLiveUrl(self):
        # self.updateStatus()
        if not self.onair:
            print('当前没有在直播')
            return None

        response = requests.get(
            "https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={}".format(self.id),
            timeout=10, headers=self.headers
        ).json()
        self.roomInfo['user_name'] = response['data']['info']['uname']
        
        rates = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/playUrl?cid={}&quality=0&platform=web".format(self.roomInfo['room_id']),
            timeout=10, headers=self.headers
        ).json()['data']['quality_description']
        self.roomInfo['live_rates'] = {rate['qn']:rate['desc'] for rate in rates}
        qn=max(self.roomInfo['live_rates'])
        
        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/playUrl?cid={}&quality={}&platform=web".format(self.roomInfo['room_id'], qn),
            timeout=10, headers=self.headers
        ).json()
        url = response['data']['durl'][0]['url']
        realqn = response['data']['current_qn']
        print("申请清晰度 %s的链接，得到清晰度 %d的链接"%(qn, realqn))
        return url
    
    def recordthis(self):
        global monitor
        if self.onair:
            url=self.getLiveUrl()
            path='{room_id}-{user_name}-{time}-{endtime}-{title}.flv'.format(**self.roomInfo,time=datetime.now().strftime('%y%m%d%H%M%S'),endtime='{endtime}')
            self.recordThread=Recorder(self.id,self.roomInfo,url,path)
        else:
            return None
        
        self.recordThread.start()

class Monitor(threading.Thread):
    def __init__(self,rooms):
        super().__init__()
        self.rooms=rooms
        self.running=True
    
    def run(self):
        print('monitor thread running')
        stopped=[]
        while self.running:
            for room in self.rooms:
                if room.recordThread:
                    th=room.recordThread
                    if th.isRecording():
                        print('[{}][id:{}] {} bytes downloaded.'.format(datetime.now(),th.roomid,th.downloaded))
                    else:
                        room.recordThread=None
                else:
                    now=datetime.now()
                    if (now-room.lastUpdate).seconds>60:
                        print(f'[{now}][id:{room.id}] updating status.')
                        room.updateStatus()
                        print(f'[{now}][id:{room.id}] status updated.','on air' if room.onair else 'not on air')

            sleep(10)
        print('monitor thread stopped')

    
    def addthread(self,th):
        self.threads.append(th)
    
    def terminate(self):
        self.running=False


class Recorder(threading.Thread):
    def __init__(self,roomid,roomInfo,url,path):
        super().__init__()
        self.roomid=roomid
        self._downloading=False
        self.downloaded=0
        self.live_url=url
        self.roomInfo=roomInfo
        self.savepath=path

    def run(self):
        print('start running recording thread')
        try:
            self.record()
        except e:
            print('exception occurred:',e)
        print('recording thread terminated')

    def record(self):
        self._downloading=True
        print('Recording')
        
        with open('./tmp/'+self.savepath,"wb") as file:
            response = requests.get(
                self.live_url, stream=True,
                headers = {
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
                print('停止录制')
                response.close()
                self._downloading=False
            
            saveto=self.savepath.format(endtime=datetime.now().strftime('%y%m%d%H%M%S'))
            print("正在校准时间戳")
            flv = Flv('./tmp/'+self.savepath,saveto ,False)
            flv.check()
            os.remove('./tmp/'+self.savepath)
            print('thread quitted')
    
    def isRecording(self):
        return self._downloading

    def stopRecord(self):
        print('Exiting...')
        self._downloading=False

if __name__ == "__main__":
    r=[LiveRoom(i) for i in [9196015,596082,22571958]]
    monitor=Monitor(r)
    monitor.start()
    monitor.join()
    for i in r:
        if i.recordThread:
            i.recordThread.join()