import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import { downloadBlob } from "../../audio/download";
import { encodeWav } from "../../audio/encode";
import {
  clampOffset,
  estimateLatencySec,
  loadOffset,
  saveOffset,
} from "../../audio/latency";
import { LevelMeter } from "../../audio/meter";
import { mixdown } from "../../audio/mixdown";
import { Monitor } from "../../audio/monitor";
import { playBuffer } from "../../audio/playback";
import type { Playback } from "../../audio/playback";
import { MIC_CONSTRAINTS, Recorder } from "../../audio/recorder";
import type { Samples } from "../../audio/samples";

/**
 * @param plan Тариф из сессии. От него зависит водяной знак в экспорте.
 *   Решение исполняет клиент, и подделать его через DevTools можно — это
 *   записано в дизайне подсистемы C как сознательно необеспеченная защита:
 *   сведение обязано остаться в браузере, иначе рушится юнит-экономика.
 */
export function useStudio(
  client: ApiClient,
  trackId: string,
  plan: "free" | "pro" = "free",
) {
  const ctxRef = useRef<AudioContext | null>(null);
  const musicRef = useRef<AudioBuffer | null>(null);
  const recorderRef = useRef<Recorder | null>(null);
  const monitorRef = useRef<Monitor | null>(null);
  const meterRef = useRef<LevelMeter | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playbackRef = useRef<AudioBufferSourceNode | null>(null);
  const takeRef = useRef<Samples[] | null>(null);
  const previewRef = useRef<Playback | null>(null);
  // Смещение, выставленное человеком или взятое из хранилища, оценка контекста
  // перебивать не имеет права.
  const offsetIsSetRef = useRef(false);
  // Доля реверба нужна и до создания монитора: узлы рождаются позже, чем
  // человек может подвинуть ползунок.
  const reverbWetRef = useRef(0.25);

  const [ready, setReady] = useState(false);
  const [recording, setRecording] = useState(false);
  const [mixing, setMixing] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [trackDeleted, setTrackDeleted] = useState(false);
  const [hasTake, setHasTake] = useState(false);
  /** Сбой подготовки: студии нет и не будет, показывать нечего. */
  const [error, setError] = useState<string | null>(null);
  /** Поправимая беда: студия на месте, дубль цел, можно попробовать снова. */
  const [notice, setNotice] = useState<string | null>(null);
  const [levelDb, setLevelDb] = useState(-120);
  const [clipped, setClipped] = useState(false);

  const [offsetSec, setOffsetSecState] = useState(0);
  const [voiceGain, setVoiceGain] = useState(1);
  const [musicGain, setMusicGain] = useState(0.8);
  const [reverbWet, setReverbWetState] = useState(0.25);
  const [monitorOn, setMonitorOnState] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const bytes = await client.fetchStem(trackId, "no_vocals");

      const probe = new AudioContext();
      const decoded = await probe.decodeAudioData(bytes.slice(0));
      await probe.close();
      if (cancelled) return;

      // Контекст создаётся в частоте трека: иначе рассинхрон копится по
      // длине песни и голос медленно уползает.
      const ctx = new AudioContext({ sampleRate: decoded.sampleRate });
      await ctx.audioWorklet.addModule("/recorder-worklet.js");
      if (cancelled) {
        // Ушли с экрана, пока грузился воркет. Незакрытый контекст остаётся
        // навсегда, а больше полудюжины их браузер не даёт создать.
        void ctx.close();
        return;
      }

      ctxRef.current = ctx;
      musicRef.current = decoded;
      recorderRef.current = new Recorder(ctx);
      monitorRef.current = new Monitor(ctx);
      meterRef.current = new LevelMeter(ctx);

      monitorRef.current.setWet(reverbWetRef.current);

      const stored = loadOffset(window.localStorage);
      if (stored !== null) {
        offsetIsSetRef.current = true;
        setOffsetSecState(stored);
      } else {
        setOffsetSecState(clampOffset(estimateLatencySec(ctx)));
      }
      setReady(true);
    })().catch((exc: unknown) => {
      if (cancelled) return;
      setError(
        exc instanceof ApiError
          ? "Минусовка больше недоступна: трек мог быть удалён по истечении " +
              "срока хранения. Загрузите файл заново."
          : exc instanceof Error
            ? exc.message
            : String(exc),
      );
    });

    return () => {
      cancelled = true;
      previewRef.current?.stop();
      previewRef.current = null;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      void ctxRef.current?.close();
    };
  }, [client, trackId]);

  const setOffsetSec = useCallback((sec: number) => {
    offsetIsSetRef.current = true;
    setOffsetSecState(sec);
    saveOffset(window.localStorage, sec);
  }, []);

  const setReverbWet = useCallback((value: number) => {
    // Наушники должны звучать так же, как будет звучать файл: иначе певец
    // подбирает эффект на слух и получает в экспорте другой.
    monitorRef.current?.setWet(value);
    reverbWetRef.current = value;
    setReverbWetState(value);
  }, []);

  const setMonitorOn = useCallback((on: boolean) => {
    monitorRef.current?.setEnabled(on);
    setMonitorOnState(on);
  }, []);

  const stopPreview = useCallback(() => {
    previewRef.current?.stop();
    previewRef.current = null;
  }, []);

  const stopRecording = useCallback(() => {
    // Идемпотентно: сюда приходят и нажатие «Остановить», и конец минусовки,
    // а `playback.stop()` из первого пути сам вызывает `onended`. Второй
    // проход забрал бы у записи уже пустые буферы и стёр дубль.
    if (!recorderRef.current?.isRecording) return;

    playbackRef.current?.stop();
    playbackRef.current = null;

    takeRef.current = recorderRef.current?.stop() ?? null;
    monitorRef.current?.detach();
    meterRef.current?.detach();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    setRecording(false);
    setHasTake((takeRef.current?.[0].length ?? 0) > 0);
  }, []);

  const startRecording = useCallback(async () => {
    const ctx = ctxRef.current;
    const music = musicRef.current;
    if (!ctx || !music || !recorderRef.current) return;

    try {
      // Контекст, созданный без участия человека, браузер держит в suspended:
      // ни воспроизведения, ни кадров с микрофона не будет. Здесь мы внутри
      // обработчика нажатия — единственное место, где его пускают.
      if (ctx.state !== "running") await ctx.resume();

      // Прослушивание и запись в одних наушниках несовместимы: старый микс
      // попал бы в новый дубль через микрофон.
      stopPreview();
      setPreviewing(false);

      // `outputLatency` до первого рендера равен нулю: контекст создаётся при
      // входе на экран, а поднимается только здесь. Оценка, снятая раньше,
      // давала единицы миллисекунд вместо настоящих сорока и выше.
      if (!offsetIsSetRef.current) {
        const estimated = clampOffset(estimateLatencySec(ctx));
        if (estimated > 0) setOffsetSecState(estimated);
      }

      setNotice(null);

      const stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
      streamRef.current = stream;

      const source = ctx.createMediaStreamSource(stream);
      monitorRef.current?.attach(source);
      meterRef.current?.resetClip();
      meterRef.current?.attach(source);
      setClipped(false);

      await recorderRef.current.start(stream);

      const playback = ctx.createBufferSource();
      playback.buffer = music;
      playback.connect(ctx.destination);
      playback.start();
      // Песня, доигравшая до конца, обязана закрыть дубль так же, как нажатие
      // «Остановить». Иначе воркет продолжает писать в никуда, дубль остаётся
      // не забранным, а кнопка экспорта — серой: человек спел всё и потерял
      // запись.
      playback.onended = () => stopRecording();
      playbackRef.current = playback;

      setRecording(true);
    } catch {
      // Не `error`: студия цела и предыдущий дубль тоже. Человек разрешает
      // микрофон или закрывает программу, которая его держит, и жмёт снова.
      setNotice(
        "Нет доступа к микрофону. Разрешите его в настройках браузера или " +
          "закройте программу, которая его заняла, и нажмите «Записать» снова.",
      );
    }
  }, [stopRecording, stopPreview]);

  /**
   * Сводит дубль текущими настройками.
   *
   * Одна и та же сборка идёт и в файл, и в прослушивание — иначе
   * прослушивание врало бы: человек подбирал бы смещение на одном звуке, а
   * скачивал другой. Водяной знак поэтому включён и здесь.
   */
  const buildMix = useCallback(async (): Promise<AudioBuffer | null> => {
    const music = musicRef.current;
    const take = takeRef.current;
    if (!music || !take) return null;

    return mixdown(music, take, music.sampleRate, {
      offsetSec,
      voiceGain,
      musicGain,
      reverbWet,
      watermark: plan !== "pro",
    });
  }, [offsetSec, voiceGain, musicGain, reverbWet, plan]);

  const previewMix = useCallback(async () => {
    const ctx = ctxRef.current;
    if (!ctx) return;

    if (previewRef.current) {
      stopPreview();
      setPreviewing(false);
      return;
    }

    setMixing(true);
    setNotice(null);
    try {
      // Тот же suspended, что и у записи: контекст поднимается только из
      // обработчика нажатия.
      if (ctx.state !== "running") await ctx.resume();

      const mixed = await buildMix();
      if (!mixed) return;

      setPreviewing(true);
      previewRef.current = playBuffer(ctx, mixed, () => {
        previewRef.current = null;
        setPreviewing(false);
      });
    } catch {
      setNotice(
        "Не удалось собрать микс для прослушивания. Попробуйте ещё раз.",
      );
    } finally {
      setMixing(false);
    }
  }, [buildMix, stopPreview]);

  /**
   * Убирает исходник и дорожки с сервера, не трогая студию.
   *
   * Минусовка уже раскодирована в память вкладки, а дубль там и жил, — то
   * есть после удаления можно спокойно допеть и скачать. Ради этого кнопка и
   * существует: до неё единственным способом убрать свою музыку с чужого
   * диска было дождаться уборки по сроку хранения.
   */
  const deleteTrack = useCallback(async () => {
    setNotice(null);
    try {
      await client.deleteTrack(trackId);
      setTrackDeleted(true);
    } catch {
      setNotice(
        "Не удалось удалить трек с сервера. Попробуйте ещё раз; если " +
          "не выходит, он всё равно исчезнет сам по истечении срока хранения.",
      );
    }
  }, [client, trackId]);

  const exportMix = useCallback(async () => {
    const music = musicRef.current;
    const take = takeRef.current;
    if (!music || !take) return;

    setMixing(true);
    setNotice(null);
    try {
      const mixed = await buildMix();
      if (!mixed) return;

      const channels = Array.from({ length: mixed.numberOfChannels }, (_, ch) =>
        Float32Array.from(mixed.getChannelData(ch)),
      );
      downloadBlob(encodeWav(channels, mixed.sampleRate), "karaoke-mix.wav");
    } catch {
      // Молчащая кнопка хуже ошибки: человек жмёт её снова и снова.
      setNotice(
        "Не удалось свести микс. Попробуйте ещё раз; если повторяется — " +
          "перезапишите дубль покороче.",
      );
    } finally {
      setMixing(false);
    }
  }, [buildMix]);

  // Уровень читается только во время записи: клиппинг надо показать сразу,
  // а не после сведения, когда дубль уже испорчен.
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => {
      const reading = meterRef.current?.read();
      if (!reading) return;
      setLevelDb(reading.db);
      setClipped(reading.clipped);
    }, 100);
    return () => window.clearInterval(timer);
  }, [recording]);

  useEffect(() => {
    if (!hasTake) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasTake]);

  return {
    ready,
    recording,
    mixing,
    previewing,
    trackDeleted,
    hasTake,
    error,
    notice,
    levelDb,
    clipped,
    offsetSec,
    setOffsetSec,
    voiceGain,
    setVoiceGain,
    musicGain,
    setMusicGain,
    reverbWet,
    setReverbWet,
    monitorOn,
    setMonitorOn,
    startRecording,
    stopRecording,
    previewMix,
    exportMix,
    deleteTrack,
  };
}
