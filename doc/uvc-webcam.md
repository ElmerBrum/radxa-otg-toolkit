# Radxa ZERO 3E as a USB webcam (UVC gadget)

These scripts make the Radxa ZERO 3E show up on a laptop as a normal USB webcam
that plays a video file in a loop. The laptop reads it like any webcam, with no
driver needed (Camera app, OBS, ffplay, browser...).

The video is transcoded live, using the RK3566 hardware video engine, only
while the laptop is streaming. Nothing is cached on disk, and video length or
size doesn't matter.

Tested on DietPi (Debian 12), kernel `6.1.115-vendor-rk35xx`, Rockchip
`ffmpeg` (`/usr/local/bin/ffmpeg` with `rkmpp`/`rkrga`). The laptop was
connected to the OTG USB‑C port.

## Files

| File | What it does |
|------|--------------|
| `uvc-webcam/uvc-webcam.sh`  | Creates the UVC gadget (configfs), starts the streamer, and removes the gadget on exit |
| `uvc-webcam/uvc-stream.py`  | Answers the host's UVC requests and runs `ffmpeg` while the host streams, sending frames at the video's frame rate |
| [uvc-webcam-configuration.md](uvc-webcam-configuration.md) | Settings you can change (resolutions, FPS, quality, USB name...) |

No packages to install: it only needs `bash`, `python3` and the Rockchip
`ffmpeg`, which are already on the board.

## 1. Put the OTG port in device mode

The OTG port must **not** be in host mode. If you enabled host mode
(see [otg-host-mode.md](otg-host-mode.md)), remove `dwc3-host` from the `overlays=` line and reboot:

```bash
sudo sed -i -E '/^overlays=/s/ ?dwc3-host//' /boot/dietpiEnv.txt
sudo reboot
```

Check:

```bash
ls /sys/class/udc
# fcc00000.usb
```

## 2. Copy the scripts to the board

```bash
scp uvc-webcam/uvc-webcam.sh uvc-webcam/uvc-stream.py toletus@192.168.27.65:~/uvc-webcam/
```

## 3. Start

```bash
cd ~/uvc-webcam
sudo ./uvc-webcam.sh start /usr/local/share/ihm-facial/videos/actuar-video.mp4
```

```
UVC gadget on /dev/video0, 24 fps, 1280x720 640x360 (Ctrl+C to stop)
host connected
format committed: MJPEG 1280x720
streaming started (1280x720, 4 buffers)
```

It starts right away; there's nothing to pre-render.

## 4. Connect the laptop

Connect a USB cable from the laptop to the board's **OTG USB‑C port**. The laptop
sees a new camera called **"Radxa UVC Webcam"**. Open it with any camera app.

On a Linux laptop:

```bash
lsusb                       # ... ID 1d6b:0104 Linux Foundation Multifunction Composite Gadget
v4l2-ctl --list-devices     # find the /dev/videoX of "Radxa UVC Webcam"
ffplay /dev/videoX
```

## 5. Stop

Press **Ctrl+C**, run `sudo ./uvc-webcam.sh stop` from another shell, or just
close the terminal. All three stop `ffmpeg` and the streamer and remove the USB
gadget. If the process is killed with `kill -9`, the next `start` removes the
leftover gadget automatically.

## How it works

1. The script creates a UVC function in configfs
   (`/sys/kernel/config/usb_gadget/uvcwebcam`) with an MJPEG format, one frame
   descriptor per resolution and the video's frame interval. Then it binds the
   gadget to the OTG controller `fcc00000.usb`.
2. The kernel creates a V4L2 output node (`/dev/videoN`) for the gadget.
   `uvc-stream.py` handles the UVC probe/commit requests on that node.
3. When the laptop starts streaming, `uvc-stream.py` starts `ffmpeg` at the
   resolution the laptop chose, with every step in hardware:

   ```
   h264_rkmpp (decode) → scale_rkrga (resize, convert to RGBA) → mjpeg_rkmpp (JPEG encode) → pipe
   ```

   with `-stream_loop -1` to loop the video and `-r FPS` for a constant rate.
4. A reader thread splits the pipe into JPEG frames and keeps a queue of up to
   4 frames. When the queue is full, `ffmpeg` blocks on the pipe, so it runs only
   as fast as frames are sent. The main loop sends one frame per frame period.
   If a frame is ever late, it repeats the previous one.
5. When the laptop stops streaming, `ffmpeg` is killed. The kernel also kills
   it if the streamer dies (`PR_SET_PDEATHSIG`).

## Design measurements

All measured on the board with a 112 MB, 1080p30 H.264 (High profile) test
video, the "heavy video" case. 100% CPU = one of the 4 cores.

**Why live transcoding and not a cache.** The previous version rendered every
frame to JPEG in `/var/cache` and loaded all of them into RAM. For a 100 MB /
4 min / 30 fps video that's about 525 MB of disk *and* 525 MB of RAM, on a board
with 961 MB of RAM. Live transcoding uses about 20 MB of RAM and no disk.

**Pipeline throughput** (30 s of video, as fast as possible):

| Pipeline | 720p | 1080p |
|---|---|---|
| Software decode + scale + JPEG | 28.6 fps, 375% CPU | 22.1 fps, 381% CPU |
| Hardware decode, software scale + JPEG | 33.2 fps, 325% | 25.6 fps, 359% |
| Hardware decode + JPEG, software scale | 29.4 fps, 188% | 28.0 fps, 51% |
| **Everything in hardware (used)** | **66.8 fps, 50%** | **34.5 fps, 39%** |

Software can't hold 30 fps. The full-hardware pipeline holds 720p with a 2× margin.
At 1080p the hardware JPEG encoder is the limit, about 32–35 fps, so there's
little margin.

**Real-time run, the whole 180 s video at 30 fps:**

| Output | Frames | fps | First frame | ffmpeg CPU | RAM | Largest JPEG |
|---|---|---|---|---|---|---|
| 640x360 | 5400/5400 | 30.04 | 0.23 s | 31% | 20 MB | 39 KB |
| 1280x720 | 5400/5400 | 30.03 | 0.25 s | 31% | 18 MB | 107 KB |
| 1920x1080 | 5400/5400 | 30.01 | 0.30 s | 38% | 19 MB | 197 KB |

SoC temperature stayed at 46–53 °C.

**Colors.** If the JPEG encoder is fed NV12 directly, it keeps the video's
limited-range values (16–235) but labels the JPEG full range (0–255), so the
image looks washed out. The PSNR as viewed in RGB was 26.8 dB. Converting to
RGBA in RGA first fixes it: 37.8 dB, against 37.4 dB for the old software
encoder.

**Buffer size.** Hardware JPEGs peaked at 0.10–0.18 bytes/pixel. The gadget
offers `width × height × 0.5` bytes per frame. A larger frame would be skipped,
not sent truncated; none were in testing.

**Start-up.** Without a prefill, the first 2 frame ticks always found the queue
empty. Waiting for 2 queued frames (0.07 s) removed that: 0 underruns in
20 s × 3 runs, and 0 underruns in the full end-to-end test with the laptop. With
the laptop streaming 720p, `ffmpeg` used 28% CPU and `uvc-stream.py` 11%.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `fcc00000.usb is not in device mode` | `dwc3-host` is still in `/boot/dietpiEnv.txt`. See step 1. |
| `Already running` | Another instance is running. Use `sudo ./uvc-webcam.sh stop`. |
| Laptop sees nothing | Check that you are using the OTG port and a data cable (not charge-only). Run `cat /sys/class/udc/fcc00000.usb/state`: it must say `configured`. |
| Camera listed but black | Look at the script output. You should see `streaming started`. `no frame from ffmpeg` or `ffmpeg exited (...)` means the video couldn't be decoded. Test it with `ffprobe <video>`. |
| Stutter | When streaming stops, the log line `transcoder stopped (underruns=N, ...)` shows how many frames were late. Use a lower resolution, or lower `FPS`. |
