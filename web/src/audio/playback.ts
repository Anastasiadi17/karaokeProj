/**
 * Проигрывание готового буфера с ручкой остановки.
 *
 * Вынесено из хука студии, потому что здесь легко ошибиться дважды: забыть,
 * что `stop()` сам вызывает `onended`, и позвать обработчик конца дважды; и
 * наоборот — не позвать его вовсе, когда прослушивание оборвали руками.
 */
export interface Playback {
  /** Останавливает воспроизведение. Повторный вызов ничего не делает. */
  stop(): void;
}

export function playBuffer(
  ctx: AudioContext,
  buffer: AudioBuffer,
  onEnded?: () => void,
): Playback {
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);

  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    onEnded?.();
  };

  source.onended = finish;
  source.start();

  return {
    stop() {
      if (finished) return;
      try {
        source.stop();
      } catch {
        // Источник мог не успеть стартовать — тогда останавливать нечего.
      }
      finish();
    },
  };
}
