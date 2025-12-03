import { PDFDocument, StandardFonts, rgb, degrees } from 'pdf-lib';

export async function g3(contentFile, letterheadFile) {
  try {
    const [contentBuffer, letterheadBuffer] = await Promise.all([
      contentFile.arrayBuffer(),
      letterheadFile.arrayBuffer()
    ]);

    const contentDoc = await PDFDocument.load(contentBuffer);
    const letterheadDoc = await PDFDocument.load(letterheadBuffer);

    // Embed the first page of the letterhead
    const [letterheadPage] = await contentDoc.copyPages(letterheadDoc, [0]);
    const embeddedLetterhead = await contentDoc.embedPage(letterheadPage);

    const pageCount = contentDoc.getPageCount();
    const font = await contentDoc.embedFont(StandardFonts.Helvetica);

    for (let i = 0; i < pageCount; i++) {
      const page = contentDoc.getPage(i);
      const { width, height } = page.getSize();

      // Draw Letterhead (Background)
      page.drawPage(embeddedLetterhead, {
        x: 0,
        y: 0,
        width: width,
        height: height,
        blendMode: 'Multiply'
      });

      // Draw Page Number
      // Style: Golden Yellow (1, 0.843, 0), Rotated 90deg, Right Edge
      const text = "Página " + (i + 1);
      const textSize = 12;
      const textWidth = font.widthOfTextAtSize(text, textSize);

      // Position: x = width - 15 (margin from right), y = 20 (margin from bottom)
      // Rotation: 90 degrees
      page.drawText(text, {
        x: width - 15,
        y: 20,
        size: textSize,
        font: font,
        color: rgb(1, 0.843, 0), // Golden Yellow
        rotate: degrees(90)
      });
    }

    const pdfBytes = await contentDoc.save();
    return new Blob([pdfBytes], { type: "application/pdf" });

  } catch (error) {
    console.error("Erro na mesclagem:", error);
    throw new Error("Falha na mesclagem: " + error.message);
  }
}

export async function combinePDFs(files) {
  const pdfDoc = await PDFDocument.create();
  for (const file of files) {
    const arrayBuffer = await file.arrayBuffer();
    const srcDoc = await PDFDocument.load(arrayBuffer);
    const indices = srcDoc.getPageIndices();
    const copiedPages = await pdfDoc.copyPages(srcDoc, indices);
    copiedPages.forEach((page) => pdfDoc.addPage(page));
  }
  const pdfBytes = await pdfDoc.save();
  return new Blob([pdfBytes], { type: "application/pdf" });
}
