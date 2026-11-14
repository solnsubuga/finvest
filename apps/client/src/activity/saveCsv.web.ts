/** In a browser: hand the CSV to the browser as a download under its own name. Nothing is kept by the page. */
export async function saveCsv(fileName: string, csv: string): Promise<void> {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    // Give the browser a moment to start the download before the blob goes.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
