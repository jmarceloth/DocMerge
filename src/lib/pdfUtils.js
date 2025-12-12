import { PDFDocument, StandardFonts, rgb, degrees } from 'pdf-lib';

export async function g3(contentFile, letterheadFile) {
  try {
    const [contentBuffer, letterheadBuffer] = await Promise.all([
      contentFile.arrayBuffer(),
      letterheadFile.arrayBuffer()
    ]);

    const contentDoc = await PDFDocument.load(contentBuffer);
    const letterheadDoc = await PDFDocument.load(letterheadBuffer);

    // Create a new document for the output
    const newDoc = await PDFDocument.create();

    // Embed the letterhead page (background)
    const letterheadPage = letterheadDoc.getPage(0);
    const embeddedLh = await newDoc.embedPage(letterheadPage);
    const { width: lhWidth, height: lhHeight } = embeddedLh;

    const pageCount = contentDoc.getPageCount();
    const font = await newDoc.embedFont(StandardFonts.Helvetica);

    for (let i = 0; i < pageCount; i++) {
        const contentPage = contentDoc.getPage(i);
        const embeddedContent = await newDoc.embedPage(contentPage);

        // Create new page matching letterhead size
        const newPage = newDoc.addPage([lhWidth, lhHeight]);

        // 1. Draw Letterhead (Background)
        newPage.drawPage(embeddedLh, {
            x: 0,
            y: 0,
            width: lhWidth,
            height: lhHeight
        });

        // 2. Draw Content (Scaled and Centered)
        // Scale factor 0.85 reduces size to 85%, creating safe margins
        const scaleFactor = 0.85; 
        const newContentWidth = embeddedContent.width * scaleFactor;
        const newContentHeight = embeddedContent.height * scaleFactor;
        
        // Center the content on the page
        const xPos = (lhWidth - newContentWidth) / 2;
        const yPos = (lhHeight - newContentHeight) / 2;

        newPage.drawPage(embeddedContent, {
            x: xPos,
            y: yPos,
            width: newContentWidth,
            height: newContentHeight
        });

        // 3. Draw Page Number
        // Left original logic: Right edge, rotated 90deg
        const text = "Página " + (i + 1);
        const textSize = 12;
       
        newPage.drawText(text, {
            x: lhWidth - 15,
            y: 20,
            size: textSize,
            font: font,
            color: rgb(1, 0.843, 0), // Golden Yellow
            rotate: degrees(90)
        });
    }

    const pdfBytes = await newDoc.save();
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
