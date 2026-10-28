// Desktop sound (see audio.h). One thread reads the guest's PCM as it plays, cuts it into 10 ms
// frames and encodes each into one Opus packet. A guest that plays nothing sends nothing.
#include "audio.h"

#include <AudioToolbox/AudioToolbox.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

#include "display.h"

enum { FRAME_SAMPLES = 480, FRAME_BYTES = FRAME_SAMPLES * 4, MAX_OPUS = 1275 };

struct encoder {
  AudioConverterRef converter;
  int16_t pcm[FRAME_SAMPLES * 2];
  bool served;  // the converter's input callback has taken the current frame
};

static OSStatus supply(AudioConverterRef converter, UInt32 *packets, AudioBufferList *data,
                       AudioStreamPacketDescription **descriptions, void *user) {
  (void)converter, (void)descriptions;
  struct encoder *e = user;
  if (e->served) {
    *packets = 0;
    return 1;  // no more input for this packet
  }
  e->served = true;
  *packets = FRAME_SAMPLES;
  data->mBuffers[0] = (AudioBuffer){.mNumberChannels = 2, .mDataByteSize = FRAME_BYTES, .mData = e->pcm};
  return noErr;
}

static void encode(struct encoder *e) {
  uint8_t out[MAX_OPUS];
  AudioBufferList list = {.mNumberBuffers = 1, .mBuffers = {{.mNumberChannels = 2, .mDataByteSize = sizeof out, .mData = out}}};
  AudioStreamPacketDescription description;
  UInt32 packets = 1;
  e->served = false;
  const OSStatus status = AudioConverterFillComplexBuffer(e->converter, supply, e, &packets, &list, &description);
  if ((status == noErr || status == 1) && packets == 1 && list.mBuffers[0].mDataByteSize)
    display_packet(5, out, list.mBuffers[0].mDataByteSize);
}

static void *run(void *arg) {
  const int fd = (int)(intptr_t)arg;
  const AudioStreamBasicDescription pcm = {
      .mSampleRate = 48000, .mFormatID = kAudioFormatLinearPCM,
      .mFormatFlags = kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked, .mBytesPerPacket = 4,
      .mFramesPerPacket = 1, .mBytesPerFrame = 4, .mChannelsPerFrame = 2, .mBitsPerChannel = 16};
  const AudioStreamBasicDescription opus = {
      .mSampleRate = 48000, .mFormatID = kAudioFormatOpus, .mFramesPerPacket = FRAME_SAMPLES, .mChannelsPerFrame = 2};
  static struct encoder e;
  if (AudioConverterNew(&pcm, &opus, &e.converter)) {
    fprintf(stderr, "sentinel-workspace: no Opus encoder; desktop sound is off\n");
    return NULL;
  }
  const UInt32 bitrate = 128000;
  AudioConverterSetProperty(e.converter, kAudioConverterEncodeBitRate, sizeof bitrate, &bitrate);
  size_t have = 0;
  for (;;) {
    const ssize_t n = read(fd, (uint8_t *)e.pcm + have, FRAME_BYTES - have);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return NULL;
    have += (size_t)n;
    if (have == FRAME_BYTES) {
      encode(&e);
      have = 0;
    }
  }
}

KrunSoundDevice audio_device(void) {
  int pair[2];
  if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair)) {
    perror("sentinel-workspace: sound socket pair");
    exit(1);
  }
  // A few hundred milliseconds of room: the device drops what does not fit, never waits.
  const int size = 64 << 10;
  setsockopt(pair[0], SOL_SOCKET, SO_SNDBUF, &size, sizeof size);
  setsockopt(pair[1], SOL_SOCKET, SO_RCVBUF, &size, sizeof size);
  KrunError err = NULL;
  KrunSoundDevice device = krun_sound_device_new(pair[0], &err);
  if (err) {
    fprintf(stderr, "sentinel-workspace: sound device failed\n");
    exit(1);
  }
  pthread_t thread;
  if (pthread_create(&thread, NULL, run, (void *)(intptr_t)pair[1])) {
    perror("sentinel-workspace: sound thread");
    exit(1);
  }
  pthread_detach(thread);
  return device;
}
