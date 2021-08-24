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
$ python3 record.py -c ./config.ini
# 或者
$ python3 record.py -r <roomid> -s <savedir>
```
程序运行时会循环监听房间的开播状态或者录制直播。

### 通过docker运行
首先将文件clone到本地。
```bash
$ git clone https://github.com/Furffico/bililiveRecorder.git
```

随后用docker构建镜像：
``` bash
$ docker build -t bililiverecorder:1.4 .
```

按照说明配置好config.ini并拷贝至主机挂载至容器的目录内。运行容器，其中`/path/to/data`替换为前述目录的路径：
``` bash
$ docker run -d -v /path/to/data:/data bililiverecorder:1.4
```

#### 说明
容器的默认时区为Asia/Shanghai，如需更改时区请在构建前修改Dockerfile。

## To-dos
- 让代码更美观