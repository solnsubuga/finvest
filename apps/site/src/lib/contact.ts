/** The email the contact form opens in the visitor's own email app. Nothing is sent by the site. */
export function mailtoLink(to: string, name: string, replyTo: string, message: string): string {
  const clean = (text: string) => text.replace(/\r\n?/g, "\n").trim();
  const subject = `Website message from ${clean(name).replace(/\n/g, " ")}`;
  const body = `${clean(message)}\n\nName: ${clean(name)}\nReply to: ${clean(replyTo)}`;
  return `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
