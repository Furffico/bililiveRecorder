import httpx
import logging
from .flv_checker import Flv

logger = logging.getLogger('recorder')


class AsyncStreamContextManager():
    def __init__(self, method, url, chunk_size=1024, **kwargs):
        self.chunk_size = chunk_size
        self.method = method
        self.url = url
        self.kwargs = kwargs
        self.stream = None

    async def __aenter__(self):
        logger.info("entering awith")
        self.stream = AsyncStreamHandler(
            self.method, self.url, self.chunk_size, **self.kwargs)
        return self.stream

    async def __aexit__(self, *exc_info,):
        logger.info("exiting awith")
        if self.stream:
            return await self.stream.close()


class AsyncStreamHandler():
    def __init__(self, iterator, chunk_size=1024):
        self._pos = 0
        self.absolutepos = 0
        self._chunk_size = chunk_size
        self._this = None
        self.downloaded = 0
        self._iter = iterator

    async def getnextChunk(self):
        i = await self._iter.__anext__()
        self.downloaded += len(i)
        return i

    async def read(self, bytescount):
        if self._this is None:
            try:
                self._this = await self.getnextChunk()
            except StopAsyncIteration:
                return b''
        end = self._pos+bytescount
        if end < len(self._this):
            # 没有超出缓存中的block
            data = self._this[self._pos:end]
            self._pos = end
        else:
            # 超出了缓存中的block
            data = self._this[self._pos:]
            bytesleft = bytescount-len(data)
            required_count = bytesleft // self._chunk_size
            try:
                for _ in range(required_count):
                    # 获取required_count个的block
                    data += await self.getnextChunk()
                # 再获取一个存入this
                self._this = await self.getnextChunk()
            except StopAsyncIteration:
                self._this = None
            else:
                self._pos = bytesleft-required_count*self._chunk_size
                data += self._this[:self._pos]
        self.absolutepos += len(data)
        return data


class Recorder:
    def __init__(self, url, savepath, threadid, roomid):
        self.roomid = roomid
        self._url = url
        self.threadid = threadid
        self.savepath = savepath
        self.stream = None

    async def record(self):
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Origin': 'https://live.bilibili.com',
            'Referer': f'https://live.bilibili.com/{self.roomid}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0',
        }

        logger.info(f"{self.threadid}: start recording.")
        with open(self.savepath, "wb") as file:
            async with httpx.AsyncClient() as client:
                async with client.stream('GET', self._url, headers=headers, timeout=10) as response:
                    iterator = response.aiter_bytes(chunk_size=1048576)
                    self.stream = AsyncStreamHandler(
                        iterator, chunk_size=1048576)
                    flv = Flv()
                    await flv.check(self.stream, file)
            del self.stream
            self.stream = None

    @property
    def downloaded(self):
        if self.stream:
            return self.stream.absolutepos
        else:
            return 0
