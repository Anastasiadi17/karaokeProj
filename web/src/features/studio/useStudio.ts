import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../../api/client";
import { downloadBlob } from "../../audio/download";
import { encodeWav } from "../../audio/encode";
import {
  estimateLatencySec,
  loadOffset,
  saveOffset,
} from "../../audio/latency";
import { LevelMeter } from "../../audio/meter";
import { mixdown } from "../../audio/mixdown";
import { Monitor } from "../../audio/monitor";
import { MIC_CONSTRAINTS, Recorder } from "../../audio/recorder";
import type { Samples } from "../../audio/samples";

export function useStudio(client: ApiClient, trackId: string) {
  const ctxRef = useRef<AudioContext | null>(null);
  const musicRef = useRef<AudioBuffer | null>(null);
  const recorderRef = useRef<Recorder | null>(null);
  const monitorRef = useRef<Monitor | null>(null);
  const meterRef = useRef<LevelMeter | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playbackRef = useRef<AudioBufferSourceNode | null>(null);
  const takeRef = useRef<Samples[] | null>(null);

  const [ready, setReady] = useState(false);
  const [recording, setRecording] = useState(false);
  const [mixing, setMixing] = useState(false);
  const [hasTake, setHasTake] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [levelDb, setLevelDb] = useState(-120);
  const [clipped, setClipped] = useState(false);

  const [offsetSec, setOffsetSecState] = useState(0);
  const [voiceGain, setVoiceGain] = useState(1);
  const [musicGain, setMusicGain] = useState(0.8);
  const [reverbWet, setReverbWet] = useState(0.25);
  const [monitorOn, setMonitorOnState] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const response = await fetch(client.stemUrl(trackId, "no_vocals"));
      const bytes = await response.arrayBuffer();

      const probe = new AudioContext();
      const decoded = await probe.decodeAudioData(bytes.slice(0));
      await probe.close();
      if (cancelled) return;

      // Контекст создаётся в частоте трека: иначе рассинхрон копится по
      // длине песни и голос медленно уползает.
      const ctx = new AudioContext({ sampleRate: decoded.sampleRate });
      await ctx.audioWorklet.addModule("/recorder-worklet.js");

      ctxRef.current = ctx;
      musicRef.current = decoded;
      recorderRef.current = new Recorder(ctx);
      monitorRef.current = new Monitor(ctx);
      meterRef.current = new LevelMeter(ctx);

      const stored = loadOffset(window.localStorage);
      setOffsetSecState(stored || estimateLatencySec(ctx));
      setReady(true);
    })().catch((exc: unknown) => {
      if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
    });

    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      void ctxRef.current?.close();
    };
  }, [client, trackId]);

  const setOffsetSec = useCallback((sec: number) => {
    setOffsetSecState(sec);
    saveOffset(window.localStorage, sec);
  }, []);

  const setMonitorOn = useCallback((on: boolean) => {
    monitorRef.current?.setEnabled(on);
    setMonitorOnState(on);
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
      playback.onended = () => setRecording(false);
      playbackRef.current = playback;

      setRecording(true);
    } catch {
      setError(
        "Нет доступа к микрофону. Разрешите его в настройках браузера и " +
          "перезагрузите страницу.",
      );
    }
  }, []);

  const stopRecording = useCallback(() => {
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

  const exportMix = useCallback(async () => {
    const music = musicRef.current;
    const take = takeRef.current;
    if (!music || !take) return;

    setMixing(true);
    try {
      const mixed = await mixdown(music, take, music.sampleRate, {
        offsetSec,
        voiceGain,
        musicGain,
        reverbWet,
        watermark: true,
      });

      const channels = Array.from({ length: mixed.numberOfChannels }, (_, ch) =>
        Float32Array.from(mixed.getChannelData(ch)),
      );
      downloadBlob(encodeWav(channels, mixed.sampleRate), "karaoke-mix.wav");
    } finally {
      setMixing(false);
    }
  }, [offsetSec, voiceGain, musicGain, reverbWet]);

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
    hasTake,
    error,
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
    exportMix,
  };
}
