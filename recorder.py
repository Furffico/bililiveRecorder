import os
import pickle
import logging
import signal

dockerconfig = """\
[BASIC]
; 使用docker运行时以下三项都不需要更改
saveroot=/data/downloads
temppath=/data/tmp
history=/data
; Bark App的推送地址，可以留空
; barkurl=https://api.day.app/<key>/

; 房间配置（可以有不止一个） 
; 例：
[bilibiliLive] ; 房间的标识符
; 直播间的id，必填
roomid=1
; 保存录播的路径（为全局配置中saveroot的子目录），默认为房间的标识符
savefolder=哔哩哔哩直播
; 请求直播状态的最少间隔（单位秒，默认为60）
updateinterval=60
; 是否(yes/no)启用监听和录播，默认为yes
activated=yes
"""


def readHistory(path):    # 读取开播历史
    hispath = os.path.join(path, 'history.pkl') if path else None
    if hispath and os.path.isfile(hispath):
        with open(hispath, 'rb') as f:
            OrgHistory = pickle.load(f)
    else:
        OrgHistory = {}
    return OrgHistory


def setlogger(level=logging.INFO, filepath=None, filelevel=logging.WARNING):
    global logger

    formatter = logging.Formatter(
        "[%(asctime)s][%(name)s][%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    handler1 = logging.StreamHandler()
    handler1.setFormatter(formatter)
    handler1.setLevel(level)

    if filepath:
        with open(filepath, 'a') as f:
            f.write('\n\n\n')
        handler2 = logging.FileHandler(filename=filepath)
        handler2.setFormatter(formatter)
        handler2.setLevel(filelevel)

    for name in ['recorder', 'monitor', 'postprocess', 'main']:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.addHandler(handler1)
        if filepath:
            logger.addHandler(handler2)

    logger.info('Program started')


def cleartempDir(path):    # 完成剩余的时间轴处理任务后退出
    from configparser import ConfigParser
    from main.Monitor import createFlvcheckThreads
    from main.FlvCheckThread import FlvCheckThread

    config = ConfigParser()
    config.read(path)

    HISTORYPATH = os.getenv(
        'HISTORYDIR') or config['BASIC'].get('history', './')
    if not os.path.isdir(HISTORYPATH):
        os.mkdir(HISTORYPATH)

    createFlvcheckThreads(config['BASIC'].get(
        'flecheckercount', 1), HISTORYPATH)
    FlvCheckThread.q.join()
    FlvCheckThread.onexit()

    l = list(FlvCheckThread.getQueue())
    with open(os.path.join(HISTORYPATH, 'queue.pkl'), 'wb') as f:
        pickle.dump(l, f)


def runfromConfig(path):    # 从设置文件读取房间后运行
    from configparser import ConfigParser
    from main.Liveroom import LiveRoom
    from main.Monitor import Monitor

    config = ConfigParser()
    config.read(path)

    # 读取缓存路径与保存路径
    TEMPDIR = os.getenv('TEMPDIR') or config['BASIC'].get('temppath', './tmp')
    SAVEDIR = os.getenv('SAVEDIR') or config['BASIC'].get(
        'saveroot', './downloads')
    if not os.path.isdir(TEMPDIR):
        os.mkdir(TEMPDIR)
    if not os.path.isdir(SAVEDIR):
        os.mkdir(SAVEDIR)

    # 读取历史
    HISTORYPATH = os.getenv(
        'HISTORYDIR') or config['BASIC'].get('history', './')
    if not os.path.isdir(HISTORYPATH):
        os.mkdir(HISTORYPATH)
    history = readHistory(HISTORYPATH)

    barkurl = config['BASIC'].get('barkurl', '')
    if barkurl:
        LiveRoom.setNotification(barkurl)

    # 读取房间
    r = []
    for key in config.sections():
        if key == 'BASIC':
            continue
        item = config[key]
        if item.getboolean('activated', True):
            roomid = item.getint('roomid')
            r.append(LiveRoom(
                roomid=roomid,
                code=key,
                savefolder=os.path.join(SAVEDIR, key),
                tmpfolder=TEMPDIR,
                updateInterval=item.getint('updateinterval', 60),
                history=history.get(roomid, None)
            ))

    if not r:
        logger.warning('NO activated room found in config file')
        logger.warning('program terminated')
        quit()

    # 运行
    monitor = Monitor(
        rooms=r,
        flvcheckercount=config['BASIC'].get('flecheckercount', 1),
        historypath=HISTORYPATH
    )
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()


def runfromTerminalArgs(args):    # 从命令行参数读取房间后运行
    from main.Liveroom import LiveRoom
    from main.Monitor import Monitor

    if not os.path.isdir(args.savedir):
        logger.warning('provided savedir is not a folder')
        quit()

    SAVEDIR = args.savedir

    LiveRoom.overrideDynamicInterval=True
    r = [LiveRoom(args.roomid, 'M', SAVEDIR, args.updateinterval)]

    # 运行
    monitor = Monitor(r, cleanTerminate=True)
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()


def run():
    import argparse
    parser = argparse.ArgumentParser(description='A recorder for bililive.')

    parser.add_argument("-c", "--config", type=str, help="path to config file.")
    parser.add_argument("--log", type=str, help="path to log file.", default='')

    parser.add_argument("-r", "--roomid", type=int)
    parser.add_argument("-i", "--updateinterval", type=int, default=30)
    parser.add_argument("-s", "--savedir", type=str, help="directory to store recorded videos.")
    parser.add_argument("--cleartmp", action="store_true", default=False)

    args = parser.parse_args()

    configpath = args.config or os.getenv('CONFIGPATH')
    logpath = args.log or os.getenv('LOGPATH')
    setlogger(filepath=logpath)

    if configpath:
        if not args.config and not os.path.isfile(os.getenv('CONFIGPATH')):
            # 用docker运行时配置文件不在指定的位置
            open(os.getenv('CONFIGPATH'), 'w').write(dockerconfig)
            logger.warning('请按照说明配置好挂载目录下的config.ini后再运行这个容器。')
            logger.info('program exited')
            quit()
        if args.cleartmp:
            cleartempDir(configpath)
        else:
            runfromConfig(configpath)
    else:
        if not args.roomid or not args.savedir:
            logger.warning('config file or parameter ROOMID and SAVEDIR is required.')
            quit()
        runfromTerminalArgs(args)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run()
