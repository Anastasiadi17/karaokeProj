import { useMemo, useState } from "react";
import { ApiClient } from "./api/client";
import { UploadScreen } from "./features/upload/UploadScreen";
import { ProcessingScreen } from "./features/processing/ProcessingScreen";
import { StudioScreen } from "./features/studio/StudioScreen";

export type AppStage = "upload" | "processing" | "studio";

export default function App() {
  const client = useMemo(() => new ApiClient(""), []);
  const [stage, setStage] = useState<AppStage>("upload");
  const [trackId, setTrackId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  if (stage === "upload") {
    return (
      <UploadScreen
        client={client}
        onUploaded={({ trackId: t, jobId: j }) => {
          setTrackId(t);
          setJobId(j);
          setStage("processing");
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
