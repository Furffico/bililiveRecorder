import logging
import time
import os
import pickle

from .FlvCheckThread import FlvCheckThread
from .Recorder import Recorder

logger = logging.getLogger('monitor')


def createFlvcheckThreads(count=1, historypath=None):
    # 创建时间戳校准进程
    for _ in range(count):
        a = FlvCheckThread()
        a.start()

    # 读取未完成的时间戳校准
    if historypath:
        queuepath = os.path.join(historypath, 'queue.pkl')
        if os.path.isfile(queuepath):
            with open(queuepath, 'rb') as f:
                unfinished = pickle.load(f)
            for temppath, saveto in unfinished:
                if os.path.isfile(temppath):
                    logger.info(
                        f'Enqueue unfinished FlvCheck task:\n    {temppath} -> {saveto}')
                    FlvCheckThread.addTask(temppath, saveto)


class Monitor:
    def __init__(self, rooms, flvcheckercount=1, cleanTerminate=False, historypath=None):
        self.rooms = rooms
        self.running = True
        createFlvcheckThreads(flvcheckercount, historypath)
        self.cleanTerminate = cleanTerminate
        self.historypath = historypath

    def run(self):
        logger.info('monitor thread running')
        while self.running:
            for room in self.rooms:
                try:
                    room.report()
                except Exception as e:
                    logger.exception(
                        f'room{room.id}: exception occurred while checking for status')
                time.sleep(0.1)
            time.sleep(0.5)
        logger.info('monitor thread stopped')

    def shutdown(self, signalnum, frame):
        self.running = False
        logger.info('Program terminating')
        Recorder.onexit()
        if self.cleanTerminate:
            logger.info('waiting for flvcheck thread')
            FlvCheckThread.q.join()
        FlvCheckThread.onexit()

        logger.info('Storing history')
        if self.historypath:
            his=os.path.join(self.historypath, 'history.pkl')
            if os.path.isfile(his):
                with open(his, 'rb') as f:
                    OrgHistory=pickle.load(f)
            else:
                OrgHistory={}

            for r in self.rooms:
                OrgHistory[r.id]=r.history
            with open(his, 'wb') as f:
                pickle.dump(OrgHistory, f)
            l = list(FlvCheckThread.getQueue())
            if l:
                logger.info('Remaining FlvCheck tasks:\n' +
                            '\n'.join((f"    {i} -> {j}" for i, j in l)))
            with open(os.path.join(self.historypath, 'queue.pkl'), 'wb') as f:
                pickle.dump(l, f)

        logger.info('Program terminated')
