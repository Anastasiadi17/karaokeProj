/** Кодирование в WAV 16 бит. Без зависимостей: заголовок плюс PCM. */

export function floatToPcm16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample));
  return clamped < 0
    ? Math.round(clamped * 32768)
    : Math.round(clamped * 32767);
}

export function interleave(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0];

  const frames = channels[0].length;
  const out = new Float32Array(frames * channels.length);
  for (let frame = 0; frame < frames; frame += 1) {
    for (let ch = 0; ch < channels.length; ch += 1) {
      out[frame * channels.length + ch] = channels[ch][frame];
    }
  }
  return out;
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}

export function encodeWav(channels: Float32Array[], sampleRate: number): Blob {
  const samples = interleave(channels);
  const channelCount = channels.length;
  const dataBytes = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");

  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channelCount, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channelCount * 2, true);
  view.setUint16(32, channelCount * 2, true);
  view.setUint16(34, 16, true);

  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);

  for (let i = 0; i < samples.length; i += 1) {
    view.setInt16(44 + i * 2, floatToPcm16(samples[i]), true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}
