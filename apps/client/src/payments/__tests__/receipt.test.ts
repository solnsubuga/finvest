import { formatDay, formatFileSize } from "../format";
import {
  checkReceipt,
  checkReceiptFile,
  daySentToday,
  groupDigits,
  kampalaToday,
  MAX_RECEIPT_BYTES,
  parseDaySent,
  parseShillings,
} from "../receipt";

const NOW = Date.parse("2026-11-13T12:00:00Z");

describe("checkReceipt", () => {
  it("accepts a JPG, a PNG and a PDF", () => {
    for (const [name, mimeType] of [
      ["slip.jpg", "image/jpeg"],
      ["screen.png", "image/png"],
      ["receipt.pdf", "application/pdf"],
    ] as const) {
      expect(checkReceipt({ uri: "file:///x", name, mimeType, size: 2048 })).toEqual({
        uri: "file:///x",
        name,
        mimeType,
        sizeBytes: 2048,
      });
    }
  });

  it("goes by the file name when the device does not say the type", () => {
    expect(checkReceipt({ uri: "u", name: "Slip.JPEG", size: 10 })).toMatchObject({
      mimeType: "image/jpeg",
    });
    expect(
      checkReceipt({ uri: "u", name: "r.pdf", mimeType: "application/octet-stream", size: 10 }),
    ).toMatchObject({ mimeType: "application/pdf" });
  });

  it("refuses other types, even with an accepted file name", () => {
    expect(checkReceipt({ uri: "u", name: "notes.docx", size: 10 })).toBe("wrong_type");
    expect(checkReceipt({ uri: "u", name: "clip.pdf", mimeType: "video/mp4", size: 10 })).toBe(
      "wrong_type",
    );
    expect(checkReceipt({ uri: "u", name: "noextension", size: 10 })).toBe("wrong_type");
  });

  it("refuses a file over 10 MB, an empty one, and one of unknown size", () => {
    const base = { uri: "u", name: "r.pdf", mimeType: "application/pdf" };
    expect(checkReceipt({ ...base, size: MAX_RECEIPT_BYTES })).toMatchObject({ name: "r.pdf" });
    expect(checkReceipt({ ...base, size: MAX_RECEIPT_BYTES + 1 })).toBe("too_large");
    expect(checkReceipt({ ...base, size: 0 })).toBe("empty");
    expect(checkReceipt({ ...base })).toBe("empty");
  });
});

describe("amounts", () => {
  it("reads whole shillings, with or without commas", () => {
    expect(parseShillings("2,000,000")).toBe(2_000_000);
    expect(parseShillings("50000")).toBe(50_000);
    expect(parseShillings(" 1 000 ")).toBe(1_000);
  });

  it("refuses anything that is not a positive whole number", () => {
    for (const text of ["", "0", "-5", "12.5", "1e6", "abc", "0100", "9999999999999"]) {
      expect(parseShillings(text)).toBeNull();
    }
  });

  it("groups digits as they are typed", () => {
    expect(groupDigits("2000000")).toBe("2,000,000");
    expect(groupDigits("2,000,0005")).toBe("20,000,005");
    expect(groupDigits("00120")).toBe("120");
    expect(groupDigits("12a")).toBe("12");
  });
});

describe("the day sent", () => {
  it("is today in Kampala, three hours ahead of UTC", () => {
    expect(kampalaToday(Date.parse("2026-11-13T20:59:00Z"))).toBe("2026-11-13");
    expect(kampalaToday(Date.parse("2026-11-13T21:00:00Z"))).toBe("2026-11-14");
  });

  it("starts the day sent as today in Kampala, written as the form reads it", () => {
    expect(daySentToday(Date.parse("2026-11-15T09:00:00Z"))).toBe("15/11/2026");
    expect(daySentToday(Date.parse("2026-11-30T21:30:00Z"))).toBe("01/12/2026");
    expect(parseDaySent(daySentToday(NOW), NOW)).toBe("2026-11-13");
  });

  it("reads day, month, year", () => {
    expect(parseDaySent("13/11/2026", NOW)).toBe("2026-11-13");
    expect(parseDaySent("3-11-2026", NOW)).toBe("2026-11-03");
    expect(parseDaySent(" 01.10.2026 ", NOW)).toBe("2026-10-01");
  });

  it("refuses a day that does not exist, is in the future or is too old", () => {
    for (const text of ["31/11/2026", "14/11/2026", "13/11/26", "2026-11-13", "13/13/2026", ""]) {
      expect(parseDaySent(text, NOW)).toBeNull();
    }
    expect(parseDaySent("15/08/2026", NOW)).toBe("2026-08-15");
    expect(parseDaySent("14/08/2026", NOW)).toBeNull();
  });
});

describe("formatting", () => {
  it("shows a file size and a day", () => {
    expect(formatFileSize(214 * 1024)).toBe("214 KB");
    expect(formatFileSize(100)).toBe("1 KB");
    expect(formatFileSize(1_500_000)).toBe("1.4 MB");
    expect(formatDay("2026-11-13")).toBe("13 Nov 2026");
  });
});

describe("checkReceiptFile", () => {
  const bytes = (...values: number[]) => async () => new Uint8Array(values);
  const jpg = { uri: "blob:1", name: "slip.jpg", mimeType: "image/jpeg", size: 2048 };

  it("takes a file that starts as its type does", async () => {
    expect(await checkReceiptFile(jpg, bytes(0xff, 0xd8, 0xff, 0xe0))).toMatchObject({ name: "slip.jpg" });
    expect(
      await checkReceiptFile({ ...jpg, name: "s.png", mimeType: "image/png" }, bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)),
    ).toMatchObject({ mimeType: "image/png" });
    expect(
      await checkReceiptFile({ ...jpg, name: "s.pdf", mimeType: "application/pdf" }, bytes(0x25, 0x50, 0x44, 0x46, 0x2d, 0x31)),
    ).toMatchObject({ mimeType: "application/pdf" });
  });

  it("refuses a text file renamed .jpg at once", async () => {
    const text = Array.from("hello there", (letter) => letter.charCodeAt(0));
    expect(await checkReceiptFile(jpg, bytes(...text))).toBe("not_what_it_says");
    expect(await checkReceiptFile(jpg, bytes())).toBe("not_what_it_says");
  });

  it("keeps the name and size checks when the file cannot be read", async () => {
    expect(await checkReceiptFile(jpg, async () => Promise.reject(new Error("no")))).toMatchObject({ name: "slip.jpg" });
    expect(await checkReceiptFile({ ...jpg, name: "notes.docx", mimeType: "" }, bytes(0xff, 0xd8, 0xff))).toBe("wrong_type");
  });
});
