import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "fixtures");
mkdirSync(outDir, { recursive: true });

const sampleRate = 44100;
const seconds = 3;
const frames = sampleRate * seconds;
const channels = 2;
const dataBytes = frames * channels * 2;

const buffer = Buffer.alloc(44 + dataBytes);
buffer.write("RIFF", 0, "ascii");
buffer.writeUInt32LE(36 + dataBytes, 4);
buffer.write("WAVE", 8, "ascii");
buffer.write("fmt ", 12, "ascii");
buffer.writeUInt32LE(16, 16);
buffer.writeUInt16LE(1, 20);
buffer.writeUInt16LE(channels, 22);
buffer.writeUInt32LE(sampleRate, 24);
buffer.writeUInt32LE(sampleRate * channels * 2, 28);
buffer.writeUInt16LE(channels * 2, 32);
buffer.writeUInt16LE(16, 34);
buffer.write("data", 36, "ascii");
buffer.writeUInt32LE(dataBytes, 40);

for (let i = 0; i < frames; i += 1) {
  const value = Math.round(
    12000 * Math.sin((2 * Math.PI * 440 * i) / sampleRate),
  );
  for (let ch = 0; ch < channels; ch += 1) {
    buffer.writeInt16LE(value, 44 + (i * channels + ch) * 2);
  }
}

writeFileSync(join(outDir, "sample.wav"), buffer);
console.log("создан e2e/fixtures/sample.wav");
