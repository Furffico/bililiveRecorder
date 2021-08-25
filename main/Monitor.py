import logging
import time
import os
import pickle
from queue import PriorityQueue
import threading

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
        if len(rooms) == 0:
            raise Exception('list for Liverooms is empty')
        self.rooms = rooms
        createFlvcheckThreads(flvcheckercount, historypath)
        self.cleanTerminate = cleanTerminate
        self.historypath = historypath
        self.event = threading.Event()

    def run(self):
        logger.info('monitor thread running')
        q = PriorityQueue()
        t = time.time()+3
        for index in range(len(self.rooms)):
            q.put((t, index))
        logger.info('The process will begin after 3 seconds')

        while not self.event.is_set():
            schedule, roomindex = q.get()
            t = time.time()
            if t < schedule:
                self.event.wait(schedule-t)
                if self.event.is_set():
                    break
            
            room = self.rooms[roomindex]
            try:
                interval = room.report()
            except Exception as e:
                logger.exception(f'room{room.id}: exception occurred')
                logger.info(f'room{room.id}: retry after 60 seconds.')
                interval = 60
            q.put((time.time()+interval, roomindex))
            self.event.wait(0.1)

        logger.info('monitor thread stopped')

    def shutdown(self, signalnum, frame):
        self.event.set()
        logger.info('Program terminating')
        Recorder.onexit()
        if self.cleanTerminate:
            logger.info('waiting for flvcheck thread')
            FlvCheckThread.q.join()
        FlvCheckThread.onexit()

        logger.info('Storing history')
        if self.historypath:
            his = os.path.join(self.historypath, 'history.pkl')
            if os.path.isfile(his):
                with open(his, 'rb') as f:
                    OrgHistory = pickle.load(f)
            else:
                OrgHistory = {}

            for r in self.rooms:
                OrgHistory[r.id] = r.history
            with open(his, 'wb') as f:
                pickle.dump(OrgHistory, f)
            l = list(FlvCheckThread.getQueue())
            if l:
                logger.info('Remaining FlvCheck tasks:\n' +
                            '\n'.join((f"    {i} -> {j}" for i, j in l)))
            with open(os.path.join(self.historypath, 'queue.pkl'), 'wb') as f:
                pickle.dump(l, f)
            
        logger.info('Program terminated')
