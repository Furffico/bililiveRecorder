import os
import pickle
import logging
from main.Liveroom import LiveRoom
from main.Monitor import Monitor
import signal


def readHistory(path):
    # 读取开播历史
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


def cleartempDir(path):
    # 完成剩余的时间轴处理任务后退出
    from configparser import ConfigParser
    from main.Monitor import createFlvcheckThreads
    from main.FlvCheckThread import FlvCheckThread
    import pickle

    config = ConfigParser()
    config.read(path)

    HISTORYPATH = os.getenv(
        'HISTORYDIR') or config['BASIC'].get('history', './')
    if not os.path.isdir(HISTORYPATH):
        os.mkdir(HISTORYPATH)

    createFlvcheckThreads(config['BASIC'].get('flecheckercount', 1),HISTORYPATH)
    FlvCheckThread.q.join()
    FlvCheckThread.onexit()

    l = list(FlvCheckThread.getQueue())
    with open(os.path.join(HISTORYPATH, 'queue.pkl'), 'wb') as f:
        pickle.dump(l, f)


def runfromConfig(path):
    # 从设置文件读取房间
    from configparser import ConfigParser
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


def runfromTerminalArgs(args):
    # 从命令行参数读取房间
    if not args.roomid:
        logger.warning('roomid required')
        quit()
    if not args.savedir:
        logger.warning('savedir required')
        quit()
    elif not os.path.isdir(args.savedir):
        logger.warning('provided savedir is not a folder')
        quit()

    SAVEDIR = args.savedir
    HISTORYPATH = None
    readHistory(HISTORYPATH)

    r = [LiveRoom(args.roomid, 'M', SAVEDIR, args.updateinterval,
                  overrideDynamicInterval=True)]

    # 运行
    monitor = Monitor(r, cleanTerminate=True)
    for sig in [signal.SIGINT, signal.SIGHUP, signal.SIGTERM]:
        signal.signal(sig, monitor.shutdown)
    monitor.run()


def run():
    import argparse
    parser = argparse.ArgumentParser(description='A recorder for bililive.')
    parser.add_argument("-c", "--config", type=str,
                        help="path to config file.")
    parser.add_argument("--log", type=str,
                        help="path to log file.", default='')

    parser.add_argument("-r", "--roomid", type=int)
    parser.add_argument("-i", "--updateinterval", type=int, default=30)
    parser.add_argument("-s", "--savedir", type=str,
                        help="directory to store recorded videos.")
    parser.add_argument("--cleartmp", action="store_true", default=False)

    args = parser.parse_args()

    configpath = args.config or os.getenv('CONFIGPATH')
    logpath = args.log or os.getenv('LOGPATH')
    setlogger(filepath=logpath)

    if configpath:
        if not args.config and not os.path.isfile(os.getenv('CONFIGPATH')):
            logger.warning('请将配置文件config.ini放置在挂载路径的根目录下。')
            logger.info('program exited')
            quit()
        if args.cleartmp:
            cleartempDir(configpath)
        else:
            runfromConfig(configpath)
    else:
        runfromTerminalArgs(args)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run()
