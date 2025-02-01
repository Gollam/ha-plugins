FROM alpine:3.21.2
ENV LANG C.UTF-8

COPY user.mak /tmp/

WORKDIR /tmp/pjproject

RUN apk add --no-cache build-base openssl-dev alsa-lib-dev gsm-dev opus-dev speex-dev speexdsp-dev portaudio-dev libsrtp-dev libsamplerate-dev linux-headers python3-dev swig ffmpeg git python3 py3-pip tzdata \
    && pip3 install --no-cache-dir --break-system-packages pydub requests PyYAML typing_extensions python-dotenv ha-mqtt-discoverable \
    && git clone --depth 1 --branch 2.15.1 https://github.com/pjsip/pjproject.git /tmp/pjproject \
    && cp /tmp/user.mak /tmp/pjproject \
    && set -xe \
    && ./configure --enable-shared --disable-libwebrtc \
    && make \
    && make dep \
    && make install \
    && cd pjsip-apps/src/swig \
    && make python \
    && make -C python install \
    && make -C python clean \
    && cd / \
    && rm -rf /tmp/pjproject \
    && apk --purge del build-base openssl-dev alsa-lib-dev gsm-dev opus-dev speex-dev speexdsp-dev portaudio-dev libsrtp-dev libsamplerate-dev linux-headers python3-dev swig git py3-pip

COPY run.sh /
RUN chmod a+x /run.sh

COPY src/ /ha-sip/

CMD [ "python3", "/ha-sip/main.py" ]
