import asyncio
import logging
import os
import pickle
from queue import PriorityQueue
import signal
import time

logger = logging.getLogger('monitor')


class Monitor:
    def __init__(self, rooms, historypath=None):
        if len(rooms) == 0:
            raise Exception('list for Liverooms is empty')
        self.rooms = rooms
        self.historypath = historypath
        self.running = True
        self.sleeptask = None

    def run(self):
        for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
            signal.signal(sig, self.cleanup)
        asyncio.run(self.mainloop())

    async def mainloop(self):
        logger.info('main thread running')

        q = PriorityQueue()
        t = time.time()
        for index in range(len(self.rooms)):
            t += 3
            q.put((t, index))
        logger.info('The process will begin after 3 seconds')

        while self.running:
            schedule, roomindex = q.get()
            t = time.time()
            if t < schedule:
                try:
                    self.sleeptask = asyncio.create_task(
                        asyncio.sleep(schedule-t))
                    await self.sleeptask
                except asyncio.CancelledError:
                    break
                self.sleeptask = None

            room = self.rooms[roomindex]
            try:
                interval = await room.report()
            except Exception as e:
                logger.exception(
                    f'room{room.id}: exception occurred, retry after 60 seconds.')
                interval = 60
            except asyncio.CancelledError:
                break
            q.put((time.time()+interval, roomindex))

        await asyncio.sleep(0.4)
        logger.info('main thread stopped')

    def cleanup(self, a, b):
        logger.info('Program terminating')
        for r in self.rooms:
            if r.recordTask:
                r.running = False
                r.recordTask.cancel()

        self.running = False
        if self.sleeptask:
            self.sleeptask.cancel()

        if self.historypath:
            logger.info('Storing history')
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
