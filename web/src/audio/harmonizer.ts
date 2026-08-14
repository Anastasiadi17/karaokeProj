/**
 * Бэк-вокал без генерации: копии голоса, сдвинутые по высоте.
 *
 * Это не AI и в интерфейсе так не называется. Терция и квинта, спетые тем же
 * голосом, звучат как подпевка, потому что тембр совпадает — и стоят они
 * ноль: ни модели, ни ключей, ни сервера.
 *
 * Сдвиг по высоте с сохранением длины делается в два шага: растянуть во
 * времени наложением зёрен (overlap-add) и прочитать результат с другой
 * скоростью. Оба шага — школьная арифметика над массивом; библиотека сюда не
 * тащится, потому что весь код ниже короче её подключения.
 *
 * Качество у такого сдвига честно среднее: на широких интервалах слышен
 * «хор роботов». Для терции и квинты этого достаточно, для октавы вниз —
 * уже нет, и потому диапазон ограничен.
 */

import type { Samples } from "./samples";

/** Дальше этих границ артефакты слышны сильнее, чем польза. */
export const MIN_SEMITONES = -7;
export const MAX_SEMITONES = 7;

const GRAIN = 2048;
const OVERLAP = GRAIN / 2;

/**
 * Насколько зерну позволено съехать от расчётного места, чтобы попасть в
 * фазу предыдущего. ±512 сэмплов — это период голоса от 86 Гц и выше, то
 * есть весь человеческий диапазон, включая низкий мужской.
 */
const SEARCH = 512;
/** Шаг перебора и длина сравнения: точность против времени счёта. */
const SEARCH_STEP = 8;
const CORRELATION = 256;

/**
 * Ищет, откуда взять зерно, чтобы оно продолжило уже написанное.
 *
 * Без этого зёрна кладутся вслепую, стыки рвут фазу, и накопленный разрыв
 * уводит высоту: замер на синусе 200 Гц давал терцию мимо на 15 центов, а
 * квинту вниз — на 113, то есть больше полутона. Одновременно с голосом это
 * слышно не как неточность, а как фальшь.
 */
function alignedStart(
  input: Samples,
  out: Samples,
  outPos: number,
  guess: number,
): number {
  const lo = Math.max(0, guess - SEARCH);
  const hi = Math.min(input.length - CORRELATION - 1, guess + SEARCH);
  if (hi <= lo || outPos + CORRELATION >= out.length) return guess;

  let bestStart = guess;
  let bestScore = -Infinity;

  for (let candidate = lo; candidate <= hi; candidate += SEARCH_STEP) {
    let dot = 0;
    let energy = 0;
    for (let i = 0; i < CORRELATION; i += 1) {
      const value = input[candidate + i];
      dot += value * out[outPos + i];
      energy += value * value;
    }
    // Нормировка на энергию кандидата: иначе перебор всегда выбирает самое
    // громкое место, а не самое похожее.
    const score = energy > 0 ? dot / Math.sqrt(energy) : 0;
    if (score > bestScore) {
      bestScore = score;
      bestStart = candidate;
    }
  }

  return bestStart;
}

function ratioOf(semitones: number): number {
  const clamped = Math.max(MIN_SEMITONES, Math.min(MAX_SEMITONES, semitones));
  return Math.pow(2, clamped / 12);
}

/** Растягивает сигнал в `factor` раз наложением зёрен с косинусным окном. */
export function timeStretch(input: Samples, factor: number): Samples {
  const outLength = Math.max(1, Math.round(input.length * factor));
  const out = new Float32Array(outLength);
  const window = new Float32Array(GRAIN);
  for (let i = 0; i < GRAIN; i += 1) {
    // Окно Ханна: на стыке зёрен сумма двух окон даёт единицу, поэтому
    // громкость не пульсирует.
    window[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / GRAIN);
  }

  const step = OVERLAP;
  const inStep = step / factor;

  for (let out_i = 0, in_pos = 0; out_i < outLength; out_i += step) {
    // Расчётное место остаётся расчётным: оно задаёт скорость растяжения.
    // Подвинуть разрешено только само зерно, и только чтобы попасть в фазу.
    const start = out_i === 0
      ? Math.round(in_pos)
      : alignedStart(input, out, out_i, Math.round(in_pos));
    for (let i = 0; i < GRAIN; i += 1) {
      const from = start + i;
      const to = out_i + i;
      if (from >= input.length || to >= outLength) break;
      out[to] += input[from] * window[i];
    }
    in_pos += inStep;
  }

  return out;
}

/** Читает сигнал с другой скоростью — меняет и высоту, и длину. */
export function resample(input: Samples, factor: number): Samples {
  const outLength = Math.max(1, Math.round(input.length / factor));
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i += 1) {
    const position = i * factor;
    const left = Math.floor(position);
    const right = Math.min(left + 1, input.length - 1);
    const fraction = position - left;
    out[i] = input[left] * (1 - fraction) + input[right] * fraction;
  }
  return out;
}

/**
 * Сдвигает высоту, сохраняя длину.
 *
 * Длина сохраняется точно, а не примерно: копия подмешивается к дублю, и
 * расхождение даже в сотню сэмплов слышно как расфазировка.
 */
export function pitchShift(input: Samples, semitones: number): Samples {
  const ratio = ratioOf(semitones);
  const stretched = timeStretch(input, ratio);
  const shifted = resample(stretched, ratio);

  const out = new Float32Array(input.length);
  out.set(shifted.subarray(0, Math.min(shifted.length, input.length)));
  return out;
}

export interface HarmonyVoice {
  semitones: number;
  gain: number;
  /** Сдвиг во времени в сэмплах: живые люди не попадают в унисон идеально. */
  delaySamples: number;
}

/** Терция и квинта сверху — самая безопасная подпевка. */
export const DEFAULT_VOICES: HarmonyVoice[] = [
  { semitones: 4, gain: 0.5, delaySamples: 220 },
  { semitones: 7, gain: 0.35, delaySamples: 480 },
];

/**
 * Собирает дорожку бэк-вокала из дубля.
 *
 * Голоса чуть расходятся по времени намеренно: идеальный унисон звучит как
 * один голос, ставший громче, а не как подпевка.
 */
export function makeHarmony(
  voice: Samples,
  voices: HarmonyVoice[] = DEFAULT_VOICES,
): Samples {
  const out = new Float32Array(voice.length);

  for (const { semitones, gain, delaySamples } of voices) {
    const shifted = pitchShift(voice, semitones);
    for (let i = delaySamples; i < out.length; i += 1) {
      out[i] += shifted[i - delaySamples] * gain;
    }
  }

  return out;
}
