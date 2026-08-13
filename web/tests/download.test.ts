import { describe, expect, it, vi } from "vitest";
import { downloadBlob } from "../src/audio/download";

function fakeDocument() {
  const anchor = {
    href: "",
    download: "",
    click: vi.fn(),
    remove: vi.fn(),
  };
  return {
    anchor,
    doc: {
      createElement: vi.fn(() => anchor),
      body: { appendChild: vi.fn() },
    } as unknown as Document,
  };
}

describe("downloadBlob", () => {
  it("ставит имя файла и кликает по ссылке", () => {
    const { anchor, doc } = fakeDocument();
    vi.stubGlobal("URL", {
      createObjectURL: () => "blob:fake",
      revokeObjectURL: vi.fn(),
    });

    downloadBlob(new Blob(["x"]), "mix.wav", doc);

    expect(anchor.download).toBe("mix.wav");
    expect(anchor.href).toBe("blob:fake");
    expect(anchor.click).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("освобождает объектный URL", () => {
    const { doc } = fakeDocument();
    const revoke = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: () => "blob:fake",
      revokeObjectURL: revoke,
    });

    downloadBlob(new Blob(["x"]), "mix.wav", doc);

    expect(revoke).toHaveBeenCalledWith("blob:fake");
    vi.unstubAllGlobals();
  });
});
