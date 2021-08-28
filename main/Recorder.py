import httpx
import logging
from .flv_checker import Flv
import asyncio

logger = logging.getLogger('recorder')


class AsyncStreamHandler():
    def __init__(self, iterator, chunk_size=1024):
        self._pos = 0
        self.absolutepos = 0
        self._chunk_size = chunk_size
        self._this = None
        self.downloaded = 0
        self._iter = iterator

    async def getnextChunk(self):
        i = await asyncio.wait_for(self._iter.__anext__(), timeout=5)  # 等待五秒钟
        self.downloaded += len(i)
        return i

    async def read(self, bytescount):
        if self._this is None:
            try:
                self._this = await self.getnextChunk()
            except (StopAsyncIteration, asyncio.TimeoutError):
                return b''
        end = self._pos+bytescount
        if end < len(self._this):
            # 没有超出缓存中的block
            data = self._this[self._pos:end]
            self._pos = end
        else:
            # 超出了缓存中的block
            data = self._this[self._pos:]
            self._pos = bytescount-len(data)
            try:
                self._this = await self.getnextChunk()
            except (StopAsyncIteration, asyncio.TimeoutError):
                self._this = None
            else:
                data += self._this[:self._pos]
        self.absolutepos += len(data)
        return data

    async def directWrite(self, bytescount, dest):
        if self._this is None:
            try:
                self._this = await self.getnextChunk()
            except StopAsyncIteration:
                return b''
        end = self._pos + bytescount
        if end < len(self._this):
            # 没有超出缓存中的block
            dest.write(self._this[self._pos:end])
            self._pos = end
            count = bytescount
        else:
            # 超出了缓存中的block
            dest.write(self._this[self._pos:])
            count = len(self._this)-self._pos
            bytesleft = bytescount-count
            required_count = bytesleft // self._chunk_size
            try:
                for _ in range(required_count):
                    # 获取required_count个的block
                    block = await self.getnextChunk()
                    count += len(block)
                    dest.write(block)
                # 再获取一个存入this
                self._this = await self.getnextChunk()
            except StopAsyncIteration:
                self._this = None
            else:
                self._pos = bytesleft-required_count*self._chunk_size
                dest.write(self._this[:self._pos])
                count += self._pos
        self.absolutepos += count


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
                    try:
                        flv = Flv()
                        await flv.check(self.stream, file)
                    except asyncio.TimeoutError:
                        logger.error(f"{self.threadid}: Access Timeout")
                    except StopAsyncIteration:
                        pass
                    except Exception as e:
                        logger.exception(
                            f"{self.threadid}: Exception occurred while recording.")
                    finally:
                        del flv
            del self.stream
            self.stream = None

    @property
    def downloaded(self):
        if self.stream:
            return self.stream.absolutepos
        else:
            return 0
