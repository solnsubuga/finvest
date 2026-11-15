import { PDFDocument, StandardFonts } from "pdf-lib";
import { describe, expect, it } from "vitest";
import { earlierVersions, legalDocuments, type LegalDocument, versionLabel } from "../data/legal.ts";
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
      stage: "draft",
      summary: "A long document.",
      sections: Array.from({ length: 40 }, (_, index) => ({
        heading: `Section ${index + 1}`,
        body: ["A paragraph written out at some length so it wraps.", { list: ["One point", "Another point"] }],
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

describe("wording", () => {
  it("writes every current document out in full, as a draft until the lawyers review it", () => {
    for (const document of legalDocuments) {
      expect(document.stage, document.slug).not.toBe("layout");
      if (document.stage === "reviewed") expect(document.reviewedBy?.trim(), document.slug).toBeTruthy();
      expect(document.summary.length, document.slug).toBeGreaterThanOrEqual(40);
      expect(document.summary.length, document.slug).toBeLessThanOrEqual(170);
      for (const section of document.sections) {
        expect(section.body.length, `${document.slug}: ${section.heading}`).toBeGreaterThan(0);
      }
    }
  });

  it("never states a tax rate or a placeholder", () => {
    const text = JSON.stringify(legalDocuments);
    expect(text).not.toMatch(/\d\s*%|per ?cent/i);
    expect(text).not.toMatch(/\[[A-Z][A-Z :,'.a-z]*[A-Z.]\]|\bTBC\b|\bTBD\b|\bTODO\b|null|undefined/);
  });

  it("keeps each earlier version of the terms and the privacy notice", () => {
    const current = new Map(legalDocuments.map((document) => [document.slug, document.version]));
    for (const earlier of earlierVersions) {
      expect(earlier.version, earlier.slug).toBeTruthy();
      expect(earlier.version, earlier.slug).not.toBe(current.get(earlier.slug));
    }
  });

  it("uses only characters the PDF font can draw", async () => {
    for (const document of [...legalDocuments, ...earlierVersions]) {
      await expect(legalPdf(document, "finvestug.com/legal/x")).resolves.toBeInstanceOf(Uint8Array);
    }
  });
});

describe("versions", () => {
  it("gives the documents a client agrees to a version, and only those", () => {
    const versioned = legalDocuments.filter((document) => document.version).map((document) => document.slug);
    expect(versioned).toEqual(["terms", "privacy"]);
    for (const document of legalDocuments) {
      if (document.version) expect(document.version).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });

  it("names a version by its day", () => {
    expect(versionLabel("2026-11-14")).toBe("14 November 2026");
    expect(versionLabel("2027-01-03")).toBe("3 January 2027");
  });
});
