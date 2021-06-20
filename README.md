# bililiveRecorder

基于python的轻量级多线程bilibili直播录播姬，适合在树莓派或低性能服务器上运行。

API和flv的时间轴处理的部分参考和复制了[nICEnnnnnnnLee/LiveRecorder](https://github.com/nICEnnnnnnnLee/LiveRecorder)的部分内容，在此深表感谢。

## 用法
### 依赖
- python版本至少为3.6
- [requests](https://github.com/psf/requests)，可通过pip安装。

### config.ini
config.ini由python的标准库[configparser](https://docs.python.org/3/library/configparser.html)解析，包含基本配置和房间配置两部分，支持注释。
```ini
; config.ini
; 全局配置 ==========
[BASIC] 
saveroot=./downloads/ ; 录制文件的保存路径
temppath=./tmp ; 缓存路径

; 房间配置（可以有不止一个） 
; 例：
[MinamiNami] ; 房间名
; 直播间的id
roomid=22571958
; 保存录播的路径（为全局配置中saveroot的子目录），这行默认为上面的房间名
savefolder=美波七海
; 请求直播状态的等待时间（单位秒，默认为60）
updateInterval=60
; 是否启用监听和录播，默认为yes
activated=yes

```
### 启动
将record.py和flv_checker.py下载到同一个文件夹内，安装依赖requests库并配置好config.ini。
```bash
$ python3 record.py
```
程序运行时会循环监听房间的开播状态或者录制直播。

## To-dos
- 完善命令行参数和运行配置
- 让代码更美观