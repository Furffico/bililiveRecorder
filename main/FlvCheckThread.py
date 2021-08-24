from queue import Queue
import threading
import logging
import time
import os

from .flv_checker import Flv

logger = logging.getLogger('postprocess')

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
                    logger.info(f'task finished:{temppath} -> {saveto}')
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
