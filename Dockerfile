FROM python:3.8-alpine

VOLUME /data

WORKDIR /app

COPY ./recorder.py ./requirements.txt /app/
COPY ./main /app/main

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apk/repositories && \
    apk add -U --no-cache tzdata && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    apk del tzdata

RUN pip3 install --no-cache-dir -r requirements.txt && \
    rm requirements.txt

ENV TEMPDIR /data/tmp
ENV SAVEDIR /data/downloads
ENV CONFIGPATH /data/config.ini
ENV HISTORYDIR /data
ENV LOGPATH ''

WORKDIR /app

CMD /usr/local/bin/python recorder.py