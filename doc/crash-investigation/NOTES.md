# Kernel crash investigation (uvc-webcam, MPP version) — paused 2026-10-29

Status: **open, cause not confirmed.** Investigation paused; resume later.

## Symptom

Stopping the webcam (Ctrl+C) while the laptop is streaming, or the next start
after such a stop, makes the board's kernel crash. The terminal shows
`Segmentation fault`, or the board freezes or reboots.

## Evidence

**Crash 1 (16:52:13, full trace captured):**
- Root oops: `mpp_worker_1` in `mpp_dma_session_destroy` ← `mpp_session_deinit`
  ← `mpp_session_cleanup_detach` ← `mpp_task_worker_default`. Misaligned
  pointer `ffff000007ab9a7d` (alignment fault), consistent with use-after-free.
- Oops #2–#5: `__kmem_cache_alloc_node` in `udev-worker` and `python3` (V4L2
  ioctl), i.e. kernel memory already corrupted; Python is a victim.
- Timeline: mid-stream stop of the MPP version at 16:51:05 (ffmpeg SIGKILLed),
  crash 68 s later **at the next gadget start**.

**Crash 2 (~17:48, no trace):** mid-stream stop of the MPP version (B) at
17:47:08, then a new gadget was bound and enumerated at 17:48:16, then the board
died. No oops reached the network log, `pstore` was empty, and unsynced files
on `/var/tmp` were lost on reboot.

**Your Ctrl+C crash:** the run printed `transcoder stopped (...)`, then the
kernel froze. No log was captured (`ssh` without `-t`, so `sudo` couldn't run
`dmesg -w`).

**Every stop while streaming** logs dwc3 `request ... was not queued to ep1in`,
plus `Failed to queue request (-108)` / `VS request completed with status -104`,
the UVC gadget cancelling in-flight USB requests. This was seen with both the
old and the MPP versions.

## Experiments

| Test | Stop | USB streaming | Result |
|---|---|---|---|
| T1: 40 × 3 s | ffmpeg SIGKILL | no | 0 oopses |
| T3: 1 × 150 s at 30 fps (heavy video) | ffmpeg SIGKILL | no | 0 oopses (stopped early) |
| Graceful: 8 × 5 s | ffmpeg SIGTERM | no | 0 oopses |
| A: old version (no MPP) | SIGINT to group, mid-stream | yes | no crash, and the next start (B) was OK |
| B: MPP version | SIGINT to group, mid-stream | yes | no crash at stop; **crashed at the next gadget start** |
| C: MPP version under pty + sudo | Ctrl+C char | yes | invalid: harness started with SIGINT ignored, only ffmpeg died |

## Hypotheses

- H1 (ffmpeg SIGKILL alone breaks MPP): **not supported**, 0 of 49 without
  USB streaming.
- H2 (UVC gadget/dwc3 teardown with in-flight requests corrupts memory):
  plausible, but A (no MPP) did not crash, n=1.
- **H3 (current lead): a mid-stream stop of the MPP version leaves bad kernel
  state that blows up around the next gadget setup.** 2 of 2 for the MPP
  version, 0 of 1 for the old version. n is small.

## Next steps when resuming

1. Before each test, on the board: `sudo sysctl kernel.panic_on_oops=1 kernel.panic=10`
   (runtime only, lets `ramoops` save the oops). Note: crash 2 left `pstore`
   empty anyway.
2. Live log on the laptop: `ssh -t toletus@192.168.27.65 'sudo dmesg -w -T' | tee board-dmesg.log`.
3. Repeat A and B several times each: start, stream, stop mid-stream, wait
   70 s, start again. Compare crash rates.
4. Test mitigations one at a time:
   - stop the USB stream first (`STREAMOFF`, release buffers) and only then stop
     ffmpeg (the current code stops ffmpeg first)
   - run ffmpeg in its own session (`start_new_session=True`), so a terminal
     Ctrl+C doesn't hit it at the same time as Python's teardown
   - after stop, wait or `sync` before removing the gadget
5. Harness lesson: never launch test processes with `&` from a non-interactive
   shell without resetting SIGINT/SIGHUP to `SIG_DFL`. They inherit "ignored".

## Other findings

- `mmcblk1p2` reports 30 ext4 errors since its last fsck (the first predates
  today). Run `fsck` on it.
- Debian `sudo` uses `use_pty`: the command runs on its own pty, and `sudo`
  relays Ctrl+C.

## Files

- `live-A.log`: board kernel log 17:30–17:48 (experiments A and B, up to crash 2).
- `live-D.log`: board kernel log after the 17:48 reboot (experiment C and its stop).
