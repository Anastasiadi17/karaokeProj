import type { ApiClient } from "../../api/client";
import { MAX_OFFSET_SEC, MIN_OFFSET_SEC } from "../../audio/latency";
import { useStudio } from "./useStudio";

export function StudioScreen({
  client,
  trackId,
}: {
  client: ApiClient;
  trackId: string;
}) {
  const s = useStudio(client, trackId);

  if (s.error) return <p role="alert">{s.error}</p>;
  if (!s.ready) return <p>Готовлю студию…</p>;

  return (
    <section>
      <h1>Студия</h1>

      <div>
        {s.recording ? (
          <button onClick={s.stopRecording}>Остановить</button>
        ) : (
          <button onClick={() => void s.startRecording()}>Записать</button>
        )}
        <button
          disabled={!s.hasTake || s.mixing || s.recording}
          onClick={() => void s.exportMix()}
        >
          {s.mixing ? "Свожу…" : "Скачать микс"}
        </button>
      </div>

      {/* Поправимая беда показывается рядом со студией, а не вместо неё:
          записанный дубль живёт в памяти вкладки, и снести экран из-за
          занятого микрофона значит потерять его. */}
      {s.notice && <p role="alert">{s.notice}</p>}

      {s.recording && (
        <div>
          <meter min={-60} max={0} value={Math.max(-60, s.levelDb)} />
          <span>{s.levelDb.toFixed(0)} dB</span>
        </div>
      )}

      {s.clipped && (
        <p role="alert">
          Сигнал перегружен — в записи будут щелчки. Отодвиньтесь от микрофона
          или убавьте усиление входа и запишите дубль заново.
        </p>
      )}

      <label>
        <input
          type="checkbox"
          checked={s.monitorOn}
          onChange={(e) => s.setMonitorOn(e.target.checked)}
        />
        Слышать себя в наушниках (добавляет 20–40 мс задержки)
      </label>

      <label>
        Смещение записи: {(s.offsetSec * 1000).toFixed(0)} мс
        <input
          type="range"
          min={MIN_OFFSET_SEC}
          max={MAX_OFFSET_SEC}
          step={0.005}
          value={s.offsetSec}
          onChange={(e) => s.setOffsetSec(Number(e.target.value))}
        />
      </label>

      <label>
        Голос
        <input
          type="range"
          min={0}
          max={2}
          step={0.05}
          value={s.voiceGain}
          onChange={(e) => s.setVoiceGain(Number(e.target.value))}
        />
      </label>

      <label>
        Минусовка
        <input
          type="range"
          min={0}
          max={2}
          step={0.05}
          value={s.musicGain}
          onChange={(e) => s.setMusicGain(Number(e.target.value))}
        />
      </label>

      <label>
        Реверб
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={s.reverbWet}
          onChange={(e) => s.setReverbWet(Number(e.target.value))}
        />
      </label>
    </section>
  );
}
