# coding=utf-8
# source: nICEnnnnnnnLee/LiveRecorder
# with modification
class Flv(object):
    def __init__(self):
        pass

    async def check(self, stream, dest):
        # 复制头部
        dest.write(await stream.read(9))
        # 处理Tag内容
        await self.checkTag(stream, dest)

    async def checkTag(self, origin, dest):
        latsValidLength = currentLength = 9

        self.lastTimestampRead = {b'\x08': -1, b'\x09': -1}
        self.lastTimestampWrite = {b'\x08': -1, b'\x09': -1}

        while True:
            # 读取前一个tag size
            data = await origin.read(4)
            dest.write(data)
            # 记录当前新文件位置，若下一tag无效，则需要回退
            latsValidLength, currentLength = currentLength, dest.tell()
            # 读取tag类型
            tagType = await origin.read(1)

            if tagType == b'\x08' or tagType == b'\x09':  # 8/9 audio/video
                # tag data size 3个字节。表示tag data的长度。从streamd id 后算起。
                data = await origin.read(7)
                dataSize = int.from_bytes(
                    data[:3], byteorder='big', signed=False)
                # 时间戳 3 + 1
                timestamp = int.from_bytes(
                    data[3:6], byteorder='big', signed=False)
                timestamp |= (int.from_bytes(
                    data[6:], byteorder='big', signed=False) << 24)
                fixedTimestamp = self.dealTimeStamp(timestamp, tagType)

                dest.write(tagType + data[:3] + fixedTimestamp)

                # 数据
                dest.write(await origin.read(3 + dataSize))

            elif tagType == b'\x12':  # scripts
                # 如果是scripts脚本，默认为第一个tag，此时将前一个tag Size 置零
                dest.seek(dest.tell() - 4)
                dest.write(b'\x00\x00\x00\x00' + tagType)
                # tag data size 3个字节。表示tag data的长度。从streamd id 后算起。
                data = await origin.read(7)
                dest.write(data[:3] + b'\x00\x00\x00\x00')
                dataSize = int.from_bytes(
                    data[:3], byteorder='big', signed=False)
                # 数据
                dest.write(await origin.read(3 + dataSize))

            else:
                dest.truncate(latsValidLength)
                break

    def dealTimeStamp(self, timestamp, tagType):
        # 如果是首帧
        ltsr = self.lastTimestampRead[tagType]
        if ltsr == -1:
            self.lastTimestampWrite[tagType] = 0
        elif timestamp >= ltsr:  # 如果时序正常
            # 间隔十分巨大(1s)，那么重新开始即可
            if timestamp > ltsr + 1000:
                self.lastTimestampWrite[tagType] += 10
            else:
                self.lastTimestampWrite[tagType] += timestamp - ltsr
        else:  # 如果出现倒序时间戳
            # 如果间隔不大，那么如实反馈
            if ltsr - timestamp < 5 * 1000:
                tmp = timestamp - ltsr + self.lastTimestampWrite[tagType]
                if tmp < 0:
                    tmp = 1
                self.lastTimestampWrite[tagType] = tmp
            else:  # 间隔十分巨大，那么重新开始即可
                self.lastTimestampWrite[tagType] += 10
        self.lastTimestampRead[tagType] = timestamp

        # 低于0xffffff部分
        lowCurrenttime = self.lastTimestampWrite[tagType] & 0xffffff
        result = lowCurrenttime.to_bytes(3, byteorder='big')
        # 高于0xffffff部分
        highCurrenttime = self.lastTimestampWrite[tagType] >> 24
        result += highCurrenttime.to_bytes(1, byteorder='big')

        return result
