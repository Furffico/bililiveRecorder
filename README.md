# bililiveRecorder

基于python的轻量级多线程bilibili直播录播姬，适合在树莓派或低性能服务器上运行。

API和flv的时间轴处理的部分参考和复制了[nICEnnnnnnnLee/LiveRecorder](https://github.com/nICEnnnnnnnLee/LiveRecorder)的部分内容，在此深表感谢。

## 依赖
- python版本至少为3.6
- [requests](https://github.com/psf/requests)，可通过pip安装。

## 运行
### 直接运行
将record.py和flv_checker.py放置于同一个文件夹内，安装依赖requests库并配置好config.ini。
```bash
$ python3 record.py
```
程序运行时会循环监听房间的开播状态或者录制直播。

### 通过docker运行
首先将文件clone到本地，并按照说明配置config.ini。

随后用docker构建镜像：
``` bash
$ docker build -t bililiverecorder:1.3 .
```

运行镜像，其中/path/to/data替换为数据卷或主机上用于存储录播数据的位置：
``` bash
$ docker run -d -v /path/to/data:/data bililiverecorder:1.3
```

## To-dos
- 完善命令行参数和运行配置
- 让代码更美观