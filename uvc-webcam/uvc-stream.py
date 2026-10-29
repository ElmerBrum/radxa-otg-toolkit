#!/usr/bin/env python3
"""Minimal UVC gadget userspace driver.

Answers the host's UVC probe/commit requests and, while the host streams, runs
ffmpeg with the Rockchip hardware decoder/scaler/JPEG encoder to transcode the
video (looped) to MJPEG at the resolution the host chose. Frames are sent at a
fixed FPS; nothing is cached on disk.

Usage: uvc-stream.py /dev/videoN VIDEO FPS MAXPACKET WxH [WxH ...]
Env:   QUALITY=1-99 hardware JPEG quality (empty = encoder default)
"""
import ctypes
import errno
import fcntl
import mmap
import os
import queue
import select
import signal
import struct
import subprocess
import sys
import threading
import time


def _ioc(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


# V4L2 / UVC gadget ioctls (aarch64 struct sizes)
VIDIOC_S_FMT = _ioc(3, 'V', 5, 208)
VIDIOC_REQBUFS = _ioc(3, 'V', 8, 20)
VIDIOC_QUERYBUF = _ioc(3, 'V', 9, 88)
VIDIOC_QBUF = _ioc(3, 'V', 15, 88)
VIDIOC_DQBUF = _ioc(3, 'V', 17, 88)
VIDIOC_STREAMON = _ioc(1, 'V', 18, 4)
VIDIOC_STREAMOFF = _ioc(1, 'V', 19, 4)
VIDIOC_DQEVENT = _ioc(2, 'V', 89, 136)
VIDIOC_SUBSCRIBE_EVENT = _ioc(1, 'V', 90, 32)
UVCIOC_SEND_RESPONSE = _ioc(1, 'U', 1, 64)

BUF_TYPE_OUTPUT = 2
MEMORY_MMAP = 1
FIELD_NONE = 1
MJPG = struct.unpack('<I', b'MJPG')[0]

UVC_EVENT_CONNECT = 0x08000000
UVC_EVENT_DISCONNECT = UVC_EVENT_CONNECT + 1
UVC_EVENT_STREAMON = UVC_EVENT_CONNECT + 2
UVC_EVENT_STREAMOFF = UVC_EVENT_CONNECT + 3
UVC_EVENT_SETUP = UVC_EVENT_CONNECT + 4
UVC_EVENT_DATA = UVC_EVENT_CONNECT + 5

USB_TYPE_MASK = 0x60
USB_TYPE_CLASS = 0x20
UVC_SET_CUR, UVC_GET_CUR, UVC_GET_MIN, UVC_GET_MAX = 0x01, 0x81, 0x82, 0x83
UVC_GET_RES, UVC_GET_LEN, UVC_GET_INFO, UVC_GET_DEF = 0x84, 0x85, 0x86, 0x87
UVC_VS_PROBE_CONTROL, UVC_VS_COMMIT_CONTROL = 1, 2
STREAMING_INTERFACE = 1
EL2HLT = 51

CTRL_FMT = '<HBBIHHHHHIIIBBBB'  # struct uvc_streaming_control (34 bytes)
NBUFS = 4
QUEUE_FRAMES = 4     # encoded JPEGs buffered ahead; ffmpeg blocks on the pipe when full
PREFILL_FRAMES = 2   # queued before streaming starts (measured: the first 2 ticks underrun without it)
FIRST_FRAME_TIMEOUT = 5
STOP_TIMEOUT = 3     # seconds ffmpeg gets to exit on SIGTERM before SIGKILL
PR_SET_PDEATHSIG = 1


def log(*a):
    print(*a, flush=True)


def die_with_parent():
    ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class Transcoder:
    """ffmpeg (hardware) -> MJPEG pipe -> bounded queue of JPEG frames."""

    def __init__(self, video, w, h, fps, quality):
        # RGA converts to RGBA so the JPEG encoder does the YUV range conversion itself;
        # feeding it NV12 directly gives washed-out (limited-range) colors.
        cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-stream_loop', '-1',
               '-hwaccel', 'rkmpp', '-hwaccel_output_format', 'drm_prime', '-afbc', 'rga',
               '-i', video, '-an', '-r', str(fps),
               '-vf', f'scale_rkrga=w={w}:h={h}:format=rgba', '-c:v', 'mjpeg_rkmpp']
        if quality:
            cmd += ['-qp_init', quality]
        cmd += ['-f', 'mjpeg', '-']
        self.frames = queue.Queue(QUEUE_FRAMES)
        self.stopping = False
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL, preexec_fn=die_with_parent)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        buf = b''
        while chunk := self.proc.stdout.read1(65536):
            buf += chunk
            while (end := buf.find(b'\xff\xd9')) >= 0:
                start = buf.find(b'\xff\xd8')
                self.put(buf[start:end + 2])
                buf = buf[end + 2:]
        err = self.proc.stderr.read().decode(errors='replace').strip()
        if not self.stopping:
            log(f'ffmpeg exited ({self.proc.wait()}): {err}')

    def put(self, jpeg):
        while not self.stopping:
            try:
                return self.frames.put(jpeg, timeout=0.2)
            except queue.Full:
                pass

    def get(self, timeout=None):
        try:
            return self.frames.get(timeout=timeout) if timeout else self.frames.get_nowait()
        except queue.Empty:
            return None

    def prefill(self, n, timeout):
        """Wait until n frames are queued: the encoder needs a moment to reach full speed."""
        deadline = time.monotonic() + timeout
        while self.frames.qsize() < n and time.monotonic() < deadline and self.proc.poll() is None:
            time.sleep(0.01)

    def stop(self):
        # Let ffmpeg close its hardware sessions itself: the Rockchip MPP driver crashed once
        # (kernel oops in mpp_session_cleanup_detach) cleaning up a session after ffmpeg was
        # SIGKILLed. The reader keeps draining the pipe (and drops frames) so ffmpeg can exit.
        self.stopping = True
        self.proc.terminate()
        try:
            self.proc.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.thread.join()


class Stream:
    def __init__(self, dev, video, fps, maxpacket, resolutions):
        self.fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        self.video = video
        self.fps = fps
        self.quality = os.environ.get('QUALITY', '')
        self.interval = 10000000 // fps
        self.period = 1.0 / fps
        self.maxpacket = maxpacket
        self.sizes = []     # per frame index: (w, h, max buffer size), same as configfs
        for r in resolutions:
            w, h = map(int, r.split('x'))
            self.sizes.append((w, h, w * h // 2))
        self.probe = self.ctrl(1)
        self.commit = self.ctrl(1)
        self.set_cs = None
        self.streaming = False
        self.bufs = []
        self.free = []
        self.source = None
        self.last = None
        self.underruns = 0
        self.skipped = 0
        self.next_t = 0.0
        for ev in range(UVC_EVENT_CONNECT, UVC_EVENT_DATA + 1):
            fcntl.ioctl(self.fd, VIDIOC_SUBSCRIBE_EVENT, struct.pack('<III20x', ev, 0, 0))

    def ctrl(self, frame):
        frame = min(max(frame, 1), len(self.sizes))
        return struct.pack(CTRL_FMT, 1, 1, frame, self.interval, 0, 0, 0, 0, 0,
                           self.sizes[frame - 1][2], self.maxpacket, 48000000, 3, 1, 1, 1)

    def respond(self, data):
        resp = bytearray(64)
        if isinstance(data, int):
            struct.pack_into('<i', resp, 0, data)
        else:
            struct.pack_into('<i', resp, 0, len(data))
            resp[4:4 + len(data)] = data
        fcntl.ioctl(self.fd, UVCIOC_SEND_RESPONSE, resp)

    def on_setup(self, req):
        rtype, request, wvalue, windex, wlength = struct.unpack_from('<BBHHH', req)
        if rtype & USB_TYPE_MASK != USB_TYPE_CLASS:
            return self.respond(-EL2HLT)
        cs = wvalue >> 8
        if (windex & 0xff) != STREAMING_INTERFACE or cs not in (UVC_VS_PROBE_CONTROL, UVC_VS_COMMIT_CONTROL):
            # No camera/processing-unit controls: stall.
            return self.respond(-EL2HLT)
        if request == UVC_SET_CUR:
            self.set_cs = cs
            return self.respond(wlength)  # accept the data stage, see on_data()
        if request in (UVC_GET_CUR,):
            data = self.probe if cs == UVC_VS_PROBE_CONTROL else self.commit
        elif request in (UVC_GET_MIN, UVC_GET_DEF):
            data = self.ctrl(1)
        elif request == UVC_GET_MAX:
            data = self.ctrl(len(self.sizes))
        elif request == UVC_GET_RES:
            data = bytes(34)
        elif request == UVC_GET_LEN:
            data = struct.pack('<H', 34)
        elif request == UVC_GET_INFO:
            data = bytes([0x03])
        else:
            return self.respond(-EL2HLT)
        self.respond(data[:wlength])

    def on_data(self, data):
        length = struct.unpack_from('<i', data)[0]
        payload = bytes(data[4:4 + length])
        frame = payload[3] if len(payload) > 3 else 1
        ctrl = self.ctrl(frame)
        if self.set_cs == UVC_VS_PROBE_CONTROL:
            self.probe = ctrl
        elif self.set_cs == UVC_VS_COMMIT_CONTROL:
            self.commit = ctrl
            self.set_format()

    def frame_index(self):
        return struct.unpack(CTRL_FMT, self.commit)[2]

    def set_format(self):
        w, h, size = self.sizes[self.frame_index() - 1]
        fmt = bytearray(208)
        struct.pack_into('<I', fmt, 0, BUF_TYPE_OUTPUT)
        struct.pack_into('<IIIIII', fmt, 8, w, h, MJPG, FIELD_NONE, 0, size)
        fcntl.ioctl(self.fd, VIDIOC_S_FMT, fmt)
        log(f'format committed: MJPEG {w}x{h}')

    def reqbufs(self, count):
        rb = bytearray(struct.pack('<III8x', count, BUF_TYPE_OUTPUT, MEMORY_MMAP))
        fcntl.ioctl(self.fd, VIDIOC_REQBUFS, rb)
        return struct.unpack_from('<I', rb)[0]

    def v4l2_buf(self, index=0, bytesused=0):
        b = bytearray(88)
        struct.pack_into('<III', b, 0, index, BUF_TYPE_OUTPUT, bytesused)
        struct.pack_into('<I', b, 16, FIELD_NONE)
        struct.pack_into('<I', b, 60, MEMORY_MMAP)
        return b

    def stream_on(self):
        self.stream_off()
        self.set_format()
        n = self.reqbufs(NBUFS)
        for i in range(n):
            b = self.v4l2_buf(i)
            fcntl.ioctl(self.fd, VIDIOC_QUERYBUF, b)
            offset, length = struct.unpack_from('<I', b, 64)[0], struct.unpack_from('<I', b, 72)[0]
            self.bufs.append(mmap.mmap(self.fd, length, mmap.MAP_SHARED,
                                       mmap.PROT_READ | mmap.PROT_WRITE, offset=offset))
        self.free = list(range(n))
        w, h, _ = self.sizes[self.frame_index() - 1]
        self.source = Transcoder(self.video, w, h, self.fps, self.quality)
        self.last = self.source.get(FIRST_FRAME_TIMEOUT)
        if self.last is None:
            log('no frame from ffmpeg')
        self.source.prefill(PREFILL_FRAMES, 1)
        self.underruns = self.skipped = 0
        self.queue_frame()
        fcntl.ioctl(self.fd, VIDIOC_STREAMON, struct.pack('<i', BUF_TYPE_OUTPUT))
        self.streaming = True
        self.next_t = time.monotonic() + self.period
        log(f'streaming started ({w}x{h}, {n} buffers)')

    def stream_off(self):
        if self.source:
            self.source.stop()
            self.source = None
            log(f'transcoder stopped (underruns={self.underruns}, oversized frames skipped={self.skipped})')
        if self.streaming:
            try:
                fcntl.ioctl(self.fd, VIDIOC_STREAMOFF, struct.pack('<i', BUF_TYPE_OUTPUT))
            except OSError:
                pass
            log('streaming stopped')
        self.streaming = False
        for m in self.bufs:
            m.close()
        self.bufs, self.free = [], []
        try:
            self.reqbufs(0)
        except OSError:
            pass

    def queue_frame(self):
        # The first frame was already taken in stream_on(); later ones come from the queue.
        jpeg = self.source.get() if self.streaming else self.last
        if jpeg is None:          # transcoder late: repeat the previous frame
            self.underruns += 1
            jpeg = self.last
        if jpeg is None or not self.free:
            return
        i = self.free[0]
        if len(jpeg) > len(self.bufs[i]):
            self.skipped += 1
            return
        self.last = jpeg
        self.free.pop(0)
        self.bufs[i][:len(jpeg)] = jpeg
        fcntl.ioctl(self.fd, VIDIOC_QBUF, self.v4l2_buf(i, len(jpeg)))

    def dequeue(self):
        b = self.v4l2_buf()
        try:
            fcntl.ioctl(self.fd, VIDIOC_DQBUF, b)
        except OSError:
            return
        self.free.append(struct.unpack_from('<I', b)[0])

    def handle_event(self):
        ev = bytearray(136)
        fcntl.ioctl(self.fd, VIDIOC_DQEVENT, ev)
        etype = struct.unpack_from('<I', ev)[0]
        u = ev[8:72]
        if etype == UVC_EVENT_SETUP:
            self.on_setup(u)
        elif etype == UVC_EVENT_DATA:
            self.on_data(u)
        elif etype == UVC_EVENT_STREAMON:
            self.stream_on()
        elif etype in (UVC_EVENT_STREAMOFF, UVC_EVENT_DISCONNECT):
            self.stream_off()
        if etype == UVC_EVENT_CONNECT:
            log('host connected')
        elif etype == UVC_EVENT_DISCONNECT:
            log('host disconnected')

    def run(self):
        poller = select.poll()
        poller.register(self.fd, select.POLLPRI)
        registered_out = False
        while True:
            if self.streaming != registered_out:
                poller.modify(self.fd, select.POLLPRI | (select.POLLOUT if self.streaming else 0))
                registered_out = self.streaming
            timeout = None
            if self.streaming:
                timeout = max(0, (self.next_t - time.monotonic()) * 1000)
            for _, mask in poller.poll(timeout):
                if mask & select.POLLPRI:
                    self.handle_event()
                if mask & select.POLLOUT and self.streaming:
                    self.dequeue()
            if self.streaming and time.monotonic() >= self.next_t:
                self.queue_frame()
                self.next_t += self.period
                if self.next_t < time.monotonic():  # fell behind: resync
                    self.next_t = time.monotonic() + self.period


if __name__ == '__main__':
    if len(sys.argv) < 6:
        sys.exit(__doc__)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))
    stream = Stream(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5:])
    try:
        stream.run()
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if e.errno != errno.ENODEV:
            raise
        log('gadget removed')
    finally:
        stream.stream_off()
