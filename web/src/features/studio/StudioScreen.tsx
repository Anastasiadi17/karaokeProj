import type { ApiClient } from "../../api/client";
import { MAX_OFFSET_SEC, MIN_OFFSET_SEC } from "../../audio/latency";
import { useStudio } from "./useStudio";

export function StudioScreen({
  client,
  trackId,
  plan,
}: {
  client: ApiClient;
  trackId: string;
  plan: "free" | "pro";
}) {
  const s = useStudio(client, trackId, plan);

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
          onClick={() => void s.previewMix()}
        >
          {s.previewing ? "Остановить прослушивание" : "Прослушать"}
        </button>
        <button
          disabled={!s.hasTake || s.mixing || s.recording}
          onClick={() => void s.exportMix()}
        >
          {s.mixing ? "Свожу…" : "Скачать микс"}
        </button>
      </div>

      <p>
        {s.mastering ? (
          "Улучшение звучания включено: громче, ровнее, без гула. Оно попадёт " +
          "и в прослушивание, и в файл."
        ) : (
          <button
            disabled={s.recording || s.mixing}
            onClick={() => void s.enableMastering()}
          >
            Улучшить звучание (1 кредит)
          </button>
        )}
      </p>

      {plan === "free" && (
        <p>
          В бесплатном экспорте есть короткий сигнал каждые полминуты. На Pro
          его нет.
        </p>
      )}

      {/* Поправимая беда показывается рядом со студией, а не вместо неё:
          записанный дубль живёт в памяти вкладки, и снести экран из-за
          занятого микрофона значит потерять его. */}
      {s.takeRestored && (
        <p>
          Восстановлен дубль из прошлого захода — можно слушать и скачивать.
          Новая запись его заменит.
        </p>
      )}

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
        {/* Смещение подбирается на слух, поэтому рядом с ползунком должно
            быть чем послушать: «Прослушать» собирает ровно тот микс, который
            уйдёт в файл. */}
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

      <p>
        <button
          disabled={s.recording || s.calibrating}
          onClick={() => void s.calibrate()}
        >
          {s.calibrating ? "Слушаю щелчок…" : "Измерить задержку"}
        </button>{" "}
        Понадобятся динамики: в наушниках микрофон щелчка не услышит.
      </p>

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

      <p>
        {s.trackDeleted ? (
          "Трек удалён с сервера. Минусовка осталась в этой вкладке: допеть и " +
          "скачать микс можно, но после перезагрузки страницы её придётся " +
          "загружать заново."
        ) : (
          <button
            disabled={s.recording || s.mixing}
            onClick={() => void s.deleteTrack()}
          >
            Удалить трек с сервера
          </button>
        )}
      </p>

      <label>
        {/* Подпевка — те же копии голоса, сдвинутые по высоте; на нуле её
            нет вовсе и считать её не нужно. */}
        Подпевка
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={s.harmonyGain}
          onChange={(e) => s.setHarmonyGain(Number(e.target.value))}
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
