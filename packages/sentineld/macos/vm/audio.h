// Desktop sound: libkrun's virtio-snd playback stream (stereo S16 at 48 kHz) -> Opus (AudioToolbox,
// 10 ms packets) -> the media channel (sentineld DESIGN.md §6 packet 5), which viewers play.
#pragma once
#include <libkrun.h>

// The guest's sound card; its playback is encoded and published while the VM runs.
KrunSoundDevice audio_device(void);
