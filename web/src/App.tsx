import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient } from "./api/client";
import type { Me } from "./api/types";
import { AuthScreen } from "./features/auth/AuthScreen";
import { PricingScreen } from "./features/pricing/PricingScreen";
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
  const [showPricing, setShowPricing] = useState(false);
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

  if (showPricing) {
    return (
      <PricingScreen
        client={client}
        me={me}
        onBack={() => {
          setShowPricing(false);
          // Тариф мог смениться в другой вкладке, пока человек был у Stripe.
          void refreshMe();
        }}
      />
    );
  }

  if (stage === "upload") {
    return (
      <UploadScreen
        client={client}
        me={me}
        onShowPricing={() => setShowPricing(true)}
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

  return <StudioScreen client={client} trackId={trackId!} plan={me.plan} />;
}
