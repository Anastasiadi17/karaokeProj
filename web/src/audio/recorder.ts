/**
 * Захват голоса. Складывает сырые кадры воркета в непрерывный буфер.
 *
 * Никакой компенсации задержки здесь нет намеренно: сдвиг применяется при
 * сведении, чтобы его можно было поменять, не переписывая дубль.
 */

export const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: 2,
  },
  video: false,
};

export function concatChunks(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

export class Recorder {
  private readonly ctx: AudioContext;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private left: Float32Array[] = [];
  private right: Float32Array[] = [];

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
  }

  get isRecording(): boolean {
    return this.node !== null;
  }

  async start(stream: MediaStream): Promise<void> {
    this.left = [];
    this.right = [];

    this.node = new AudioWorkletNode(this.ctx, "recorder-processor");
    this.node.port.onmessage = (event: MessageEvent<Float32Array[]>) => {
      const channels = event.data;
      this.left.push(channels[0]);
      this.right.push(channels[1] ?? channels[0]);
    };

    this.source = this.ctx.createMediaStreamSource(stream);
    this.source.connect(this.node);

    // Воркет должен получать вызовы process — для этого узел подключается к
    // назначению через глушитель, иначе граф считается неактивным.
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    this.node.connect(mute).connect(this.ctx.destination);
  }

  stop(): Float32Array[] {
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
      this.source?.disconnect();
      this.node = null;
      this.source = null;
    }
    return [concatChunks(this.left), concatChunks(this.right)];
  }
}
