/**
 * Захват сырых кадров с микрофона.
 *
 * MediaRecorder здесь не годится: он отдаёт сжатый webm с неопределённым
 * таймингом, а для выравнивания нужен точный счёт сэмплов.
 */
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input.length > 0 && input[0].length > 0) {
      this.port.postMessage(input.map((channel) => new Float32Array(channel)));
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
