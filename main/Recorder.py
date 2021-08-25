import threading
import requests
import logging
import time

logger = logging.getLogger('recorder')


class Recorder(threading.Thread):
    runningThreads = {}

    def __init__(self, url, savepath, threadid, room):
        super().__init__()
        self.room = room
        self.roomid = room.id
        self._url = url
        self.threadid = threadid
        self.savepath = savepath

        self._downloading = False
        self.downloaded = 0

    def run(self):
        logger.info(f'{self.threadid}: start recording thread')
        Recorder.runningThreads[(self.roomid, self.savepath)] = self
        try:
            self._record()
        except Exception as e:
            logger.exception(f'{self.threadid}: exception occurred')
        del Recorder.runningThreads[(self.roomid, self.savepath)]
        logger.info(f'{self.threadid}: recording thread terminated')

    @classmethod
    def onexit(cls):
        rt = list(cls.runningThreads.values())
        for i in rt:
            i.stopRecording()
        for i in rt:
            i.join()

    def _record(self):
        self._downloading = True

        starttime = time.time()
        with open(self.savepath, "wb") as file:
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
            except:
                logger.exception(f'{self.threadid}: exception occurred.',exc_info=True)
            finally:
                endtime = time.time()
                logger.info(f'{self.threadid}: stop recording')
                response.close()
                self._downloading = False
                
                self.room.recordingFinished(self.savepath,self.downloaded,starttime,endtime)

    def isRecording(self):
        return self._downloading

    def stopRecording(self):
        logger.info(f'{self.threadid}: Exiting...')
        self._downloading = False
