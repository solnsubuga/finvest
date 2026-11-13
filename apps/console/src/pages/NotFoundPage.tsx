import { Link } from "react-router";
import { PageHeader } from "./PageHeader.tsx";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" summary="There is no console screen at this address." />
      <Link to="/" className="self-start text-md font-bold text-ink underline">
        Back to the console
      </Link>
    </>
  );
}
