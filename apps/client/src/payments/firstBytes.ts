/** The first few bytes of a file the device handed us, to tell what it really is. */
export async function readFirstBytes(uri: string, count = 8): Promise<Uint8Array> {
  const blob = await (await fetch(uri)).blob();
  return new Uint8Array(await blob.slice(0, count).arrayBuffer());
}
