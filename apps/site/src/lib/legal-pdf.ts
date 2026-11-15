/**
 * Each legal page as a PDF, made at build time so "Download as PDF" is a plain link that works
 * without JavaScript. The PDF holds the same wording as the page, including the notice while the
 * wording is still a draft or a layout.
 */

import { colors } from "@finvest/tokens";
import { PDFDocument, type PDFFont, type PDFPage, rgb, StandardFonts } from "pdf-lib";
import { draftNotice, layoutOnlyNotice, type LegalDocument, versionLabel } from "../data/legal.ts";

// A4 in points.
const PAGE_WIDTH = 595.28;
const PAGE_HEIGHT = 841.89;
const MARGIN = 56;
const TEXT_WIDTH = PAGE_WIDTH - MARGIN * 2;
const FOOTER_SPACE = 24;

function colour(hex: string) {
  const value = Number.parseInt(hex.slice(1), 16);
  return rgb(((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255);
}

/** Splits text into lines that fit `width` at `size`, breaking only between words. */
export function wrap(text: string, font: PDFFont, size: number, width: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const next = line === "" ? word : `${line} ${word}`;
    if (line !== "" && font.widthOfTextAtSize(next, size) > width) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  if (line !== "") lines.push(line);
  return lines;
}

/** The PDF of one legal document. `address` is the page's address on the website. */
export async function legalPdf(document: LegalDocument, address: string): Promise<Uint8Array> {
  // No creation or change dates, so the same document always builds to the same bytes.
  const pdf = await PDFDocument.create({ updateMetadata: false });
  pdf.setTitle(`${document.title} | Finvest`);
  pdf.setAuthor("Finvest");
  pdf.setLanguage("en-UG");

  const regular = await pdf.embedFont(StandardFonts.Helvetica);
  const bold = await pdf.embedFont(StandardFonts.HelveticaBold);

  let page: PDFPage = pdf.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
  let y = PAGE_HEIGHT - MARGIN;

  /** Moves to a new page when `height` more points would run into the footer. */
  const room = (height: number) => {
    if (y - height < MARGIN + FOOTER_SPACE) {
      page = pdf.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
      y = PAGE_HEIGHT - MARGIN;
    }
  };

  const lines = (text: string, font: PDFFont, size: number, hex: string, leading: number) => {
    for (const line of wrap(text, font, size, TEXT_WIDTH)) {
      room(leading);
      y -= leading;
      page.drawText(line, { x: MARGIN, y: y + (leading - size) / 2, size, font, color: colour(hex) });
    }
  };

  // The logo: a black rounded square holding a white rising line that ends in a lime dot.
  const mark = 26;
  const scale = mark / 64;
  const top = y;
  page.drawSvgPath("M18 0H46A18 18 0 0 1 64 18V46A18 18 0 0 1 46 64H18A18 18 0 0 1 0 46V18A18 18 0 0 1 18 0Z", {
    x: MARGIN,
    y: top,
    scale,
    color: colour(colors.black),
  });
  page.drawSvgPath("M14 44L25 32L34 40L43 29", {
    x: MARGIN,
    y: top,
    scale,
    borderColor: colour(colors.white),
    borderWidth: 7,
    borderLineCap: 1,
  });
  page.drawCircle({
    x: MARGIN + 49 * scale,
    y: top - 20 * scale,
    size: 6.5 * scale,
    color: colour(colors.lime),
  });
  page.drawText("finvest", {
    x: MARGIN + mark + 8,
    y: top - mark + 7,
    size: 17,
    font: bold,
    color: colour(colors.ink),
  });
  y -= mark + 30;

  lines("LEGAL", bold, 9, colors.muted, 14);
  y -= 4;
  lines(document.title, bold, 24, colors.ink, 30);
  if (document.version) {
    y -= 2;
    lines(`Version of ${versionLabel(document.version)}`, regular, 11, colors.muted, 16);
  }
  y -= 16;

  // The notice that the wording is still to come, in the same warm box as on the page.
  const notice =
    document.stage === "layout"
      ? { lead: "Layout only.", text: layoutOnlyNotice }
      : document.stage === "draft"
        ? { lead: "Draft for legal review.", text: draftNotice }
        : null;
  if (notice) {
    const noticeLines = wrap(`${notice.lead} ${notice.text}`, regular, 10, TEXT_WIDTH - 28);
    const noticeHeight = noticeLines.length * 15 + 20;
    room(noticeHeight);
    page.drawRectangle({
      x: MARGIN,
      y: y - noticeHeight,
      width: TEXT_WIDTH,
      height: noticeHeight,
      color: colour(colors.warningSurface),
    });
    let noticeY = y - 10;
    noticeLines.forEach((line, index) => {
      noticeY -= 15;
      if (index === 0 && line.startsWith(notice.lead)) {
        const options = { y: noticeY + 3, size: 10, color: colour(colors.warningText) };
        page.drawText(notice.lead, { ...options, x: MARGIN + 14, font: bold });
        page.drawText(line.slice(notice.lead.length), {
          ...options,
          x: MARGIN + 14 + bold.widthOfTextAtSize(notice.lead, 10),
          font: regular,
        });
      } else {
        page.drawText(line, {
          x: MARGIN + 14,
          y: noticeY + 3,
          size: 10,
          font: regular,
          color: colour(colors.warningText),
        });
      }
    });
    y -= noticeHeight + 8;
  }

  // A layout keeps its earlier grey text, so its PDF stays as it was. Written wording is in ink.
  const bodyColour = document.stage === "layout" ? colors.muted : colors.ink;

  /** A list item: a bullet, then its text wrapped beside it. */
  const bullet = (text: string) => {
    const indent = 14;
    wrap(text, regular, 11, TEXT_WIDTH - indent).forEach((line, index) => {
      room(17);
      y -= 17;
      const baseline = y + (17 - 11) / 2;
      if (index === 0) {
        page.drawText("\u2022", { x: MARGIN + 2, y: baseline, size: 11, font: regular, color: colour(bodyColour) });
      }
      page.drawText(line, { x: MARGIN + indent, y: baseline, size: 11, font: regular, color: colour(bodyColour) });
    });
  };

  document.sections.forEach((section, index) => {
    // Keep a heading with the first line of its text.
    room(12 + 22 + 17);
    y -= 12;
    lines(`${index + 1}. ${section.heading}`, bold, 13, colors.ink, 22);
    section.body.forEach((block, blockIndex) => {
      if (blockIndex > 0) y -= 6;
      if (typeof block === "string") {
        lines(block, regular, 11, bodyColour, 17);
      } else {
        block.list.forEach((item, itemIndex) => {
          if (itemIndex > 0) y -= 3;
          bullet(item);
        });
      }
    });
  });

  const pages = pdf.getPages();
  pages.forEach((each, index) => {
    const size = 9;
    const footerY = MARGIN - 10;
    each.drawText(address, { x: MARGIN, y: footerY, size, font: regular, color: colour(colors.muted) });
    const count = `Page ${index + 1} of ${pages.length}`;
    each.drawText(count, {
      x: PAGE_WIDTH - MARGIN - regular.widthOfTextAtSize(count, size),
      y: footerY,
      size,
      font: regular,
      color: colour(colors.muted),
    });
  });

  return pdf.save();
}
