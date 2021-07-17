FROM python:3.8-alpine

WORKDIR /app

COPY . /app/

RUN pip3 install --no-cache-dir -r requirements.txt && \
    rm requirements.txt

VOLUME /data

ENV RECORDER_TEMPDIR /data/tmp
ENV RECORDER_SAVEDIR /data/downloads
ENV RECORDER_HISTORYDIR /app

CMD /usr/local/bin/python record.py