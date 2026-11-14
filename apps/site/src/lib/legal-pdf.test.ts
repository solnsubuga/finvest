import { PDFDocument, StandardFonts } from "pdf-lib";
import { describe, expect, it } from "vitest";
import { legalDocuments, type LegalDocument } from "../data/legal.ts";
import { legalPdf, wrap } from "./legal-pdf.ts";

describe("legal PDFs", () => {
  it("builds every legal document, titled and on A4", async () => {
    for (const document of legalDocuments) {
      const bytes = await legalPdf(document, `finvestug.com/legal/${document.slug}`);
      const pdf = await PDFDocument.load(bytes);
      expect(pdf.getTitle(), document.slug).toBe(`${document.title} | Finvest`);
      expect(pdf.getPageCount(), document.slug).toBeGreaterThan(0);
      const { width, height } = pdf.getPage(0).getSize();
      expect([Math.round(width), Math.round(height)]).toEqual([595, 842]);
    }
  });

  it("builds the same bytes every time", async () => {
    const [first] = legalDocuments;
    if (!first) throw new Error("no legal documents");
    const one = await legalPdf(first, "finvestug.com/legal/terms");
    const two = await legalPdf(first, "finvestug.com/legal/terms");
    expect(Buffer.from(one).equals(Buffer.from(two))).toBe(true);
  });

  it("runs onto a second page when a document is long", async () => {
    const long: LegalDocument = {
      slug: "long",
      title: "Long",
      sections: Array.from({ length: 40 }, (_, index) => ({
        heading: `Section ${index + 1}`,
        covers: "What this section will cover, written out at some length so it wraps.",
      })),
    };
    const pdf = await PDFDocument.load(await legalPdf(long, "finvestug.com/legal/long"));
    expect(pdf.getPageCount()).toBeGreaterThan(1);
  });

  it("wraps between words and never past the width", async () => {
    const font = await (await PDFDocument.create()).embedFont(StandardFonts.Helvetica);
    const text = "The wording must come from Finvest's lawyers before launch.";
    const lines = wrap(text, font, 11, 120);
    expect(lines.length).toBeGreaterThan(1);
    expect(lines.join(" ")).toBe(text);
    for (const line of lines) expect(font.widthOfTextAtSize(line, 11)).toBeLessThanOrEqual(120);
  });
});
