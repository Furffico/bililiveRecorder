# coding=utf-8
# source: nICEnnnnnnnLee/LiveRecorder
# with modification
class Flv(object):
    def __init__(self):
        pass

    async def check(self, origin, dest):
        # 复制头部
        await origin.directWrite(9, dest)
        # 处理Tag内容

        latsValidLength = currentLength = 9
        self.lastTimestampRead = {8: -1, 9: -1}
        self.lastTimestampWrite = {8: -1, 9: -1}

        while True:
            head = await origin.read(12)
            latsValidLength, currentLength = currentLength, dest.tell() + 4
            # 读取tag类型
            tagType = head[4]

            if tagType == 8 or tagType == 9:  # 8/9 audio/video
                # tag data size 3个字节。表示tag data的长度。从streamd id 后算起。
                dataSize = int.from_bytes(
                    head[5:8], byteorder='big', signed=False)
                # 时间戳 3 + 1
                timestamp = int.from_bytes(
                    head[8:11], byteorder='big', signed=False)
                timestamp |= head[11] << 24
                fixedTimestamp = self.dealTimeStamp(timestamp, tagType)

                dest.write(head[:8] + fixedTimestamp)

                # 数据
                await origin.directWrite(3 + dataSize, dest)

            elif tagType == 18:  # scripts
                # tag data size 3个字节。表示tag data的长度。从streamd id 后算起。
                data = head[5:8]
                dest.write(b'\x00\x00\x00\x00\x12'+data+b'\x00\x00\x00\x00')
                dataSize = int.from_bytes(data, byteorder='big', signed=False)
                # 数据
                await origin.directWrite(3 + dataSize, dest)

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
            if ltsr - timestamp < 5000:
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
