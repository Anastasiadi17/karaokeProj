/**
 * Тип буфера сэмплов.
 *
 * С TypeScript 5.7 типизированные массивы параметризованы буфером, и голый
 * `Float32Array` означает `Float32Array<ArrayBufferLike>` — то есть «может
 * лежать и в SharedArrayBuffer». Web Audio такие не принимает:
 * `copyToChannel` и `getFloatTimeDomainData` требуют обычный `ArrayBuffer`.
 *
 * Всё, что рождается в этом модуле и может дойти до графа, объявляется как
 * `Samples`. Параметры, которые буфер только читают, могут оставаться широким
 * `Float32Array`.
 */
export type Samples = Float32Array<ArrayBuffer>;
