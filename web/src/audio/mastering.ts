/**
 * Автомастеринг готового микса.
 *
 * Это обработка сигнала, а не модель: срез низов под голосом, мягкая
 * компрессия и подъём к целевой громкости. Названо в интерфейсе честно —
 * «улучшить звучание», а не «AI».
 *
 * Громкость меряется как RMS в dBFS, а не по ITU-R BS.1770 (LUFS):
 * полноценный K-фильтр — отдельная работа, а для «сделать ровнее и громче»
 * разница не слышна. Оговорка сознательная, и её не надо забывать, если
 * когда-нибудь понадобится сравнивать нашу громкость с чужой.
 */

export const TARGET_DBFS = -14;

export interface MasterOptions {
  targetDbfs?: number;
}

export function measureLoudness(buffer: AudioBuffer): number {
  let sum = 0;
  let count = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      sum += data[i] * data[i];
      count += 1;
    }
  }
  if (count === 0) return -Infinity;
  const rms = Math.sqrt(sum / count);
  return rms > 0 ? 20 * Math.log10(rms) : -Infinity;
}

function peakOf(buffer: AudioBuffer): number {
  let peak = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      const value = Math.abs(data[i]);
      if (value > peak) peak = value;
    }
  }
  return peak;
}

export async function master(
  buffer: AudioBuffer,
  options: MasterOptions = {},
): Promise<AudioBuffer> {
  const target = options.targetDbfs ?? TARGET_DBFS;
  const loudness = measureLoudness(buffer);
  // Тишине помочь нечем: усиливать нечего, а деление на ноль дало бы NaN
  // во всём буфере.
  if (!Number.isFinite(loudness)) return buffer;

  const ctx = new OfflineAudioContext(
    buffer.numberOfChannels,
    buffer.length,
    buffer.sampleRate,
  );

  const source = ctx.createBufferSource();
  source.buffer = buffer;

  // Ниже 80 Гц в караоке-миксе нет ничего, кроме гула комнаты и ударов по
  // столу: срез слышен как «стало чище», а не как потеря баса.
  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = 80;

  // Мягкая компрессия: разница между шёпотом и криком сокращается, но не
  // исчезает — жёсткие настройки сплющивают пение в стену.
  const compressor = ctx.createDynamicsCompressor();
  compressor.threshold.value = -18;
  compressor.knee.value = 12;
  compressor.ratio.value = 3;
  compressor.attack.value = 0.01;
  compressor.release.value = 0.2;

  const makeup = ctx.createGain();
  makeup.gain.value = Math.pow(10, (target - loudness) / 20);

  source.connect(highpass).connect(compressor).connect(makeup)
    .connect(ctx.destination);
  source.start();

  const rendered = await ctx.startRendering();

  // Подъём к цели мог вывести пики за шкалу — тогда весь микс тише ровно
  // настолько, насколько вылез пик. Клиппинг в мастеринге был бы издевательством:
  // за него как раз и платят, чтобы его не было.
  const peak = peakOf(rendered);
  if (peak > 1) {
    const gain = 1 / peak;
    for (let ch = 0; ch < rendered.numberOfChannels; ch += 1) {
      const data = rendered.getChannelData(ch);
      for (let i = 0; i < data.length; i += 1) data[i] *= gain;
    }
  }

  return rendered;
}
