import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient } from "./api/client";
import type { Me } from "./api/types";
import { AuthScreen } from "./features/auth/AuthScreen";
import { UploadScreen } from "./features/upload/UploadScreen";
import { ProcessingScreen } from "./features/processing/ProcessingScreen";
import { StudioScreen } from "./features/studio/StudioScreen";

export type AppStage = "upload" | "processing" | "studio";

export default function App() {
  const client = useMemo(() => new ApiClient(""), []);
  const [stage, setStage] = useState<AppStage>("upload");
  const [trackId, setTrackId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const [me, setMe] = useState<Me | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);

  const refreshMe = useCallback(async () => {
    setMe(await client.me().catch(() => null));
  }, [client]);

  useEffect(() => {
    void refreshMe().finally(() => setCheckingSession(false));
  }, [refreshMe]);

  // Пока сессия не проверена, экран входа показывать нельзя: вошедший
  // человек увидел бы форму входа на долю секунды при каждой перезагрузке.
  if (checkingSession) return <p>Минуту…</p>;
  if (me === null) return <AuthScreen client={client} />;

  if (stage === "upload") {
    return (
      <UploadScreen
        client={client}
        me={me}
        onLogout={async () => {
          await client.logout();
          setMe(null);
        }}
        onUploaded={({ trackId: t, jobId: j }) => {
          setTrackId(t);
          setJobId(j);
          setStage("processing");
          // Счётчик операций изменился на сервере — забираем свежий.
          void refreshMe();
        }}
      />
    );
  }

  if (stage === "processing" && jobId) {
    return (
      <ProcessingScreen
        client={client}
        jobId={jobId}
        onReady={() => setStage("studio")}
      />
    );
  }

  return <StudioScreen client={client} trackId={trackId!} />;
}
